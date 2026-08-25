# -*- coding: utf-8 -*-
"""BUG-2 修复测试: 连续错误指数退避边界

运行 (attacker/ 目录): python -m pytest tests/test_error_backoff.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_error_backoff_zero_when_no_errors():
    from app.attacks.base import SafeAttackBase
    assert SafeAttackBase._error_backoff(0) == 0.0
    assert SafeAttackBase._error_backoff(-3) == 0.0
    print("BACKOFF ZERO OK")


def test_error_backoff_exponential_and_capped():
    from app.attacks.base import SafeAttackBase
    seq = [SafeAttackBase._error_backoff(n) for n in range(1, 13)]
    # 单调不减、指数起步 (20ms → 40ms → 80ms → 160ms)
    assert all(b + 1e-9 >= a for a, b in zip(seq, seq[1:])), "must be monotonic"
    assert abs(seq[0] - 0.02) < 5e-3, f"first backoff ~20ms, got {seq[0]}"
    assert abs(seq[1] - 0.04) < 5e-3, f"second backoff ~40ms, got {seq[1]}"
    # 封顶 250ms — 万级 worker 错误风暴时每 worker 至少 250ms 一次重试
    cap = SafeAttackBase.ERROR_BACKOFF_CAP
    assert cap == 0.25
    assert all(v <= cap + 1e-9 for v in seq), "must never exceed cap"
    assert seq[-1] == cap, "deep streak must sit at cap"
    print(f"BACKOFF EXPONENTIAL/CAP OK (cap={cap}s)")


def test_http_flood_worker_uses_backoff():
    """http_flood worker 源码必须在错误路径调用退避 (静态守护, 防回归删除)"""
    import inspect
    from app.attacks import http_flood
    src = inspect.getsource(http_flood.HTTPFloodAttack._worker)
    assert "_error_backoff" in src
    assert "consec_errors" in src
    print("HTTP_FLOOD WORKER BACKOFF WIRED OK")
