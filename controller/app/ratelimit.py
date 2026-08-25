from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from collections import defaultdict
from pathlib import Path
import yaml

import structlog

from app.models import (
    AttackType, AttackCommand, AttackParams, AttackResult, AttackStatus,
    NodeInfo, NodeStatus, NodeHeartbeat, Scenario, ScenarioStep,
    EmergencyStopCommand, TargetSpec
)
from app.audit import audit_logger
from app.node_commander import node_commander

logger = structlog.get_logger(__name__)

# =============================================================================
# 禁用 PyYAML 的 UnsafeLoader 警告 (我们只用 SafeLoader)
# =============================================================================
yaml.warnings({"YAMLLoadWarning": False})


class QuotaExhaustedError(Exception):
    """配额耗尽异常 (全局/节点任一维度)"""

class TargetValidator:
    """目标验证器

    v1.3.0 方案A: 目标域名/IP 不做任何限制 — 保留类以兼容既有装配与测试,
    is_allowed 恒真; 仅拒绝场景模板占位符 (加载期合法、执行前必须被覆盖)。
    """

    def __init__(self, allowed_cidrs: List[str]):
        # 兼容旧签名; 白名单参数不再生效
        self.allowed_networks = []

    def is_allowed(self, target: TargetSpec) -> bool:
        if getattr(target, "is_placeholder", lambda: False)():
            return False
        return True


class RateLimiter:
    """分布式配额管理器 (Controller 侧聚合)

    P1-2 修复: 配额按 (attack_id, node_id) 二元组记账,
    同一节点并发多场攻击互不覆盖, 停止单场攻击只回收该场配额。
    """

    def __init__(self, global_rps: int, global_pps: int, global_concurrent: int):
        self.global_rps = global_rps
        self.global_pps = global_pps
        self.global_concurrent = global_concurrent
        self._quotas: Dict[Any, Dict[str, int]] = {}  # (attack_id, node_id) -> quota
        self._lock = asyncio.Lock()

    async def allocate(
        self,
        attack_id: str,
        node_id: str,
        attack_type: AttackType,
        requested_rps: int,
        requested_concurrent: int,
    ) -> Dict[str, int]:
        async with self._lock:
            used_rps = sum(q["rps"] for q in self._quotas.values())
            used_pps = sum(q["pps"] for q in self._quotas.values())
            used_concurrent = sum(q["concurrent"] for q in self._quotas.values())

            remaining_rps = max(0, self.global_rps - used_rps)
            remaining_pps = max(0, self.global_pps - used_pps)
            remaining_concurrent = max(0, self.global_concurrent - used_concurrent)

            if attack_type in (AttackType.SYN_FLOOD, AttackType.UDP_FLOOD, AttackType.UDP_REFLECTION):
                allowed_pps = min(requested_rps, remaining_pps)
                allowed_rps = 0
            else:
                allowed_rps = min(requested_rps, remaining_rps)
                allowed_pps = 0

            allowed_concurrent = min(requested_concurrent, remaining_concurrent)

            if allowed_rps == 0 and allowed_pps == 0:
                raise QuotaExhaustedError("Global rate limit exceeded")

            quota = {"rps": allowed_rps, "pps": allowed_pps, "concurrent": allowed_concurrent}
            self._quotas[(attack_id, node_id)] = quota
            return quota

    async def release_attack_node(self, attack_id: str, node_id: str):
        """回收单场攻击在单节点上的配额"""
        async with self._lock:
            self._quotas.pop((attack_id, node_id), None)

    async def release_attack(self, attack_id: str):
        """回收整场攻击的全部配额 (正常停止路径)"""
        async with self._lock:
            for key in [k for k in self._quotas if k[0] == attack_id]:
                self._quotas.pop(key, None)

    async def release_nodes(self, node_ids: List[str]):
        """回收指定节点的全部配额 (节点下线路径)"""
        nodes = set(node_ids)
        async with self._lock:
            for key in [k for k in self._quotas if k[1] in nodes]:
                self._quotas.pop(key, None)

    async def release_all(self):
        """清空全部配额 (紧急熔断/停机路径 — 防泄漏)"""
        async with self._lock:
            self._quotas.clear()

    def get_usage(self) -> Dict[str, Any]:
        total_rps = sum(q["rps"] for q in self._quotas.values())
        total_pps = sum(q["pps"] for q in self._quotas.values())
        total_concurrent = sum(q["concurrent"] for q in self._quotas.values())
        return {
            "global_rps": self.global_rps,
            "global_pps": self.global_pps,
            "global_concurrent": self.global_concurrent,
            "used_rps": total_rps,
            "used_pps": total_pps,
            "used_concurrent": total_concurrent,
            "quotas": [
                {"attack_id": k[0], "node_id": k[1], **v}
                for k, v in self._quotas.items()
            ],
        }


