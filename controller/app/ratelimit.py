from __future__ import annotations

import asyncio
from typing import Dict, List, Any
import yaml

import structlog

from app.models import (
    AttackType, TargetSpec
)

logger = structlog.get_logger(__name__)

# =============================================================================
# 禁用 PyYAML 的 UnsafeLoader 警告 (我们只用 SafeLoader)
# =============================================================================
yaml.warnings({"YAMLLoadWarning": False})


class QuotaExhaustedError(Exception):
    """配额耗尽异常 (全局/节点任一维度)"""

class TargetValidator:
    """目标验证器

    v1.5.0 安全加固: 恢复 CIDR 白名单校验 (默认开启, 与 README 表述"无技术强制"对齐为
    "默认白名单 + 显式 opt-out")。is_allowed 接受:
      - IP 字符串 (IPv4/IPv6) — 单地址匹配
      - CIDR (如 10.100.0.0/16) — 子网匹配
      - 域名 — 通过 getaddrinfo 解析后任一 A 记录落在白名单即放行 (一次解析, 不缓存)
      - 场景占位符 (TARGET_*_PLACEHOLDER) — 一律拒绝 (加载期合法, 执行前必须被覆盖)

    ALLOW_ANY_TARGET=true 时绕过白名单 (v1.3.0 行为, 仅供受控教学与单节点测试使用)。
    白名单仅控制 IP 维度; 端口/协议不受白名单约束 (UDP 反射等场景需任意端口)。
    """

    def __init__(self, allowed_cidrs: List[str], allow_any: bool = False):
        self.allow_any = allow_any
        self._networks: list = []
        # 解析阶段延迟到第一次 is_allowed, 避免冷启动阶段 import 报错
        self._raw_cidrs = [c.strip() for c in (allowed_cidrs or []) if c and c.strip()]

    def _ensure_parsed(self) -> None:
        """惰性解析 CIDR 列表 (构造时可能未配置, 此时保持空)"""
        if self._networks or not self._raw_cidrs:
            return
        import ipaddress
        for cidr in self._raw_cidrs:
            try:
                self._networks.append(ipaddress.ip_network(cidr, strict=False))
            except ValueError as e:
                logger.warning("invalid_cidr_ignored", cidr=cidr, error=str(e))

    def _ip_in_cidrs(self, ip_str: str) -> bool:
        import ipaddress
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        return any(ip in net for net in self._networks)

    def _target_in_cidrs(self, host: str) -> bool:
        """目标 (IP / CIDR / 域名) 与白名单任一 CIDR 有交集即放行

        - 数值 IP: 任一 CIDR 包含即放行
        - CIDR 目标: 与任一白名单 CIDR 有重叠即放行 (含子集、超集、部分重叠)
        - 其它 (域名) 返回 False, 由调用方走 DNS 解析分支
        """
        import ipaddress
        # 1) IP
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return any(ip in net for net in self._networks)

        # 2) CIDR
        try:
            target_net = ipaddress.ip_network(host, strict=False)
        except ValueError:
            return False
        return any(target_net.overlaps(net) for net in self._networks)

    async def is_allowed(self, target: TargetSpec) -> bool:
        # 占位符永远拒绝 (执行前必须被 overrides 替换)
        if getattr(target, "is_placeholder", lambda: False)():
            return False
        if self.allow_any:
            return True
        self._ensure_parsed()
        if not self._networks:
            # 未配置白名单且未 opt-out: 拒绝 (fail-closed)
            # 调用方需显式设置 ALLOW_ANY_TARGET=true 或 ALLOWED_TARGET_CIDRS=...
            return False
        host = target.ip
        # 1) IP/CIDR 目标: 直接匹配 (含 IP 落 CIDR 与 CIDR 与白名单重叠)
        if self._target_in_cidrs(host):
            return True
        # 2) 域名目标: 解析为 IP 后任一 A 记录落在白名单即放行
        #    (单次解析, 异步, 不缓存 — 防止 DNS rebinding 风险)
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            infos = await loop.getaddrinfo(host, None, family=0, type=0)
            for info in infos:
                sockaddr = info[4]
                resolved_ip = sockaddr[0]
                if self._ip_in_cidrs(resolved_ip):
                    return True
            return False
        except (OSError, asyncio.CancelledError):
            # 解析失败: 默认拒绝 (防止任意域名白嫖白名单)
            return False

    def is_allowed_sync(self, target: TargetSpec) -> bool:
        """同步版本 (用于已经解析过的 IP, 跳过 DNS 解析)

        调用方保证 target.ip 已经是数值 IP (如攻击节点 _resolve_host 之后)。
        """
        if getattr(target, "is_placeholder", lambda: False)():
            return False
        if self.allow_any:
            return True
        self._ensure_parsed()
        if not self._networks:
            return False
        return self._target_in_cidrs(target.ip)


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


