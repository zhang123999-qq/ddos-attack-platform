"""v1.5.0 新增: Admin API 限流器专项测试 (A.3 / R-NEW-2)

覆盖:
- 令牌桶基础: 消费/恢复/拒绝逻辑
- 多 scope 独立桶
- 限流 cost 不等
- check_or_raise 抛 429 + Retry-After header
- 指标计数器 inc
- 容量=0 禁用
- 实际 HTTP 调用触发限流
"""
import asyncio
import hashlib
import hmac
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# 必须在 import 之前设环境 (test_api_smoke 模式)
os.environ.setdefault("SHARED_SECRET", "test-secret-32chars-abcdef1234567890")
os.environ.setdefault("LOG_LEVEL", "error")
os.environ.setdefault("NODE_INSECURE_PLAIN_HTTP", "true")
os.environ.setdefault("CA_STORAGE_DIR", str(Path(os.environ.get("TEMP", "/tmp")) / "ca_test_rl"))
# 关闭限流 (基础测试), 各自测试 override
os.environ["ADMIN_RATE_LIMIT_RPM"] = "60"


def _admin_token() -> str:
    return hmac.new(
        os.environ["SHARED_SECRET"].encode(),
        b"ddos-controller-auth", hashlib.sha256
    ).hexdigest()


def test_token_consume_and_refill():
    """基础令牌桶: 消费 + 恢复"""
    from app.admin_rate_limit import _Bucket
    b = _Bucket(capacity=5, refill_per_sec=2.0, tokens=5.0, last_refill=time.monotonic())
    ok, wait = b.try_consume(3)
    assert ok and wait == 0.0
    assert b.tokens == 2.0
    ok, wait = b.try_consume(3)
    assert not ok
    assert wait > 0
    time.sleep(0.6)
    ok, _ = b.try_consume(3)
    assert ok
    print("TOKEN CONSUME AND REFILL OK")


def test_limiter_isolated_scopes():
    """不同 scope 独立桶"""
    from app.admin_rate_limit import AdminAPIRateLimiter
    rl = AdminAPIRateLimiter(capacity=2, refill_per_sec=0.001)
    for _ in range(2):
        ok, _ = rl.check_sync("scopeA")
        assert ok
    ok, _ = rl.check_sync("scopeA")
    assert not ok
    ok, _ = rl.check_sync("scopeB")
    assert ok
    print("LIMITER ISOLATED SCOPES OK")


def test_limiter_check_or_raise():
    """check_or_raise 抛 429 + Retry-After"""
    from app.admin_rate_limit import AdminAPIRateLimiter
    rl = AdminAPIRateLimiter(capacity=1, refill_per_sec=0.001)
    asyncio.run(rl.check_or_raise("test", cost=1))
    raised = False
    try:
        asyncio.run(rl.check_or_raise("test", cost=1))
    except Exception as e:
        if hasattr(e, "status_code") and e.status_code == 429:
            raised = True
            assert e.headers.get("Retry-After") is not None
    assert raised, "expected 429 HTTPException"
    print("LIMITER CHECK OR RAISE OK (429 + Retry-After)")


def test_limiter_disabled_when_capacity_zero():
    """capacity=0 = 禁用"""
    from app.admin_rate_limit import AdminAPIRateLimiter
    rl = AdminAPIRateLimiter(capacity=0, refill_per_sec=0.0)
    for _ in range(100):
        ok, _ = rl.check_sync("any", cost=10)
        assert ok
    print("LIMITER DISABLED WHEN CAPACITY 0 OK")


