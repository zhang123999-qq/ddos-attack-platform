from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any

import structlog

from app.ratelimit import QuotaExhaustedError
from app.models import (
    AttackType, AttackCommand, AttackParams, AttackResult, AttackStatus,
    NodeInfo, NodeStatus, NodeHeartbeat, EmergencyStopCommand, TargetSpec
)
from app.audit import audit_logger
from app.node_commander import node_commander

logger = structlog.get_logger(__name__)


class NodeRegistry:
    """节点注册表"""

    def __init__(self):
        self._nodes: Dict[str, NodeInfo] = {}
        self._heartbeats: Dict[str, NodeHeartbeat] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None

    async def start(self):
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop(self):
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

    async def register(self, node: NodeInfo) -> NodeInfo:
        async with self._lock:
            node.status = NodeStatus.ONLINE
            node.last_heartbeat = datetime.now(timezone.utc)
            self._nodes[node.node_id] = node
        await audit_logger.log_node_register(node)
        logger.info("node_registered", node_id=node.node_id, type=node.node_type)
        return node

    async def heartbeat(self, hb: NodeHeartbeat):
        async with self._lock:
            if hb.node_id in self._nodes:
                self._nodes[hb.node_id].last_heartbeat = hb.timestamp
                self._nodes[hb.node_id].status = hb.status
            self._heartbeats[hb.node_id] = hb

    async def unregister(self, node_id: str):
        async with self._lock:
            if node_id in self._nodes:
                self._nodes[node_id].status = NodeStatus.OFFLINE
        node_commander.unregister_node(node_id)
        logger.info("node_unregistered", node_id=node_id)

    def get_node(self, node_id: str) -> Optional[NodeInfo]:
        return self._nodes.get(node_id)

    def get_nodes_by_type(self, attack_type: AttackType) -> List[NodeInfo]:
        nodes = []
        for node in self._nodes.values():
            if node.status == NodeStatus.ONLINE and attack_type in node.supported_attacks:
                nodes.append(node)
        return nodes

    def get_nodes_by_labels(self, labels: Dict[str, str]) -> List[NodeInfo]:
        if not labels:
            return [n for n in self._nodes.values() if n.status == NodeStatus.ONLINE]
        result = []
        for node in self._nodes.values():
            if node.status != NodeStatus.ONLINE:
                continue
            if all(node.labels.get(k) == v for k, v in labels.items()):
                result.append(node)
        return result

    def get_all_online(self) -> List[NodeInfo]:
        return [n for n in self._nodes.values() if n.status == NodeStatus.ONLINE]

    async def _cleanup_loop(self):
        while True:
            await asyncio.sleep(30)
            now = datetime.now(timezone.utc)
            async with self._lock:
                stale = [
                    nid for nid, node in self._nodes.items()
                    if node.last_heartbeat and (now - node.last_heartbeat) > timedelta(seconds=90)
                ]
                for nid in stale:
                    self._nodes[nid].status = NodeStatus.OFFLINE
                    logger.warning("node_stale", node_id=nid)


