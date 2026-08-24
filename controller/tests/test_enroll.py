# -*- coding: utf-8 -*-
"""一键安装引导测试: enroll token 推导/校验、enroll 端点流、命令生成端点"""
import os
import sys
import time

os.environ.setdefault("SHARED_SECRET", "test-secret-32chars-abcdef1234567890")
os.environ.setdefault("ENABLE_WEB_UI", "true")
os.environ.setdefault("LOG_LEVEL", "error")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.auth import auth_config  # noqa: E402

client = TestClient(app)


def test_enroll_token_deterministic_and_bound():
    t1 = auth_config.generate_enroll_token("node-a")
    t2 = auth_config.generate_enroll_token("node-a")
    assert t1 == t2, "same node+bucket must derive identical token"
    assert auth_config.verify_enroll_token("node-a", t1) is True
    # 绑定 node_id: 拿 node-a 的 token 冒充 node-b 必须拒绝
    assert auth_config.verify_enroll_token("node-b", t1) is False
    assert auth_config.verify_enroll_token("node-a", "f" * 64) is False
    print("ENROLL TOKEN DETERMINISM/BINDING OK")


def test_enroll_token_prev_bucket_accepted():
    """上一小时桶仍有效 (边界平滑), 两天前失效"""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    prev = (now - timedelta(hours=1)).strftime("%Y%m%d%H")
    old = (now - timedelta(hours=48)).strftime("%Y%m%d%H")
    t_prev = auth_config.generate_enroll_token("n1", bucket=prev)
    t_old = auth_config.generate_enroll_token("n1", bucket=old)
    assert auth_config.verify_enroll_token("n1", t_prev) is True
    assert auth_config.verify_enroll_token("n1", t_old) is False
    print("ENROLL TOKEN HOUR-BUCKET EXPIRY OK")


def test_controller_info_endpoint():
    r = client.get("/api/v1/controller-info")
    assert r.status_code == 200
    data = r.json()
    for key in ("service", "version", "tls_fingerprint", "artifacts"):
        assert key in data, f"missing {key}"
    print(f"CONTROLLER-INFO OK (fp={'present' if data['tls_fingerprint'] else 'no-cert'})")


def test_enroll_flow_success():
    nid = "enrolled-node-01"
    token = auth_config.generate_enroll_token(nid)
    r = client.post("/api/v1/nodes/enroll",
                    json={"node_id": nid, "enroll_token": token})
    assert r.status_code == 200
    data = r.json()
    assert data["shared_secret"], "must return shared secret over TLS"
    assert data["ca_cert_url"].endswith("/artifacts/ca-cert.pem")
    print("ENROLL SUCCESS FLOW OK")


def test_enroll_rejects_bad_token_and_bad_node_id():
    r = client.post("/api/v1/nodes/enroll",
                    json={"node_id": "evil-node", "enroll_token": "f" * 64})
    assert r.status_code == 403
    # 非法 node_id 字符
    token = auth_config.generate_enroll_token("ok-id")
    r = client.post("/api/v1/nodes/enroll",
                    json={"node_id": "../etc/passwd", "enroll_token": token})
    assert r.status_code == 400
    print("ENROLL REJECTION OK (bad token 403 / bad id 400)")


def test_enroll_command_requires_auth():
    r = client.get("/api/v1/nodes/enroll-command?type=http&node_id=x1")
    assert r.status_code in (401, 403), "must require controller bearer"
    print("ENROLL-COMMAND AUTH GATE OK")


def test_enroll_command_generation():
    # 注意: 与服务端同源取密钥 (auth_config 在首次导入时固化), 避免跨测试文件 env 污染
    import hmac as _hmac
    import hashlib as _hashlib
    admin_token = _hmac.new(auth_config.shared_secret, b"ddos-controller-auth", _hashlib.sha256).hexdigest()

    r = client.get("/api/v1/nodes/enroll-command?type=raw&node_id=attacker-raw-09",
                   headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    cmd = r.json()["data"]["command"]
    assert cmd.startswith("bash <(curl"), cmd
    assert "--type raw" in cmd and "--id attacker-raw-09" in cmd
    assert "-t " in cmd
    print(f"ENROLL COMMAND GEN OK: {cmd[:70]}...")


def test_install_script_served_with_substitution():
    """脚本存在时必须分发且替换 __CONTROLLER_URL__; 不存在则 404 (CI 环境两态均可)"""
    from app.main import INSTALL_SCRIPT
    r = client.get("/install.sh")
    if INSTALL_SCRIPT:
        assert r.status_code == 200
        assert "__CONTROLLER_URL__" not in r.text, "placeholder must be substituted"
        assert "ddos-node" in r.text
    else:
        assert r.status_code == 404
    print("INSTALL SCRIPT SERVE OK")


if __name__ == "__main__":
    test_enroll_token_deterministic_and_bound()
    test_enroll_token_prev_bucket_accepted()
    test_controller_info_endpoint()
    test_enroll_flow_success()
    test_enroll_rejects_bad_token_and_bad_node_id()
    test_enroll_command_requires_auth()
    test_enroll_command_generation()
    test_install_script_served_with_substitution()
    print("ALL ENROLL TESTS PASSED")
