from __future__ import annotations

import asyncio
import os
import uuid
from typing import Dict, List, Optional, Any
from pathlib import Path
import yaml

import structlog

from app.models import (
    AttackType, AttackCommand, AttackParams, AttackResult, AttackStatus,
    NodeInfo, NodeStatus, NodeHeartbeat, Scenario, ScenarioStep, TargetSpec
)
from app.ratelimit import RateLimiter
from app.registry import AttackExecutor
from app.audit import audit_logger
from app.node_commander import node_commander

logger = structlog.get_logger(__name__)

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