class AttackExecutor:
    """攻击执行器 - CRIT-1 修复后通过 NodeCommander 真正下发指令"""

    def __init__(self, node_registry: NodeRegistry, rate_limiter: RateLimiter, target_validator: TargetValidator):
        self.node_registry = node_registry
        self.rate_limiter = rate_limiter
        self.target_validator = target_validator
        self._active_attacks: Dict[str, AttackCommand] = {}
        self._attack_results: Dict[str, Dict[str, AttackResult]] = defaultdict(dict)
        self._attack_node_map: Dict[str, List[str]] = {}  # attack_id → [node_ids]
        self._emergency_stop = asyncio.Event()
        self._lock = asyncio.Lock()
        self._bg_tasks: set = set()  # 持有 fire-and-forget 任务引用, 防止被 GC

    async def execute_attack(self, command: AttackCommand) -> Dict[str, Any]:
        """执行攻击指令"""
        # 1. 验证目标
        if not self.target_validator.is_allowed(command.params.target):
            await audit_logger.log_target_validation_failure(
                "controller", str(command.params.target.ip), "Target not in allowlist"
            )
            raise ValueError(f"Target {command.params.target.ip} not in allowed CIDRs")

        # 2. 选择目标节点
        if command.node_ids:
            target_nodes = [self.node_registry.get_node(nid) for nid in command.node_ids]
            target_nodes = [n for n in target_nodes if n is not None]
        else:
            target_nodes = self.node_registry.get_nodes_by_type(command.attack_type)

        if not target_nodes:
            raise ValueError(f"No available nodes for attack type {command.attack_type.value}")

        # 3. 检查熔断状态
        if self._emergency_stop.is_set():
            raise RuntimeError("Emergency stop is active")

        # 4. 分配配额 (attack_id 先于配额生成, 便于按场记账与回收)
        attack_id = command.attack_id or f"atk-{uuid.uuid4().hex[:12]}"
        command.attack_id = attack_id

        per_node_rps = command.params.rps
        per_node_concurrent = command.params.concurrency
        allocated: Dict[str, Dict[str, int]] = {}
        try:
            for node in target_nodes:
                allocated[node.node_id] = await self.rate_limiter.allocate(
                    attack_id, node.node_id, command.attack_type, per_node_rps, per_node_concurrent
                )
        except QuotaExhaustedError:
            # 中途耗尽: 回滚已分配节点, 不影响其他攻击的既有配额
            for nid in list(allocated):
                await self.rate_limiter.release_attack_node(attack_id, nid)
            raise

        async with self._lock:
            self._active_attacks[attack_id] = command
            self._attack_results[attack_id] = {}
            self._attack_node_map[attack_id] = [n.node_id for n in target_nodes]

        await audit_logger.log_attack_start("controller", command, [n.node_id for n in target_nodes])

        # 5. 并行下发指令给所有节点 (逐节点发送, 失败节点立即回收配额)
        send_results = []
        for node in target_nodes:
            quota = allocated[node.node_id]
            node_params = command.params.model_copy()
            if command.attack_type in (AttackType.SYN_FLOOD, AttackType.UDP_FLOOD, AttackType.UDP_REFLECTION):
                node_params.rps = quota["pps"]
            else:
                node_params.rps = quota["rps"]
            node_params.concurrency = quota["concurrent"]

            node_command_dict = {
                "attack_id": attack_id,
                "attack_type": command.attack_type.value,
                "params": node_params.model_dump(mode='json'),
                "scenario_id": command.scenario_id,
                "node_ids": [node.node_id],
                "priority": command.priority,
            }

            success = await node_commander.send_attack_command(node.node_id, node_command_dict)
            if not success:
                # 部分失败: 该节点配额立即回收, 不留孤儿占用量
                await self.rate_limiter.release_attack_node(attack_id, node.node_id)
            send_results.append({"node_id": node.node_id, "success": success})

        if not any(r["success"] for r in send_results):
            # 全部失败: 回收注册表中的孤儿攻击记录 (配额已在逐节点失败时回收)
            async with self._lock:
                self._active_attacks.pop(attack_id, None)
                self._attack_node_map.pop(attack_id, None)
                self._attack_results.pop(attack_id, None)
            raise RuntimeError("Failed to deliver command to any node")

        return {
            "attack_id": attack_id,
            "target_nodes": [n.node_id for n in target_nodes],
            "allocated": allocated,
            "send_results": send_results,
        }

    async def stop_attack(self, attack_id: str, reason: str = "manual"):
        """停止攻击"""
        async with self._lock:
            command = self._active_attacks.get(attack_id)
            if not command:
                return {"stopped": False, "reason": "attack not found"}

            target_node_ids = self._attack_node_map.get(attack_id, [])

        # 并行发送停止指令
        tasks = [node_commander.send_stop_command(nid, attack_id) for nid in target_node_ids]
        await asyncio.gather(*tasks, return_exceptions=True)

        # 只回收本场攻击的配额 (不影响同节点其他在跑攻击)
        await self.rate_limiter.release_attack(attack_id)

        async with self._lock:
            self._active_attacks.pop(attack_id, None)
            self._attack_node_map.pop(attack_id, None)

        await audit_logger.log_attack_stop("controller", attack_id, ",".join(target_node_ids), reason)
        return {"stopped": True, "nodes": target_node_ids}

    async def emergency_stop(self, command: EmergencyStopCommand):
        """紧急熔断"""
        self._emergency_stop.set()

        async with self._lock:
            active_ids = list(self._active_attacks.keys())
            all_target_nodes = command.target_node_ids or [
                n.node_id for n in self.node_registry.get_all_online()
            ]

        # 并行调用所有节点的紧急停止
        success_count = await node_commander.broadcast_emergency_stop(
            command.reason, command.issued_by
        )

        # 熔断 = 全量停止: 清空全部配额, 防止子集释放造成的泄漏
        await self.rate_limiter.release_all()

        async with self._lock:
            for aid in active_ids:
                self._active_attacks.pop(aid, None)
                self._attack_node_map.pop(aid, None)

        await audit_logger.log_emergency_stop(command.issued_by, command.reason, all_target_nodes)
        logger.critical("emergency_stop_executed",
                        reason=command.reason, by=command.issued_by,
                        attacks=len(active_ids), nodes_notified=success_count)

        return {"stopped_attacks": active_ids, "affected_nodes": all_target_nodes, "nodes_notified": success_count}

    def reset_emergency_stop(self):
        self._emergency_stop.clear()
        logger.info("emergency_stop_reset")

    def is_emergency_stop_active(self) -> bool:
        return self._emergency_stop.is_set()

    def collect_result(self, result: AttackResult):
        # BUG-19 服务端兜底: 同一 (attack, node) 的后到结果若计数更小
        # (节点 stop 竞态补发的空占位), 保留累计值更大的那份统计
        existing = self._attack_results[result.attack_id].get(result.node_id)
        if existing is not None and result.total_requests < existing.total_requests:
            result = result.model_copy(update={
                "total_requests": existing.total_requests,
                "successful_requests": max(result.successful_requests, existing.successful_requests),
                "failed_requests": max(result.failed_requests, existing.failed_requests),
                "bytes_sent": max(result.bytes_sent, existing.bytes_sent),
                "bytes_received": max(result.bytes_received, existing.bytes_received),
            })
        self._attack_results[result.attack_id][result.node_id] = result
        task = asyncio.create_task(audit_logger.log_attack_result(result))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def get_attack_status(self, attack_id: str) -> Optional[Dict[str, Any]]:
        # 已停止的攻击仍可查询历史结果 (stop_attack 只清 active 表, 保留 results)
        command = self._active_attacks.get(attack_id)
        results = self._attack_results.get(attack_id, {})
        if command is None and not results:
            return None
        if command is not None:
            return {
                "attack_id": attack_id,
                "command": command.model_dump(mode='json'),
                "results": {nid: r.model_dump(mode='json') for nid, r in results.items()},
                "node_count": len(results)
            }
        # 仅存历史结果 (已停止): 返回最小视图
        return {
            "attack_id": attack_id,
            "command": None,
            "results": {nid: r.model_dump(mode='json') for nid, r in results.items()},
            "node_count": len(results),
            "stopped": True,
        }

    def get_all_active(self) -> List[Dict[str, Any]]:
        return [self.get_attack_status(aid) for aid in self._active_attacks]


