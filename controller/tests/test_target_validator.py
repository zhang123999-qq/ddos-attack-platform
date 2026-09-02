"""v1.5.0 新增: TargetValidator 专项测试

覆盖:
- IP/CIDR 匹配 (含 IPv6)
- 域名解析 (mock getaddrinfo, 避免真实 DNS 依赖)
- 占位符拒绝
- ALLOW_ANY_TARGET 显式 opt-out
- 留空白名单 + 未 opt-out → fail-closed
- 无效 CIDR 配置的容忍 (invalid_cidr_ignored 警告而非崩)
- is_allowed_sync 用于已解析 IP 场景
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ratelimit import TargetValidator
from app.models import TargetSpec


def test_ip_in_whitelist():
    tv = TargetValidator(["10.100.0.0/16", "192.168.0.0/16"])
    assert asyncio.run(tv.is_allowed(TargetSpec(ip="10.100.5.5"))) is True
    assert asyncio.run(tv.is_allowed(TargetSpec(ip="192.168.1.1"))) is True
    assert asyncio.run(tv.is_allowed(TargetSpec(ip="172.16.0.1"))) is False
    print("IP IN WHITELIST OK")


def test_ip_outside_whitelist_blocked():
    tv = TargetValidator(["10.100.0.0/16"])
    assert asyncio.run(tv.is_allowed(TargetSpec(ip="8.8.8.8"))) is False
    assert asyncio.run(tv.is_allowed(TargetSpec(ip="1.1.1.1"))) is False
    assert asyncio.run(tv.is_allowed(TargetSpec(ip="203.0.113.5"))) is False
    print("IP OUTSIDE WHITELIST BLOCKED OK")


def test_ipv6_supported():
    tv = TargetValidator(["fd00::/8"])
    assert asyncio.run(tv.is_allowed(TargetSpec(ip="fd00::1"))) is True
    assert asyncio.run(tv.is_allowed(TargetSpec(ip="fd12:3456:789a::1"))) is True
    assert asyncio.run(tv.is_allowed(TargetSpec(ip="2001:db8::1"))) is False
    print("IPV6 SUPPORTED OK")


def test_placeholder_always_rejected():
    """占位符是 hard rule: 即使 ALLOW_ANY_TARGET=true 也拒绝"""
    tv = TargetValidator(["10.100.0.0/16"], allow_any=True)
    assert asyncio.run(tv.is_allowed(TargetSpec(ip="TARGET_IP_PLACEHOLDER"))) is False
    tv2 = TargetValidator(["10.100.0.0/16"], allow_any=False)
    assert asyncio.run(tv2.is_allowed(TargetSpec(ip="ANY_HOST_PLACEHOLDER"))) is False
    print("PLACEHOLDER ALWAYS REJECTED OK")


def test_allow_any_target_opt_out():
    tv = TargetValidator(["10.100.0.0/16"], allow_any=True)
    assert asyncio.run(tv.is_allowed(TargetSpec(ip="8.8.8.8"))) is True
    assert asyncio.run(tv.is_allowed(TargetSpec(ip="2001:db8::1"))) is True
    print("ALLOW_ANY_TARGET OPT-OUT OK")


def test_empty_whitelist_fails_closed():
    """留空白名单 + 未 opt-out: 拒绝所有 (fail-closed)"""
    tv = TargetValidator([])
    assert asyncio.run(tv.is_allowed(TargetSpec(ip="10.100.5.5"))) is False
    assert asyncio.run(tv.is_allowed(TargetSpec(ip="8.8.8.8"))) is False
    print("EMPTY WHITELIST FAIL-CLOSED OK")


def test_invalid_cidr_ignored():
    """无效 CIDR 配置应被记录警告, 不应让整个 validator 崩"""
    tv = TargetValidator(["not-a-cidr", "10.100.0.0/16", "999.999.999.0/24"])
    # 无效条目被忽略, 10.100.0.0/16 仍生效
    assert asyncio.run(tv.is_allowed(TargetSpec(ip="10.100.5.5"))) is True
    assert asyncio.run(tv.is_allowed(TargetSpec(ip="8.8.8.8"))) is False
    print("INVALID CIDR IGNORED OK")


def test_domain_resolution_match():
    """域名解析后任一 A 记录命中白名单即放行"""
    tv = TargetValidator(["10.100.0.0/16"])
    loop = asyncio.new_event_loop()
    try:
        with patch.object(type(loop), "getaddrinfo",
                          new=AsyncMock(return_value=[(0, 0, 0, 0, ("10.100.7.7", 0))])):
            assert asyncio.run(tv.is_allowed(TargetSpec(ip="example.com"))) is True
    finally:
        loop.close()
    print("DOMAIN RESOLUTION MATCH OK")


def test_domain_resolution_miss():
    tv = TargetValidator(["10.100.0.0/16"])
    loop = asyncio.new_event_loop()
    try:
        with patch.object(type(loop), "getaddrinfo",
                          new=AsyncMock(return_value=[(0, 0, 0, 0, ("8.8.8.8", 0))])):
            assert asyncio.run(tv.is_allowed(TargetSpec(ip="evil.example.com"))) is False
    finally:
        loop.close()
    print("DOMAIN RESOLUTION MISS OK")


def test_domain_resolution_failure_blocked():
    """DNS 解析失败: 默认拒绝 (防止任意域名白嫖)"""
    tv = TargetValidator(["10.100.0.0/16"])
    loop = asyncio.new_event_loop()
    try:
        with patch.object(type(loop), "getaddrinfo",
                          new=AsyncMock(side_effect=OSError("DNS down"))):
            assert asyncio.run(tv.is_allowed(TargetSpec(ip="example.com"))) is False
    finally:
        loop.close()
    print("DOMAIN RESOLUTION FAILURE BLOCKED OK")


def test_sync_path_for_already_resolved():
    """is_allowed_sync: 跳过 DNS, 用于节点侧已经解析过的 IP"""
    tv = TargetValidator(["10.100.0.0/16"])
    assert tv.is_allowed_sync(TargetSpec(ip="10.100.5.5")) is True
    assert tv.is_allowed_sync(TargetSpec(ip="8.8.8.8")) is False
    print("SYNC PATH OK")


def test_cidr_target_overlaps():
    """目标为 CIDR 时, 与白名单 CIDR 重叠即放行"""
    tv = TargetValidator(["10.100.0.0/16"])
    assert asyncio.run(tv.is_allowed(TargetSpec(ip="10.100.5.0/24"))) is True
    # 不重叠
    assert asyncio.run(tv.is_allowed(TargetSpec(ip="172.16.0.0/12"))) is False
    print("CIDR TARGET OVERLAPS OK")


if __name__ == "__main__":
    test_ip_in_whitelist()
    test_ip_outside_whitelist_blocked()
    test_ipv6_supported()
    test_placeholder_always_rejected()
    test_allow_any_target_opt_out()
    test_empty_whitelist_fails_closed()
    test_invalid_cidr_ignored()
    test_domain_resolution_match()
    test_domain_resolution_miss()
    test_domain_resolution_failure_blocked()
    test_sync_path_for_already_resolved()
    test_cidr_target_overlaps()
    print("\nALL 12 TARGET VALIDATOR TESTS PASSED")
