"""场景加载/深合并/占位符守卫回归测试

在 controller/ 目录下运行: python tests/test_scenarios.py
"""
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import AttackParams  # noqa: E402
from app.orchestrator import ScenarioManager, TargetValidator  # noqa: E402
from app.models import TargetSpec  # noqa: E402


class RecordingExecutor:
    def __init__(self):
        self.calls = []

    async def execute_attack(self, command):
        self.calls.append(command)


def test_all_builtin_scenarios_load():
    sm = ScenarioManager(RecordingExecutor())
    ids = {s.scenario_id for s in sm.list_scenarios()}
    expected = {"cc_attack", "syn_flood", "slowloris", "udp_reflection", "mixed_wave", "ramp_up"}
    assert expected.issubset(ids), f"missing: {expected - ids}"
    print(f"SCENARIO LOAD OK ({len(ids)} scenarios)")


def test_deep_merge_overrides():
    data = AttackParams(target={"ip": "TARGET_IP_PLACEHOLDER", "port": 80}).model_dump()
    ScenarioManager._deep_merge(data, {"target": {"ip": "10.100.10.10"}, "rps": 5000})
    params = AttackParams(**data)
    assert params.target.ip == "10.100.10.10"
    assert params.rps == 5000
    assert params.target.port == 80
    print("DEEP MERGE OK")


def test_placeholder_guard_blocks_execution():
    executor = RecordingExecutor()
    sm = ScenarioManager(executor)

    async def run():
        # H-2 修复后: 无 overrides → run_scenario 同步预校验直接抛 ValueError (端点转 400),
        # 不再 200+静默 break; 双保险: _execute_scenario 内守卫仍在
        try:
            await sm.run_scenario("cc_attack", {})
            raised = False
        except ValueError:
            raised = True
        await asyncio.sleep(0.1)
        return raised, len(executor.calls)

    raised, calls = asyncio.run(run())
    assert raised, "run_scenario must raise ValueError synchronously on placeholder target"
    assert calls == 0, f"placeholder target must not execute, got {calls} calls"
    print("PLACEHOLDER GUARD OK (sync ValueError, 0 attacks launched)")


async def _run_with_override(executor, sm):
    await sm.run_scenario("cc_attack", {"target": {"ip": "127.0.0.1"}, "duration": 1})
    await asyncio.sleep(0.5)
    return list(executor.calls)


def test_overrides_reach_executor():
    executor = RecordingExecutor()
    sm = ScenarioManager(executor)
    calls = asyncio.run(_run_with_override(executor, sm))
    assert len(calls) == 1, f"expected exactly 1 attack after override, got {len(calls)}"
    assert calls[0].params.target.ip == "127.0.0.1"
    print("OVERRIDE EXECUTION OK (target resolved)")


def test_cidr_subnet_semantics():
    """v1.3.0 方案A: 目标不限 — TargetValidator 恒放行 (占位符除外), 域名合法"""
    tv = TargetValidator(["10.100.0.0/16"])
    assert tv.is_allowed(TargetSpec(ip="10.100.5.5")) is True
    assert tv.is_allowed(TargetSpec(ip="10.100.5.0/24")) is True    # CIDR 放行
    assert tv.is_allowed(TargetSpec(ip="192.168.1.1")) is True      # 不再受白名单限制
    assert tv.is_allowed(TargetSpec(ip="example.com")) is True      # 域名放行
    assert tv.is_allowed(TargetSpec(ip="target.example.com")) is True
    assert tv.is_allowed(TargetSpec(ip="TARGET_IP_PLACEHOLDER")) is False  # 占位符仍拒绝
    # is_hostname 判定
    assert TargetSpec(ip="example.com").is_hostname() is True
    assert TargetSpec(ip="10.100.5.5").is_hostname() is False
    print("CIDR SUBNET SEMANTICS OK (restrictions disabled, domains accepted)")


if __name__ == "__main__":
    test_all_builtin_scenarios_load()
    test_deep_merge_overrides()
    test_placeholder_guard_blocks_execution()
    test_overrides_reach_executor()
    test_cidr_subnet_semantics()
    print("ALL SCENARIO TESTS PASSED")
