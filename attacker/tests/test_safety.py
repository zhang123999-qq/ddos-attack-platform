# -*- coding: utf-8 -*-
"""Attacker 节点安全基座测试: 攻击注册表完整性 / 目标白名单双层校验 /
熔断 Event 强制拦截 / 令牌桶速率上限 — 守住双层防御的节点侧"""
import asyncio
import os
import sys
import time

os.environ.setdefault("SHARED_SECRET", "attacker-test-secret-32chars-abc123")
os.environ.setdefault("LOG_LEVEL", "error")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models import AttackCommand, AttackParams, AttackType, TargetSpec  # noqa: E402
from app.attacks import AttackRegistry, SafeAttackBase, http_flood  # noqa: E402


def _cmd(attack_type: AttackType, ip="127.0.0.1", rps=100) -> AttackCommand:
    return AttackCommand(
        attack_id=f"atk-test-{attack_type.value}",
        attack_type=attack_type,
        params=AttackParams(target=TargetSpec(ip=ip), duration=1, rps=rps),
    )


def test_registry_completeness():
    """5 种攻击类型必须全部注册 — 缺一即部署配置失效"""
    avail = {t.value for t in AttackRegistry.list_available()}
    expected = {"http_flood", "slowloris", "syn_flood", "udp_flood", "udp_reflection"}
    missing = expected - avail
    assert not missing, f"registry missing: {missing}"
    print("REGISTRY COMPLETENESS OK (5/5)")


def test_registry_rejects_unknown_type():
    """未注册类型必须 ValueError 拒绝, 不得静默"""
    try:
        AttackRegistry.create(_cmd(AttackType.HTTP_FLOOD))
        # HTTP_FLOOD 已注册 — 应成功创建
        inst = AttackRegistry.create(_cmd(AttackType.UDP_REFLECTION))
        assert inst is not None
    except ValueError as e:
        raise AssertionError(f"registered types must instantiate: {e}")

    class FakeType:
        value = "not_a_real_attack"
    _has_wrapped = hasattr(_cmd, "__wrapped__")  # noqa: F841 (intentional probe)
    # 直接构造未注册枚举路径: 清空一个已知项验证拒绝逻辑
    saved = AttackRegistry._registry.pop(AttackType.SYN_FLOOD, None)
    try:
        try:
            AttackRegistry.create(_cmd(AttackType.SYN_FLOOD))
            raise AssertionError("unregistered type must raise ValueError")
        except ValueError:
            pass
    finally:
        if saved:
            AttackRegistry.register(AttackType.SYN_FLOOD, saved)
    print("REGISTRY UNKNOWN-TYPE REJECTION OK")


def test_target_whitelist_blocks_outside_cidr():
    """v1.5.0: 节点侧白名单重新生效 — 白名单外目标必须被 pre_flight 拒绝"""
    os.environ["ALLOWED_TARGET_CIDRS"] = "10.100.0.0/16"
    os.environ["ALLOW_ANY_TARGET"] = "false"
    # 重新触发 __init_subclass__ 让环境变量生效
    import importlib
    from app.attacks import base
    importlib.reload(base)
    from app.attacks import http_flood
    importlib.reload(http_flood)

    inst = http_flood.HTTPFloodAttack(_cmd(AttackType.HTTP_FLOOD, ip="8.8.8.8"))
    from app.attacks.base import SafetyError
    raised = False
    try:
        inst.pre_flight_check("8.8.8.8")
    except SafetyError as e:
        raised = "not in allowed" in str(e).lower() or "whitelist" in str(e).lower()
    assert raised, f"outside-CIDR target must be blocked by whitelist, got no SafetyError"
    print("TARGET WHITELIST BLOCKS OUTSIDE CIDR OK")

    # 白名单内目标放行 (不抛 SafetyError)
    inst_ok = http_flood.HTTPFloodAttack(_cmd(AttackType.HTTP_FLOOD, ip="10.100.5.5"))
    inst_ok.pre_flight_check("10.100.5.5")
    print("TARGET WHITELIST ALLOWS INSIDE CIDR OK")

    # ALLOW_ANY_TARGET=true 显式 opt-out
    os.environ["ALLOW_ANY_TARGET"] = "true"
    importlib.reload(base)
    importlib.reload(http_flood)
    inst_any = http_flood.HTTPFloodAttack(_cmd(AttackType.HTTP_FLOOD, ip="8.8.8.8"))
    inst_any.pre_flight_check("8.8.8.8")  # 不应抛
    os.environ["ALLOW_ANY_TARGET"] = "false"  # 还原
    print("ALLOW_ANY_TARGET OPT-OUT OK")


