"""P0-1 端到端验证: 生成自签证书 → 真实启动 HTTPS uvicorn → 探测 /health"""
import os
import ssl
import sys
import time
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CERT_DIR = ROOT / "tests" / "_tmp_certs"
CERT_DIR.mkdir(parents=True, exist_ok=True)
CERT = CERT_DIR / "server-cert.pem"
KEY = CERT_DIR / "server-key.pem"
PORT = 9443


def gen_self_signed():
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import datetime

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256())
    )
    KEY.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    CERT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def ipaddress(s):
    import ipaddress as _m
    return _m.IPv4Address(s)


def main():
    gen_self_signed()
    print("SELF-SIGNED CERT GENERATED")

    env = dict(os.environ)
    env.update({
        "TLS_CERT_FILE": str(CERT),
        "TLS_KEY_FILE": str(KEY),
        "CONTROLLER_PORT": str(PORT),
        "AUDIT_LOG_PATH": str(CERT_DIR / "audit.jsonl"),
        # v1.4.0 (TD-1): 此测试只测 Controller 自身 TLS, 不涉及 Node 通信
        "NODE_INSECURE_PLAIN_HTTP": "true",
    })
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.main"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        # 等待服务就绪
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        body = None
        for _ in range(30):
            time.sleep(0.5)
            try:
                with urllib.request.urlopen(f"https://127.0.0.1:{PORT}/health", context=ctx, timeout=3) as r:
                    body = r.read().decode()
                    break
            except Exception:
                continue
        assert body is not None, "HTTPS /health never responded"
        assert '"healthy"' in body, body
        print(f"HTTPS HEALTH OK over TLS: {body[:80]}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
    print("TLS E2E TEST PASSED")
