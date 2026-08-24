from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any

import structlog

from app.models import (
    AttackType, AttackCommand, AttackParams, AttackResult, AttackStatus,
    NodeInfo, NodeStatus, NodeHeartbeat, Scenario, ScenarioStep,
    EmergencyStopCommand, TargetSpec
)
from app.audit import audit_logger
from app.node_commander import node_commander

# 拆分后的子系统 (保持旧导入路径兼容: from app.orchestrator import RateLimiter 等)
from app.ratelimit import TargetValidator, RateLimiter
from app.registry import QuotaExhaustedError, NodeRegistry, AttackExecutor
from app.scenario import ScenarioManager

logger = structlog.get_logger(__name__)

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
