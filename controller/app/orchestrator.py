from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
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


class TargetValidator:
    """目标白名单验证器"""

    def __init__(self, allowed_cidrs: List[str]):
        self.allowed_networks = []
        for cidr in allowed_cidrs:
            try:
                from ipaddress import ip_network
                self.allowed_networks.append(ip_network(cidr.strip(), strict=False))
            except ValueError as e:
                logger.warning("invalid_cidr", cidr=cidr, error=str(e))

    def is_allowed(self, target: TargetSpec) -> bool:
        from ipaddress import ip_address
        try:
            ip = ip_address(target.ip)
            return any(ip in net for net in self.allowed_networks)
        except ValueError:
            return False


class RateLimiter:
    """分布式配额管理器 (Controller 侧聚合)"""

    def __init__(self, global_rps: int, global_pps: int, global_concurrent: int):
        self.global_rps = global_rps
        self.global_pps = global_pps
        self.global_concurrent = global_concurrent
        self._node_quotas: Dict[str, Dict[str, int]] = defaultdict(lambda: {"rps": 0, "pps": 0, "concurrent": 0})
        self._lock = asyncio.Lock()

    async def allocate(self, node_id: str, attack_type: AttackType, requested_rps: int, requested_concurrent: int) -> Dict[str, int]:
        async with self._lock:
            total_rps = sum(q["rps"] for q in self._node_quotas.values())
            total_pps = sum(q["pps"] for q in self._node_quotas.values())
            total_concurrent = sum(q["concurrent"] for q in self._node_quotas.values())

            remaining_rps = max(0, self.global_rps - total_rps)
            remaining_pps = max(0, self.global_pps - total_pps)
            remaining_concurrent = max(0, self.global_concurrent - total_concurrent)

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
            self._node_quotas[node_id] = quota
            return quota

    async def release(self, node_id: str):
        async with self._lock:
            self._node_quotas.pop(node_id, None)

    async def release_all(self, node_ids: List[str]):
        async with self._lock:
            for nid in node_ids:
                self._node_quotas.pop(nid, None)

    def get_usage(self) -> Dict[str, Any]:
        total_rps = sum(q["rps"] for q in self._node_quotas.values())
        total_pps = sum(q["pps"] for q in self._node_quotas.values())
        total_concurrent = sum(q["concurrent"] for q in self._node_quotas.values())
        return {
            "global_rps": self.global_rps,
            "global_pps": self.global_pps,
            "global_concurrent": self.global_concurrent,
            "used_rps": total_rps,
            "used_pps": total_pps,
            "used_concurrent": total_concurrent,
            "node_quotas": dict(self._node_quotas)
        }


class QuotaExhaustedError(Exception):
    pass


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
            node.last_heartbeat = datetime.utcnow()
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
            now = datetime.utcnow()
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

        # 4. 分配配额
        per_node_rps = command.params.rps
        per_node_concurrent = command.params.concurrency
        allocated = {}
        for node in target_nodes:
            try:
                quota = await self.rate_limiter.allocate(
                    node.node_id, command.attack_type, per_node_rps, per_node_concurrent
                )
                allocated[node.node_id] = quota
            except QuotaExhaustedError:
                for nid in allocated:
                    await self.rate_limiter.release(nid)
                raise

        # 5. 下发指令 → 真正通过 HTTP 调用 Attacker API
        attack_id = command.attack_id or f"atk-{uuid.uuid4().hex[:12]}"
        command.attack_id = attack_id

        async with self._lock:
            self._active_attacks[attack_id] = command
            self._attack_results[attack_id] = {}
            self._attack_node_map[attack_id] = [n.node_id for n in target_nodes]

        await audit_logger.log_attack_start("controller", command, [n.node_id for n in target_nodes])

        # 并行发送给所有节点
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
                "params": node_params.model_dump(),
                "scenario_id": command.scenario_id,
                "node_ids": [node.node_id],
                "priority": command.priority,
            }

            success = await node_commander.send_attack_command(node.node_id, node_command_dict)
            send_results.append({"node_id": node.node_id, "success": success})

        if not any(r["success"] for r in send_results):
            # 所有节点发送失败时回滚配额
            for nid in allocated:
                await self.rate_limiter.release(nid)
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

        await self.rate_limiter.release_all(target_node_ids)

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

        # 释放所有配额
        await self.rate_limiter.release_all(all_target_nodes)

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
        self._attack_results[result.attack_id][result.node_id] = result
        asyncio.create_task(audit_logger.log_attack_result(result))

    def get_attack_status(self, attack_id: str) -> Optional[Dict[str, Any]]:
        if attack_id not in self._active_attacks:
            return None
        command = self._active_attacks[attack_id]
        results = self._attack_results.get(attack_id, {})
        return {
            "attack_id": attack_id,
            "command": command.model_dump(),
            "results": {nid: r.model_dump() for nid, r in results.items()},
            "node_count": len(results)
        }

    def get_all_active(self) -> List[Dict[str, Any]]:
        return [self.get_attack_status(aid) for aid in self._active_attacks]


