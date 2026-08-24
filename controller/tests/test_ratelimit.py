"""RateLimiter 回归测试 — 验证 (attack_id, node_id) 记账修复"""
import sys
import asyncio

sys.path.insert(0, '.')
from app.orchestrator import RateLimiter, QuotaExhaustedError  # noqa: E402
from app.models import AttackType  # noqa: E402


async def main():
    rl = RateLimiter(global_rps=10000, global_pps=50000, global_concurrent=20000)

    # 场景A: 同一节点并发两场 HTTP 攻击 (旧实现第二场覆盖第一场)
    q1 = await rl.allocate('atk-aaa', 'node-1', AttackType.HTTP_FLOOD, 6000, 1000)
    q2 = await rl.allocate('atk-bbb', 'node-1', AttackType.HTTP_FLOOD, 6000, 1000)
    usage = rl.get_usage()
    assert q2['rps'] == 4000, f'second attack should cap at remaining: {q2}'
    assert usage['used_rps'] == 10000, f'quotas must accumulate: {usage}'
    print(f'CONCURRENT ATTACKS OK: atk-aaa={q1["rps"]}rps atk-bbb={q2["rps"]}rps total={usage["used_rps"]}')

    # 第三场应触发配额耗尽
    try:
        await rl.allocate('atk-ccc', 'node-1', AttackType.HTTP_FLOOD, 1000, 100)
        raise AssertionError('should have raised QuotaExhaustedError')
    except QuotaExhaustedError:
        print('QUOTA EXHAUST OK')

    # 停掉第一场: 只回收它的量, 第二场不受影响
    await rl.release_attack('atk-aaa')
    usage = rl.get_usage()
    assert usage['used_rps'] == 4000, usage
    print(f'STOP SINGLE ATTACK OK: remaining={usage["used_rps"]}rps')

    # 熔断清空全部
    await rl.release_all()
    assert rl.get_usage()['used_rps'] == 0
    print('EMERGENCY RELEASE_ALL OK')


asyncio.run(main())
print('RATE LIMITER TEST PASSED')
