# -*- coding: utf-8 -*-
"""一键安装全链路 E2E: 真实 HTTPS 启动 → install.sh 分发 → controller-info 指纹
→ enroll 换密钥 → CA 分发 → enroll-command(管理员) — 验证 Komari 式接入闭环"""
import hashlib
import hmac as _hmac
import os
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CERT_DIR = ROOT / "tests" / "_tmp_certs"
CERT_DIR.mkdir(parents=True, exist_ok=True)
CERT = CERT_DIR / "server-cert.pem"
KEY = CERT_DIR / "server-key.pem"
PORT = 9444
SECRET = "e2e-install-flow-secret-32chars-abc123"

_insecure_ctx = ssl.create_default_context()
_insecure_ctx.check_hostname = False
_insecure_ctx.verify_mode = ssl.CERT_NONE


def get(path, timeout=10):
    with urllib.request.urlopen(f"https://127.0.0.1:{PORT}{path}", context=_insecure_ctx, timeout=timeout) as r:
        return r.status, r.read()


def main():
    # 复用 tls_e2e 的证书生成器
    sys.path.insert(0, str(Path(__file__).parent))
    from test_tls_e2e import gen_self_signed
    gen_self_signed()
    print("1/7 SELF-SIGNED CERT GENERATED")

    env = dict(os.environ)
    env.update({
        "TLS_CERT_FILE": str(CERT),
        "TLS_KEY_FILE": str(KEY),
        "TLS_CA_FILE": str(CERT),  # 自签场景 CA=自身 (与 controller-install.sh 行为一致)
        "SHARED_SECRET": SECRET,
        "CONTROLLER_PORT": str(PORT),
        "ALLOWED_TARGET_CIDRS": "127.0.0.0/8,10.100.0.0/16",
        "AUDIT_LOG_PATH": str(CERT_DIR / "audit-install.jsonl"),
        "ENABLE_WEB_UI": "true",
        "LOG_LEVEL": "error",
    })
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.main"], cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(40):
            time.sleep(0.5)
            try:
                get("/health", timeout=3)
                break
            except Exception:
                continue
        else:
            raise AssertionError("controller never became healthy")
        print("2/7 CONTROLLER UP OVER HTTPS")

        # controller-info: 公开元信息 + 指纹与证书 DER 摘要一致 (openssl 同语义)
        st, body = get("/api/v1/controller-info")
        assert st == 200
        import json
        info = json.loads(body)
        from cryptography import x509 as _x509
        from cryptography.hazmat.primitives import serialization as _ser
        _cert = _x509.load_pem_x509_certificate(CERT.read_bytes())
        cert_der_fp = hashlib.sha256(_cert.public_bytes(_ser.Encoding.DER)).hexdigest()
        declared = info["tls_fingerprint"].replace(":", "").lower()
        assert declared == cert_der_fp, f"fingerprint mismatch {declared} vs {cert_der_fp}"
        assert info["install_script_available"] is True
        print("3/7 CONTROLLER-INFO OK (fingerprint matches cert DER)")

        # install.sh 分发 + 占位符替换
        st, script = get("/install.sh")
        assert st == 200 and b"__CONTROLLER_URL__" not in script
        assert f"https://127.0.0.1:{PORT}".encode() in script
        print("4/7 INSTALL.SH SERVED (placeholder substituted)")

        # enroll 错误 token → 403
        req = urllib.request.Request(
            f"https://127.0.0.1:{PORT}/api/v1/nodes/enroll",
            data=json.dumps({"node_id": "e2e-node", "enroll_token": "f" * 64}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, context=_insecure_ctx, timeout=10)
            raise AssertionError("bad token must fail")
        except urllib.error.HTTPError as e:
            assert e.code == 403, e.code
        print("5/7 ENROLL REJECTS BAD TOKEN (403)")

        # enroll 正确 token → 返回配置且密钥与服务端一致
        msg = f"ddos-enroll:e2e-node:{time.strftime('%Y%m%d%H', time.gmtime())}".encode()
        token = _hmac.new(SECRET.encode(), msg, hashlib.sha256).hexdigest()
        req = urllib.request.Request(
            f"https://127.0.0.1:{PORT}/api/v1/nodes/enroll",
            data=json.dumps({"node_id": "e2e-node", "enroll_token": token}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, context=_insecure_ctx, timeout=10) as r:
            payload = json.loads(r.read())
        assert payload["shared_secret"] == SECRET
        assert "10.100.0.0/16" in payload["allowed_target_cidrs"]
        assert payload["ca_cert_url"].endswith("/artifacts/ca-cert.pem")
        print("6/7 ENROLL ROUNDTRIP OK (secret delivered over TLS)")

        # CA 分发内容 == 服务端证书
        st, ca = get("/artifacts/ca-cert.pem")
        assert st == 200 and ca.strip() == CERT.read_bytes().strip()
        print("7/7 CA DISTRIBUTION MATCHES SERVER CERT")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print("INSTALL-FLOW E2E PASSED")


if __name__ == "__main__":
    main()
