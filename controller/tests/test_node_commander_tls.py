# -*- coding: utf-8 -*-
"""v1.4.0 (TD-1) 修复测试: NodeCommander TLS 默认开启, fail-closed

验证:
- 默认 (无 env) 必须 fail-closed (RuntimeError)
- NODE_INSECURE_PLAIN_HTTP=true 显式 opt-out → http
- NODE_TLS_CA_FILE 指向 CA → https
- NODE_PLAIN_HTTP_BANNED=true + 无 CA → 强制 fail-closed
"""
import asyncio
import os
import sys

# v1.4.0: env 必须在 import app.* 之前设置 (auth_config / node_commander 都是单例)
os.environ.setdefault("SHARED_SECRET", "test-secret-32chars-abcdef1234567890")
os.environ.setdefault("LOG_LEVEL", "error")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _clear_tls_env():
    for k in ("NODE_TLS_CA_FILE", "NODE_TLS_CERT_FILE", "NODE_TLS_KEY_FILE",
              "NODE_INSECURE_PLAIN_HTTP", "NODE_PLAIN_HTTP_BANNED", "NODE_PORT"):
        os.environ.pop(k, None)


def test_default_fail_closed():
    """v1.4.0 (TD-1): 无任何 env 配置 → 必须 fail-closed (旧行为: 静默允许 HTTP)"""
    _clear_tls_env()
    from app.node_commander import NodeCommander
    nc = NodeCommander()
    try:
        asyncio.run(nc.start())
        raise AssertionError("default must fail-closed without NODE_TLS_CA_FILE or NODE_INSECURE_PLAIN_HTTP")
    except RuntimeError as e:
        assert "TLS required" in str(e) or "NODE_TLS_CA_FILE" in str(e), f"unexpected error: {e}"
        print("PASS: default fail-closed (TD-1)")
    finally:
        asyncio.run(nc.stop())


def test_explicit_insecure_opt_out():
    """v1.4.0: NODE_INSECURE_PLAIN_HTTP=true 显式 opt-out → http + WARN"""
    _clear_tls_env()
    os.environ["NODE_INSECURE_PLAIN_HTTP"] = "true"
    from app.node_commander import NodeCommander
    nc = NodeCommander()
    asyncio.run(nc.start())
    assert nc._scheme == "http", f"scheme={nc._scheme}, expected http"
    nc.register_node("test", "127.0.0.1", 8080)
    assert nc._nodes["test"] == "http://127.0.0.1:8080"
    asyncio.run(nc.stop())
    print("PASS: NODE_INSECURE_PLAIN_HTTP=true → http (TD-1)")


def test_ca_file_enables_https():
    """v1.4.0: NODE_TLS_CA_FILE 指向 CA → https + 服务端证书验证"""
    _clear_tls_env()
    # 复用 _tmp_certs (test_install_flow_e2e 生成)
    cert_dir = os.path.join(os.path.dirname(__file__), "_tmp_certs")
    ca = os.path.join(cert_dir, "server-cert.pem")  # 自签测试用 cert 当 CA
    if not os.path.isfile(ca):
        print(f"SKIP: test cert not found at {ca} (run test_install_flow_e2e first)")
        return
    os.environ["NODE_TLS_CA_FILE"] = ca
    from app.node_commander import NodeCommander
    nc = NodeCommander()
    asyncio.run(nc.start())
    assert nc._scheme == "https", f"scheme={nc._scheme}, expected https"
    nc.register_node("test", "127.0.0.1", 8080)
    assert nc._nodes["test"] == "https://127.0.0.1:8080"
    asyncio.run(nc.stop())
    print("PASS: NODE_TLS_CA_FILE → https (TD-1)")


def test_banned_without_ca_fails_closed():
    """v1.4.0: NODE_PLAIN_HTTP_BANNED=true + 无 CA → 拒绝启动"""
    _clear_tls_env()
    os.environ["NODE_PLAIN_HTTP_BANNED"] = "true"
    from app.node_commander import NodeCommander
    nc = NodeCommander()
    try:
        asyncio.run(nc.start())
        raise AssertionError("NODE_PLAIN_HTTP_BANNED=true without CA must fail-closed")
    except RuntimeError as e:
        assert "BANNED" in str(e) or "NODE_TLS_CA_FILE" in str(e), f"unexpected error: {e}"
        print("PASS: NODE_PLAIN_HTTP_BANNED → fail-closed (TD-1)")


def test_missing_ca_file_raises():
    """v1.4.0: NODE_TLS_CA_FILE 指向不存在的文件 → 拒绝启动 (避免静默降级)"""
    _clear_tls_env()
    os.environ["NODE_TLS_CA_FILE"] = "/nonexistent/ca-cert.pem"
    from app.node_commander import NodeCommander
    nc = NodeCommander()
    try:
        asyncio.run(nc.start())
        raise AssertionError("missing CA file must fail-closed")
    except RuntimeError as e:
        assert "not found" in str(e).lower(), f"unexpected error: {e}"
        print("PASS: missing CA file → fail-closed (TD-1)")


if __name__ == "__main__":
    test_default_fail_closed()
    test_explicit_insecure_opt_out()
    test_ca_file_enables_https()
    test_banned_without_ca_fails_closed()
    test_missing_ca_file_raises()
    print("\nALL TD-1 NODE COMMANDER TLS TESTS PASSED")
else:
    # pytest 入口
    def test_td1_default_fail_closed():
        test_default_fail_closed()

    def test_td1_explicit_insecure_opt_out():
        test_explicit_insecure_opt_out()

    def test_td1_ca_file_enables_https():
        test_ca_file_enables_https()

    def test_td1_banned_without_ca_fails_closed():
        test_banned_without_ca_fails_closed()

    def test_td1_missing_ca_file_raises():
        test_missing_ca_file_raises()

    # v1.4.1-hotfix6 (REG-7 测试污染): 上面的测试会 set NODE_TLS_CA_FILE=/nonexistent
    # 泄漏到后续 test, 导致其他测试 (如 test_registry_fixes) 启动失败.
    # 加 fixture-like 清理: 每个 test 后重置为 HTTP 模式
    def test_cleanup_node_env():
        """重置 NODE_TLS_* 和 NODE_INSECURE_PLAIN_HTTP, 让其他测试可运行"""
        for k in ("NODE_TLS_CA_FILE", "NODE_TLS_CERT_FILE", "NODE_TLS_KEY_FILE",
                  "NODE_PLAIN_HTTP_BANNED"):
            os.environ.pop(k, None)
        os.environ["NODE_INSECURE_PLAIN_HTTP"] = "true"
        # 也重建 node_commander 单例 (之前测试可能半初始)
        from app.node_commander import node_commander
        import asyncio
        try:
            asyncio.run(node_commander.stop())
        except Exception:
            pass
        node_commander._client = None
        node_commander._scheme = None
        print("REG-7 test pollution cleanup: NODE_TLS_* cleared, HTTP mode restored")

