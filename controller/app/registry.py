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
    """攻击执行器 - CRIT-1 修复后通过 NodeCommander 真正下发指令

    v1.3.0 方案C1: 权威状态机 — 每场攻击维护 status/started_at/finished_at,
    REST 列表与详情统一输出, WebUI 不再依赖易丢失的 WS 事件推断状态。
    v1.3.0 方案B2: 结果表 TTL — 攻击结束后保留 RESULT_TTL_MINUTES 分钟供查看,
    后台任务定期清理, 防止内存无限增长。
    """

    RESULT_TTL_MINUTES = 60
    PRUNE_INTERVAL_SECONDS = 60

    def __init__(self, node_registry: NodeRegistry, rate_limiter: RateLimiter, target_validator: TargetValidator):
        self.node_registry = node_registry
        self.rate_limiter = rate_limiter
        self.target_validator = target_validator
        self._active_attacks: Dict[str, AttackCommand] = {}
        self._attack_results: Dict[str, Dict[str, AttackResult]] = defaultdict(dict)
        self._attack_node_map: Dict[str, List[str]] = {}  # attack_id → [node_ids]
        # C1 状态机: attack_id → {status, started_at, finished_at, stop_reason}
        self._attack_meta: Dict[str, Dict[str, Any]] = {}
        self._emergency_stop = asyncio.Event()
        self._lock = asyncio.Lock()
        self._bg_tasks: set = set()  # 持有 fire-and-forget 任务引用, 防止被 GC
        self._prune_task: Optional[asyncio.Task] = None

    async def start(self):
        """B2: 启动结果表 TTL 清理循环"""
        if self._prune_task is None or self._prune_task.done():
            self._prune_task = asyncio.create_task(self._prune_loop())

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    async def _prune_loop(self):
        while True:
            try:
                await asyncio.sleep(self.PRUNE_INTERVAL_SECONDS)
                await self._prune_finished()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("result_prune_error", error=str(e))

    async def _prune_finished(self):
        """清理超过 TTL 的已完成记录与结果; 兜底回收超时未终态的僵尸攻击"""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.RESULT_TTL_MINUTES)
        async with self._lock:
            expired = [
                aid for aid, meta in self._attack_meta.items()
                if meta.get("finished_at") and meta["finished_at"] < cutoff
            ]
            for aid in expired:
                self._attack_meta.pop(aid, None)
                self._active_attacks.pop(aid, None)
                self._attack_node_map.pop(aid, None)
                self._attack_results.pop(aid, None)
            # 僵尸兜底: running 但已超过 duration + 宽限期且从未收到节点终态
            for aid, meta in list(self._attack_meta.items()):
                if meta.get("status") != "running":
                    continue
                started = meta.get("started_at_dt")
                if not started:
                    continue
                cmd = self._active_attacks.get(aid)
                max_dur = (cmd.params.duration if cmd else 3600) + 120
                if datetime.now(timezone.utc) - started > timedelta(seconds=max_dur):
                    meta.update({"status": "stopped", "finished_at": self._now_iso()})
                    logger.info("zombie_attack_finalized", attack_id=aid)
            if expired:
                logger.info("results_pruned", count=len(expired), ttl_minutes=self.RESULT_TTL_MINUTES)

    def _meta_view(self, attack_id: str) -> Dict[str, Any]:
        meta = self._attack_meta.get(attack_id) or {}
        return {
            "status": meta.get("status", "unknown"),
            "started_at": meta.get("started_at"),
            "finished_at": meta.get("finished_at"),
            "stop_reason": meta.get("stop_reason"),
        }

    async def execute_attack(self, command: AttackCommand) -> Dict[str, Any]:
        """执行攻击指令"""
        # 1. 目标验证 — v1.3.0 方案A: 域名/IP 不做限制; 仅拒绝未覆盖的场景占位符
        if not self.target_validator.is_allowed(command.params.target):
            raise ValueError(
                f"Placeholder target {command.params.target.ip} must be overridden before launch"
            )

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
            # C1: 记录权威状态
            now = datetime.now(timezone.utc)
            self._attack_meta[attack_id] = {
                "status": "running",
                "started_at": now.isoformat(),
                "started_at_dt": now,
                "finished_at": None,
                "stop_reason": None,
            }

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
            # 全部失败: 记录 failed 终态 (保留记录供 UI 显示), 清理运行表
            async with self._lock:
                self._active_attacks.pop(attack_id, None)
                self._attack_node_map.pop(attack_id, None)
                self._attack_results.pop(attack_id, None)
                meta = self._attack_meta.get(attack_id)
                if meta:
                    meta.update({"status": "failed",
                                 "finished_at": self._now_iso(),
                                 "stop_reason": "command delivery failed"})
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
            # C1: stopping → stopped (停止指令发出即视为终态, 节点终态结果随后合并计数)
            meta = self._attack_meta.get(attack_id)
            if meta and meta.get("status") in ("running", "starting", "launching"):
                meta["status"] = "stopped"
                meta["finished_at"] = self._now_iso()
                meta["stop_reason"] = reason

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
                meta = self._attack_meta.get(aid)
                if meta:
                    meta.update({"status": "emergency_stopped",
                                 "finished_at": self._now_iso(),
                                 "stop_reason": f"emergency: {command.reason}"})

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
        """接收节点结果 (终态或 C3 周期快照), 合并计数并维护状态机"""
        # C3 周期快照: 节点每 2s 上报 status=running 的部分结果。
        # 与已有结果按"逐字段取大"合并, 保证 UI 计数单调递增且不被旧快照回退
        existing = self._attack_results[result.attack_id].get(result.node_id)
        if existing is not None:
            result = result.model_copy(update={
                "total_requests": max(result.total_requests, existing.total_requests),
                "successful_requests": max(result.successful_requests, existing.successful_requests),
                "failed_requests": max(result.failed_requests, existing.failed_requests),
                "bytes_sent": max(result.bytes_sent, existing.bytes_sent),
                "bytes_received": max(result.bytes_received, existing.bytes_received),
            })
        # BUG-19 兜底保留: 快照流下后到终态计数必然 ≥ 已有值, 上述 max 已覆盖
        self._attack_results[result.attack_id][result.node_id] = result

        # C1: 节点上报终态时推进状态机 (running 快照不改变状态)
        terminal = {AttackStatus.STOPPED, AttackStatus.FAILED, AttackStatus.EMERGENCY_STOPPED}
        if result.status in terminal:
            meta = self._attack_meta.get(result.attack_id)
            if meta and meta.get("status") == "running":
                expected = set(self._attack_node_map.get(result.attack_id) or [])
                reported_terminal = {
                    nid for nid, r in self._attack_results[result.attack_id].items()
                    if r.status in terminal
                }
                if not expected or expected <= reported_terminal:
                    meta.update({"status": "completed",
                                 "finished_at": self._now_iso()})

        task = asyncio.create_task(audit_logger.log_attack_result(result))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def get_attack_status(self, attack_id: str) -> Optional[Dict[str, Any]]:
        """攻击详情 — 含权威状态 (C1); 已结束攻击在 TTL 窗口内仍可查询"""
        command = self._active_attacks.get(attack_id)
        results = self._attack_results.get(attack_id, {})
        meta = self._attack_meta.get(attack_id)
        if command is None and not results and meta is None:
            return None
        view = {
            "attack_id": attack_id,
            "command": command.model_dump(mode='json') if command else None,
            "results": {nid: r.model_dump(mode='json') for nid, r in results.items()},
            "node_count": len(results),
        }
        view.update(self._meta_view(attack_id))
        return view

    def get_all_active(self) -> List[Dict[str, Any]]:
        """全部攻击 (进行中 + TTL 窗口内的已结束记录), 每条含权威状态 (C2)"""
        out = []
        for aid in list(self._attack_meta.keys()):
            view = self.get_attack_status(aid)
            if view is not None:
                out.append(view)
        return out