def test_limiter_increments_metric():
    """被限流时指标计数器 +1"""
    from app.admin_rate_limit import AdminAPIRateLimiter
    from app.metrics import ADMIN_RATE_LIMIT_BLOCKED_TOTAL

    def get_counter():
        total = 0.0
        for m in ADMIN_RATE_LIMIT_BLOCKED_TOTAL.collect():
            for s in m.samples:
                if s.name.endswith("_total") and s.labels.get("scope") == "metric_test":
                    total += s.value
        return total

    before = get_counter()
    rl = AdminAPIRateLimiter(capacity=1, refill_per_sec=0.001)
    rl.check_sync("metric_test")
    rl.check_sync("metric_test")  # blocked
    after = get_counter()
    assert after > before, f"counter not incremented: {before} -> {after}"
    print(f"LIMITER INCREMENTS METRIC OK ({before} -> {after})")


def test_limiter_cost_different():
    """不同 cost 影响允许的请求数"""
    from app.admin_rate_limit import AdminAPIRateLimiter
    rl = AdminAPIRateLimiter(capacity=10, refill_per_sec=0.001)
    ok1, _ = rl.check_sync("cost_test", cost=5)
    ok2, _ = rl.check_sync("cost_test", cost=5)
    ok3, _ = rl.check_sync("cost_test", cost=5)
    assert ok1 and ok2 and not ok3
    print("LIMITER COST DIFFERENT OK")


def test_limiter_get_stats():
    """get_stats 返回桶状态"""
    from app.admin_rate_limit import AdminAPIRateLimiter
    rl = AdminAPIRateLimiter(capacity=5, refill_per_sec=1.0)
    rl.check_sync("stats_scope")
    stats = rl.get_stats()
    assert "stats_scope" in stats
    assert stats["stats_scope"]["capacity"] == 5
    print("LIMITER GET STATS OK")


def test_limiter_env_disable():
    """ADMIN_RATE_LIMIT_RPM=0 完全禁用"""
    os.environ["ADMIN_RATE_LIMIT_RPM"] = "0"
    import importlib
    import app.admin_rate_limit
    importlib.reload(app.admin_rate_limit)
    rl = app.admin_rate_limit.admin_rate_limiter
    assert rl.capacity == 0
    for _ in range(50):
        ok, _ = rl.check_sync("any")
        assert ok
    os.environ["ADMIN_RATE_LIMIT_RPM"] = "60"
    print("LIMITER ENV DISABLE OK")


def test_http_launch_triggers_429():
    """实际 HTTP: 连续 launch 触发 429"""
    from fastapi.testclient import TestClient
    os.environ["ADMIN_RATE_LIMIT_RPM"] = "12"  # 1 token/5s, cost=2 = 6 launch
    import importlib
    import app.admin_rate_limit
    importlib.reload(app.admin_rate_limit)
    import app.routes.attacks
    importlib.reload(app.routes.attacks)

    from app.main import app
    admin = {"Authorization": f"Bearer {_admin_token()}"}
    with TestClient(app) as client:
        last_code = None
        for i in range(20):
            r = client.post(
                "/api/v1/attacks/launch",
                headers=admin,
                json={
                    "attack_type": "http_flood",
                    "target": {"ip": "10.100.5.5", "port": 80},
                    "duration": 5, "rps": 100, "concurrency": 10,
                }
            )
            last_code = r.status_code
            if r.status_code == 429:
                assert "Retry-After" in r.headers
                print(f"  HTTP 429 at request #{i+1}, Retry-After={r.headers.get('Retry-After')}")
                break
        assert last_code == 429, f"expected 429, last={last_code}"
    os.environ["ADMIN_RATE_LIMIT_RPM"] = "60"
    print("HTTP LAUNCH TRIGGERS 429 OK")


if __name__ == "__main__":
    test_token_consume_and_refill()
    test_limiter_isolated_scopes()
    test_limiter_check_or_raise()
    test_limiter_disabled_when_capacity_zero()
    test_limiter_increments_metric()
    test_limiter_cost_different()
    test_limiter_get_stats()
    test_limiter_env_disable()
    test_http_launch_triggers_429()
    print("\nALL 9 ADMIN RATE LIMIT TESTS PASSED")

