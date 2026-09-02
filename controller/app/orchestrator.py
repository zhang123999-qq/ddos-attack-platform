from __future__ import annotations

import asyncio
from typing import Dict, List, Optional, Any

import structlog

from app.models import (
    AttackCommand, AttackResult, NodeInfo, NodeHeartbeat, Scenario, EmergencyStopCommand
)
from app.audit import audit_logger
from app.node_commander import node_commander

# 拆分后的子系统 (保持旧导入路径兼容: from app.orchestrator import RateLimiter 等)
from app.ratelimit import TargetValidator, RateLimiter, QuotaExhaustedError
from app.registry import NodeRegistry, AttackExecutor
from app.scenario import ScenarioManager

logger = structlog.get_logger(__name__)

class Orchestrator:
    """总编排器"""

    def __init__(
        self,
        allowed_cidrs: List[str],
        global_rps: int,
        global_pps: int,
        global_concurrent: int,
        allow_any_target: bool = False,
    ):
        # v1.5.0: 目标白名单默认开启 (fail-closed), ALLOW_ANY_TARGET=true 显式 opt-out
        self.target_validator = TargetValidator(allowed_cidrs, allow_any=allow_any_target)
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
        await self.executor.start()
        await audit_logger.start()

        # v1.5.0: 恢复持久化状态 (R-NEW-1)
        await self._load_persisted_state()

        self._started = True
        logger.info("orchestrator_started")

    async def _load_persisted_state(self) -> None:
        """从 SQLite 恢复重启前的状态: 节点 / 攻击 / 熔断"""
        from app.state_store import state_store
        if not state_store.enabled:
            return

        # 1. 恢复熔断状态
        em = state_store.load_emergency()
        if em and em.get("active"):
            self.executor._emergency_stop.set()  # noqa: SLF001
            logger.warning("emergency_stop_restored_from_disk",
                           reason=em.get("reason"),
                           issued_by=em.get("issued_by"))

        # 2. 恢复节点 (仅 ON-LIKE 状态, 心跳过期由节点重新 register 覆盖)
        for node_data in state_store.load_nodes():
            try:
                # status 强制为 OFFLINE — 重启后节点必须重新 register
                # 否则会误认为还在跑 (实际进程已断)
                node_data["status"] = "offline"
                node = NodeInfo(**node_data)
                self.node_registry._nodes[node.node_id] = node  # noqa: SLF001
                logger.info("node_restored_from_disk",
                            node_id=node.node_id, type=node.node_type)
            except Exception as e:
                logger.warning("node_restore_failed",
                               node_id=node_data.get("node_id"), error=str(e))

        # 3. 恢复攻击 (状态置为 running — 实际是否还在跑由节点侧再次确认)
        # 注意: 我们**不**恢复 _active_attacks (AttackCommand 对象), 仅保留元数据
        # 让 UI 显示"重启前还有这些攻击在跑", 实际是否存活依赖节点上报
        for attack_data in state_store.load_attacks():
            aid = attack_data.get("attack_id")
            if not aid:
                continue
            # 元数据写回 attack_meta (status=stale_until_heartbeat)
            self.executor._attack_meta[aid] = {  # noqa: SLF001
                "status": "running_pre_restart",
                "started_at": attack_data.get("started_at"),
                "started_at_dt": None,  # 字符串, 失去 datetime 性质
                "finished_at": None,
                "stop_reason": None,
            }
            logger.warning("attack_restored_from_disk_unconfirmed",
                           attack_id=aid,
                           target=attack_data.get("target_ip"))

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
        # BUG-18 防护: 节点在受限环境 (容器/netns) 探测不到本机 IP 时会上报 127.0.0.1,
        # 直接采用会让控制器把攻击指令发给自己。回环地址一律改用请求来源 IP 由调用方覆盖。
        node_commander.register_node(node.node_id, node.ip)
        return registered

    async def node_heartbeat(self, hb: NodeHeartbeat):
        await self.node_registry.heartbeat(hb)
        await audit_logger.log_node_heartbeat(hb.node_id, hb.cpu_percent, hb.memory_percent, hb.network_mbps)

    def get_node_by_id(self, node_id: str) -> Optional[NodeInfo]:
        """BUG-6 修复: 详情查询读【全量】节点字典 — offline 条目同样可见。
        (原实现遍历 get_all_online(), 节点离线后详情接口 404)"""
        return self.node_registry.get_node(node_id)

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
        # v1.5.0: 持久化复位 (R-NEW-1)
        from app.state_store import state_store
        asyncio.create_task(state_store.save_emergency(active=False, reason="", issued_by=""))

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
