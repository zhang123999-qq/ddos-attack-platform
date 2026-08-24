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


class TargetValidator:
    """目标白名单验证器

    支持两类目标:
    - 单 IP: 必须命中任一允许网段;
    - CIDR:  必须是某允许网段的子集 (subnet_of), 防止白名单被放大。
    占位符目标一律拒绝。
    """

    def __init__(self, allowed_cidrs: List[str]):
        self.allowed_networks = []
        for cidr in allowed_cidrs:
            try:
                from ipaddress import ip_network
                self.allowed_networks.append(ip_network(cidr.strip(), strict=False))
            except ValueError as e:
                logger.warning("invalid_cidr", cidr=cidr, error=str(e))

    def is_allowed(self, target: TargetSpec) -> bool:
        from ipaddress import ip_address, ip_network
        try:
            if getattr(target, "is_placeholder", lambda: False)():
                return False
            try:
                ip = ip_address(target.ip)
                return any(ip in net for net in self.allowed_networks)
            except ValueError:
                net = ip_network(target.ip, strict=False)
                return any(
                    net.version == allowed.version and net.subnet_of(allowed)
                    for allowed in self.allowed_networks
                )
        except (ValueError, TypeError):
            return False


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
        self._attack_results[result.attack_id][result.node_id] = result
        task = asyncio.create_task(audit_logger.log_attack_result(result))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def get_attack_status(self, attack_id: str) -> Optional[Dict[str, Any]]:
        if attack_id not in self._active_attacks:
            return None
        command = self._active_attacks[attack_id]
        results = self._attack_results.get(attack_id, {})
        return {
            "attack_id": attack_id,
            "command": command.model_dump(mode='json'),
            "results": {nid: r.model_dump(mode='json') for nid, r in results.items()},
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

    def validate_overrides(self, scenario_id: str, overrides: Optional[Dict[str, Any]]) -> None:
        """同步预校验 overrides (H-2 修复: 原先占位符缺失只在异步任务里静默 break,
        API 却返回 200+run_id, 调用方无从得知失败)。校验失败抛 ValueError → 端点转 400。
        """
        scenario = self._scenarios.get(scenario_id)
        if not scenario:
            raise ValueError(f"Scenario {scenario_id} not found")
        merged_overrides = overrides or {}
        for i, step in enumerate(scenario.steps):
            data = step.params.model_dump()
            self._deep_merge(data, merged_overrides)
            try:
                params = AttackParams(**data)
            except Exception as e:
                raise ValueError(f"Step {i}: invalid overrides: {e}")
            if params.target.is_placeholder():
                raise ValueError(
                    f"Step {i}: target.ip is still a placeholder. "
                    f"Pass overrides like {{'target': {{'ip': '10.100.10.10'}}}}"
                )

    async def run_scenario(self, scenario_id: str, overrides: Optional[Dict[str, Any]] = None) -> str:
        scenario = self._scenarios.get(scenario_id)
        if not scenario:
            raise ValueError(f"Scenario {scenario_id} not found")

        # 同步预校验 (与 _execute_scenario 内的守卫双保险)
        self.validate_overrides(scenario_id, overrides)

        if scenario_id in self._running_scenarios:
            raise ValueError(f"Scenario {scenario_id} already running")

        task = asyncio.create_task(self._execute_scenario(scenario, overrides or {}))
        self._running_scenarios[scenario_id] = task

        def cleanup(t):
            self._running_scenarios.pop(scenario_id, None)
        task.add_done_callback(cleanup)

        return f"scenario-run-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _deep_merge(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
        """递归合并: 嵌套 dict (如 target) 逐键覆盖, 其余直接替换"""
        for key, value in src.items():
            if isinstance(value, dict) and isinstance(dst.get(key), dict):
                ScenarioManager._deep_merge(dst[key], value)
            else:
                dst[key] = value

    async def _execute_scenario(self, scenario: Scenario, overrides: Dict[str, Any]):
        logger.info("scenario_started", scenario_id=scenario.scenario_id)

        for i, step in enumerate(scenario.steps):
            # P0-3 修复: 深合并 overrides 到 params (原 setattr 会把嵌套 dict
            # 直接塞给 target 字段, 绕过校验且运行期 AttributeError)
            data = step.params.model_dump()
            self._deep_merge(data, overrides)
            try:
                params = AttackParams(**data)
            except Exception as e:
                logger.error("scenario_override_invalid",
                             scenario_id=scenario.scenario_id, step=i, error=str(e))
                break

            # 占位符守卫: 未被 overrides 覆盖的模板目标拒绝执行 (fail-fast)
            if params.target.is_placeholder():
                logger.error("scenario_placeholder_target",
                             scenario_id=scenario.scenario_id, step=i,
                             hint="provide overrides like {'target': {'ip': '10.100.10.10'}}")
                break

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
        # P1-1: 复位必须同步广播到全部节点, 否则节点侧 EMERGENCY_STOP 永久置位
        asyncio.create_task(node_commander.broadcast_emergency_reset())

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