class ScenarioManager:
    """场景管理器"""

    def __init__(self, executor: AttackExecutor):
        self.executor = executor
        self._scenarios: Dict[str, Scenario] = {}
        self._running_scenarios: Dict[str, asyncio.Task] = {}
        self._load_builtin_scenarios()

    def _load_builtin_scenarios(self):
        """加载内置场景 - 支持多种路径搜索"""
        search_paths = [
            Path(__file__).parent.parent.parent / "scenarios",   # 容器内标准路径
            Path.cwd() / "scenarios",                             # 当前目录
            Path(os.getenv("SCENARIOS_PATH", "")),                # 环境变量指定
        ]
        import os

        for scenarios_dir in search_paths:
            if scenarios_dir and scenarios_dir.exists():
                for yaml_file in sorted(scenarios_dir.glob("*.yaml")):
                    try:
                        with open(yaml_file, 'r', encoding='utf-8') as f:
                            data = yaml.safe_load(f)
                        if data and "scenario_id" in data:
                            scenario = Scenario(**data)
                            self._scenarios[scenario.scenario_id] = scenario
                            logger.info("scenario_loaded", scenario_id=scenario.scenario_id, path=str(yaml_file))
                    except Exception as e:
                        logger.error("scenario_load_failed", file=str(yaml_file), error=str(e))

    def get_scenario(self, scenario_id: str) -> Optional[Scenario]:
        return self._scenarios.get(scenario_id)

    def list_scenarios(self) -> List[Scenario]:
        return list(self._scenarios.values())

    async def run_scenario(self, scenario_id: str, overrides: Optional[Dict[str, Any]] = None) -> str:
        scenario = self._scenarios.get(scenario_id)
        if not scenario:
            raise ValueError(f"Scenario {scenario_id} not found")

        if scenario_id in self._running_scenarios:
            raise ValueError(f"Scenario {scenario_id} already running")

        task = asyncio.create_task(self._execute_scenario(scenario, overrides or {}))
        self._running_scenarios[scenario_id] = task

        def cleanup(t):
            self._running_scenarios.pop(scenario_id, None)
        task.add_done_callback(cleanup)

        return f"scenario-run-{uuid.uuid4().hex[:8]}"

    async def _execute_scenario(self, scenario: Scenario, overrides: Dict[str, Any]):
        logger.info("scenario_started", scenario_id=scenario.scenario_id)

        for i, step in enumerate(scenario.steps):
            params = step.params.model_copy()
            if overrides:
                for key, value in overrides.items():
                    if hasattr(params, key):
                        setattr(params, key, value)

            command = AttackCommand(
                attack_id=f"{scenario.scenario_id}-step-{i}-{uuid.uuid4().hex[:8]}",
                attack_type=step.attack_type,
                params=params,
                scenario_id=scenario.scenario_id,
                node_ids=[],
            )

            try:
                await self.executor.execute_attack(command)
            except Exception as e:
                logger.error("scenario_step_failed", scenario_id=scenario.scenario_id, step=i, error=str(e))
                break

            # 等待攻击完成 (duration 在 execute._check_stop() 中生效)
            await asyncio.sleep(params.duration + min(params.duration * 0.1, 10))

            if step.delay_after > 0:
                await asyncio.sleep(step.delay_after)

        logger.info("scenario_completed", scenario_id=scenario.scenario_id)

    async def stop_scenario(self, scenario_id: str):
        if scenario_id in self._running_scenarios:
            self._running_scenarios[scenario_id].cancel()
            try:
                await self._running_scenarios[scenario_id]
            except asyncio.CancelledError:
                pass
            logger.info("scenario_stopped", scenario_id=scenario_id)


