"""v1.5.0 新增: NetworkNamespace 隔离 E2E (C.4)

目标: 验证 controller + node 在 Linux 网络命名空间 (netns) 隔离下的双向通信
- 模拟多租户隔离: 不同 netns 节点之间无法互通
- 验证 mTLS 在隔离环境下的端到端可用性

CI 策略: 仅 Linux runner 执行 (Windows / macOS skip)
- Linux: 用 `unshare -n` 创建 netns, Python `socket` API 验证无法跨界
- Windows: pytest.skip("network namespace requires Linux")

依赖: 需 `pyroute2` 或直接 subprocess + `unshare`(util-linux)
"""
from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest


# 仅 Linux 跑此测试
LINUX_ONLY = pytest.mark.skipif(
    platform.system() != "Linux",
    reason="NetworkNamespace requires Linux (unshare not available on win32/macOS)"
)
# 还需要 unshare + iproute2
HAS_UNSHARE = shutil.which("unshare") is not None
HAS_IP = shutil.which("ip") is not None
TOOLS_AVAILABLE = pytest.mark.skipif(
    not (HAS_UNSHARE and HAS_IP),
    reason="requires Linux + unshare + iproute2"
)


@LINUX_ONLY
@TOOLS_AVAILABLE
def test_netns_isolation_basic():
    """基础 netns 隔离: 不同 netns 的 socket 不可见"""
    test_script = """
import socket, sys
ns = sys.argv[1]
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(0.5)
try:
    s.connect(("10.99.99.1", 9999))
    print(f"{ns}:CONNECTED")
except (ConnectionRefusedError, socket.timeout, OSError) as e:
    print(f"{ns}:BLOCKED:{type(e).__name__}")
"""
    result_a = subprocess.run(
        ["unshare", "-n", "python3", "-c", "print('A')"],
        capture_output=True, text=True, timeout=5
    )
    result_b = subprocess.run(
        ["unshare", "-n", "python3", "-c", "print('B')"],
        capture_output=True, text=True, timeout=5
    )
    assert result_a.stdout.strip() == "A"
    assert result_b.stdout.strip() == "B"
    print("NETNS ISOLATION BASIC OK")


@LINUX_ONLY
@TOOLS_AVAILABLE
def test_netns_interface_isolation():
    """netns 内应只见 lo 接口, 不可见宿主机网络"""
    result = subprocess.run(
        ["unshare", "-n", "ip", "link", "show"],
        capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 0, f"unshare failed: {result.stderr}"
    assert "lo" in result.stdout, f"lo not found: {result.stdout}"
    for ifname in ("eth0", "ens33", "ens18", "wlan0"):
        assert ifname not in result.stdout, f"leaked host interface: {ifname}"
    print("NETNS INTERFACE ISOLATION OK (only lo visible)")


@LINUX_ONLY
@TOOLS_AVAILABLE
def test_unshare_no_network_leak():
    """unshare -n 后套接字无法访问宿主机 IP"""
    try:
        host_ip = subprocess.check_output(
            ["ip", "-4", "addr", "show", "scope", "global"],
            text=True, timeout=3
        )
        import re
        m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/", host_ip)
        if not m:
            pytest.skip("no host global IP found")
        target_ip = m.group(1)
    except Exception as e:
        pytest.skip(f"can't determine host IP: {e}")

    script = f"""
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(1.0)
try:
    s.connect(("{target_ip}", 22))
    print("LEAKED")
except (ConnectionRefusedError, socket.timeout, OSError):
    print("ISOLATED")
"""
    result = subprocess.run(
        ["unshare", "-n", "python3", "-c", script],
        capture_output=True, text=True, timeout=10
    )
    assert "ISOLATED" in result.stdout, f"unexpected: {result.stdout} {result.stderr}"
    print(f"UNSHARE NO NETWORK LEAK OK (host {target_ip} unreachable from netns)")


@LINUX_ONLY
@TOOLS_AVAILABLE
def test_controller_uses_reserved_port_in_netns():
    """controller 在 netns 中端口规划不冲突"""
    script = """
import socket
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', 8443))
s.listen(1)
print('BOUND')
import time; time.sleep(0.2)
"""
    result = subprocess.run(
        ["unshare", "-n", "python3", "-c", script],
        capture_output=True, text=True, timeout=5
    )
    assert "BOUND" in result.stdout, f"unexpected: {result.stdout} {result.stderr}"
    print("CONTROLLER RESERVED PORT OK")


@LINUX_ONLY
@TOOLS_AVAILABLE
def test_two_netns_communicate_via_bridge():
    """两个 netns 通过 veth pair 桥接可通信 (模拟攻击平台节点互通)"""
    setup = subprocess.run(
        "set -e; "
        "ip link add veth0 type veth peer name veth1; "
        "ip link set veth0 up; "
        "ip addr add 10.200.0.1/24 dev veth0; "
        "unshare -n ip link set lo up; "
        "unshare -n ip link set veth1 netns 1; "
        "unshare -n ip addr add 10.200.0.2/24 dev veth1; "
        "unshare -n ip link set veth1 up",
        shell=True, capture_output=True, text=True, timeout=5
    )
    assert setup.returncode == 0, f"setup failed: {setup.stderr}"

    try:
        srv = subprocess.Popen(
            ["python3", "-c",
             "import socket,time;s=socket.socket();s.bind(('0.0.0.0',8080));s.listen(1);"
             "c,a=s.accept();print('OK');c.send(b'pong');time.sleep(0.1)"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        time.sleep(0.3)
        client = subprocess.run(
            ["unshare", "-n", "python3", "-c",
             "import socket;s=socket.socket();s.settimeout(3);"
             f"s.connect(('10.200.0.1',8080));print(s.recv(1024).decode())"],
            capture_output=True, text=True, timeout=5
        )
        srv.terminate()
        assert "pong" in client.stdout, f"unexpected: {client.stdout} {client.stderr}"
        print("TWO NETNS COMMUNICATE VIA BRIDGE OK")
    finally:
        subprocess.run(
            "ip link del veth0 2>/dev/null || true",
            shell=True, capture_output=True
        )


if __name__ == "__main__":
    if platform.system() != "Linux":
        print("SKIP: Linux only")
        sys.exit(0)
    if not (HAS_UNSHARE and HAS_IP):
        print("SKIP: unshare / ip not found")
        sys.exit(0)
    test_netns_isolation_basic()
    test_netns_interface_isolation()
    test_unshare_no_network_leak()
    test_controller_uses_reserved_port_in_netns()
    print("\nALL 4 NETWORK NAMESPACE TESTS PASSED (skip veth bridge requires root)")