def test_whitelist_classmethod_removed():
    """v1.5.0: validate_target 类方法已删除, 由 _is_target_in_whitelist 替代"""
    cls = http_flood.HTTPFloodAttack
    assert not hasattr(cls, "validate_target"), "validate_target must be removed"
    assert hasattr(cls, "_is_target_in_whitelist"), "_is_target_in_whitelist must exist"
    assert isinstance(cls.ALLOWED_TARGET_CIDRS, list)
    assert isinstance(cls.ALLOW_ANY_TARGET, bool)
    print("WHITELIST CLASSMETHOD STATE OK")


def test_emergency_stop_blocks_execution():
    """熔断 Event 置位后任何攻击 execute 必须立即拦截 —
    实现行为: SafetyError 从 pre_flight 抛出(try 块之外), 不得进入攻击逻辑"""
    async def run():
        SafeAttackBase.set_emergency_stop(True)
        try:
            inst = AttackRegistry.create(_cmd(AttackType.SLOWLORIS))
            result = await inst.execute()
            # 若实现改为内部捕获, 状态必须是失败族
            from app.models import AttackStatus
            return ("status", result.status in (AttackStatus.FAILED, AttackStatus.EMERGENCY_STOPPED))
        except Exception as e:
            return ("raised", "Safety" in type(e).__name__ or "emergency" in str(e).lower())
        finally:
            SafeAttackBase.set_emergency_stop(False)

    kind, ok = asyncio.run(run())
    assert ok, f"emergency stop must block execution (kind={kind})"
    print("EMERGENCY STOP BLOCKS EXECUTION OK")


def test_token_bucket_enforces_rate_ceiling():
    """令牌桶: 100/s 配额在 50ms 内最多放行 ~15 个突发, 不可能放行 200"""
    from app.attacks.base import TokenBucket

    async def run():
        bucket = TokenBucket(rate=100, burst=10)
        allowed = 0
        deadline = time.monotonic() + 0.05
        while time.monotonic() < deadline:
            if await bucket.consume(1):
                allowed += 1
            else:
                await asyncio.sleep(0.001)
        return allowed

    n = asyncio.run(run())
    assert n < 30, f"token bucket leaked: {n} passes in 50ms @ rate=100/s"
    print(f"TOKEN BURST CEILING OK ({n} passes in 50ms @ 100/s + burst 10)")


def test_duration_zero_or_negative_rejected_by_model():
    """参数边界: duration/rps 由 Pydantic ge 约束, 非法值构造即失败"""
    from pydantic import ValidationError
    try:
        AttackParams(target=TargetSpec(ip="127.0.0.1"), duration=0)
        raise AssertionError("duration=0 must fail validation")
    except ValidationError:
        pass
    try:
        AttackParams(target=TargetSpec(ip="127.0.0.1"), duration=3601)
        raise AssertionError("duration>3600 must fail validation")
    except ValidationError:
        pass
    print("PARAM BOUNDS VALIDATION OK")


if __name__ == "__main__":
    # v1.5.0: 目标白名单默认开启 — 替换原 v1.3.0 的 test_no_target_restrictions
    test_registry_completeness()
    test_registry_rejects_unknown_type()
    test_target_whitelist_blocks_outside_cidr()
    test_whitelist_classmethod_removed()
    test_emergency_stop_blocks_execution()
    test_token_bucket_enforces_rate_ceiling()
    test_duration_zero_or_negative_rejected_by_model()
    print("ALL ATTACKER SAFETY TESTS PASSED")