class Orchestrator:
    """总编排器"""

    def __init__(self, allowed_cidrs: List[str], global_rps: int, global_pps: int, global_concurrent: int):
        self.target_validator = TargetValidator(allowed_cidrs)
        self.rate_limiter = RateLimiter(global_rps, global_pps, global_concurrent)
        self.node_registry = NodeRegistry()
        self.executor = AttackExecutor(self.node_registry, self.rate_limiter, self.target_validator)
        self.scenario_manager = ScenarioManager(self.executor)
        self._started = False

    async def start(self):
        if self._started:
            return
        await node_commander.start()
        await self.node_registry.start()
        await audit_logger.start()
        self._started = True
        logger.info("orchestrator_started")

    async def stop(self):
        if not self._started:
            return
        await self.executor.emergency_stop(EmergencyStopCommand(
            reason="orchestrator_shutdown", issued_by="system"
        ))
        await self.node_registry.stop()
        await node_commander.stop()
        await audit_logger.stop()
        self._started = False
        logger.info("orchestrator_stopped")

    async def register_node(self, node: NodeInfo) -> NodeInfo:
        registered = await self.node_registry.register(node)
        # CRIT-1: 注册 NodeCommander 通信地址
        node_commander.register_node(node.node_id, node.ip)
        return registered

    async def node_heartbeat(self, hb: NodeHeartbeat):
        await self.node_registry.heartbeat(hb)
        await audit_logger.log_node_heartbeat(hb.node_id, hb.cpu_percent, hb.memory_percent, hb.network_mbps)

    async def unregister_node(self, node_id: str):
        await self.node_registry.unregister(node_id)

    async def launch_attack(self, command: AttackCommand) -> Dict[str, Any]:
        return await self.executor.execute_attack(command)

    async def stop_attack(self, attack_id: str, reason: str = "manual") -> Dict[str, Any]:
        return await self.executor.stop_attack(attack_id, reason)

    async def emergency_stop(self, command: EmergencyStopCommand) -> Dict[str, Any]:
        return await self.executor.emergency_stop(command)

    def reset_emergency_stop(self):
        self.executor.reset_emergency_stop()

    def is_emergency_active(self) -> bool:
        return self.executor.is_emergency_stop_active()

    def collect_result(self, result: AttackResult):
        self.executor.collect_result(result)

    def get_attack_status(self, attack_id: str) -> Optional[Dict[str, Any]]:
        return self.executor.get_attack_status(attack_id)

    def get_all_attacks(self) -> List[Dict[str, Any]]:
        return self.executor.get_all_active()

    def get_nodes(self) -> List[NodeInfo]:
        return self.node_registry.get_all_online()

    def get_rate_limit_status(self) -> Dict[str, Any]:
        return self.rate_limiter.get_usage()

    def get_scenarios(self) -> List[Scenario]:
        return self.scenario_manager.list_scenarios()

    async def run_scenario(self, scenario_id: str, overrides: Optional[Dict[str, Any]] = None) -> str:
        return await self.scenario_manager.run_scenario(scenario_id, overrides)

    async def stop_scenario(self, scenario_id: str):
        await self.scenario_manager.stop_scenario(scenario_id)