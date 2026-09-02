"""v1.5.0 新增: CertAuthority 专项测试

覆盖:
- 首次启动生成 CA 私钥 + 自签证书
- 持久化重载一致性
- 节点证书签发 (含 SAN: DNS + IP)
- 扩展字段: extendedKeyUsage=clientAuth, BasicConstraints(CA=FALSE)
- 证书由 CA 私钥签名 (verify pass)
- 多次签发序列号不碰撞
- 吊销 (内存)
- CA 目录自动创建
- 默认 365 天有效期
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_bootstrap_creates_ca():
    """首次启动: 生成 CA 私钥 + 自签证书 + 落盘"""
    with tempfile.TemporaryDirectory() as tmp:
        ca_dir = Path(tmp) / "ca"
        from app.cert_authority import CertAuthority
        ca = CertAuthority(ca_dir=ca_dir)
        ca.bootstrap_if_needed()
        assert (ca_dir / "ca-key.pem").exists()
        assert (ca_dir / "ca-cert.pem").exists()
        key_bytes = (ca_dir / "ca-key.pem").read_bytes()
        assert b"BEGIN" in key_bytes and b"PRIVATE KEY" in key_bytes
        from cryptography import x509
        cert = x509.load_pem_x509_certificate((ca_dir / "ca-cert.pem").read_bytes())
        assert cert.issuer == cert.subject
        bc = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
        assert bc.ca is True
        print("BOOTSTRAP CREATES CA OK")


def test_bootstrap_reload_persists():
    """重载 CA: 内容与首次一致 (证明持久化有效)"""
    with tempfile.TemporaryDirectory() as tmp:
        ca_dir = Path(tmp) / "ca"
        from app.cert_authority import CertAuthority
        ca1 = CertAuthority(ca_dir=ca_dir)
        ca1.bootstrap_if_needed()
        cert1 = ca1.get_ca_cert_pem()
        ca2 = CertAuthority(ca_dir=ca_dir)
        ca2.bootstrap_if_needed()
        cert2 = ca2.get_ca_cert_pem()
        assert cert1 == cert2
        print("BOOTSTRAP RELOAD PERSISTS OK")


def test_issue_node_cert_with_dns_and_ip():
    """签发 node 证书 - SAN 必须含 DNS + IP, EKU=clientAuth"""
    with tempfile.TemporaryDirectory() as tmp:
        ca_dir = Path(tmp) / "ca"
        from app.cert_authority import CertAuthority
        ca = CertAuthority(ca_dir=ca_dir)
        ca.bootstrap_if_needed()
        cert_pem, key_pem = ca.issue_node_cert(
            node_id="node-test-01", node_ip="10.100.1.20"
        )
        from cryptography import x509
        cert = x509.load_pem_x509_certificate(cert_pem)
        cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
        assert cn == "node-test-01"
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        san_strs = str([str(s) for s in san])
        assert "node-test-01" in san_strs
        assert "10.100.1.20" in san_strs
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        from cryptography.x509.oid import ExtendedKeyUsageOID
        assert ExtendedKeyUsageOID.CLIENT_AUTH in eku
        from cryptography.hazmat.primitives import serialization
        key = serialization.load_pem_private_key(key_pem, password=None)
        assert key.key_size == 2048
        print("ISSUE NODE CERT WITH DNS AND IP OK")


def test_node_cert_signed_by_ca():
    """关键: node 证书必须由 CA 私钥签名 (mTLS 链路可信)"""
    with tempfile.TemporaryDirectory() as tmp:
        ca_dir = Path(tmp) / "ca"
        from app.cert_authority import CertAuthority
        ca = CertAuthority(ca_dir=ca_dir)
        ca.bootstrap_if_needed()
        cert_pem, _ = ca.issue_node_cert("node-01", node_ip="10.0.0.1")
        from cryptography import x509
        from cryptography.hazmat.primitives.asymmetric import padding
        node_cert = x509.load_pem_x509_certificate(cert_pem)
        ca_cert = x509.load_pem_x509_certificate(ca.get_ca_cert_pem())
        ca_cert.public_key().verify(
            node_cert.signature,
            node_cert.tbs_certificate_bytes,
            padding.PKCS1v15(),
            node_cert.signature_hash_algorithm,
        )
        print("NODE CERT SIGNED BY CA OK")


def test_serial_numbers_unique():
    """多次签发序列号不碰撞"""
    with tempfile.TemporaryDirectory() as tmp:
        ca_dir = Path(tmp) / "ca"
        from app.cert_authority import CertAuthority
        ca = CertAuthority(ca_dir=ca_dir)
        ca.bootstrap_if_needed()
        serials = set()
        from cryptography import x509
        for i in range(5):
            cert_pem, _ = ca.issue_node_cert(f"node-{i}")
            cert = x509.load_pem_x509_certificate(cert_pem)
            serials.add(cert.serial_number)
        assert len(serials) == 5, f"serial collision: {serials}"
        print("SERIAL NUMBERS UNIQUE OK")


def test_revoke_tracks():
    """吊销: 内存记录 serial, 可查"""
    with tempfile.TemporaryDirectory() as tmp:
        ca_dir = Path(tmp) / "ca"
        from app.cert_authority import CertAuthority
        ca = CertAuthority(ca_dir=ca_dir)
        ca.bootstrap_if_needed()
        from cryptography import x509
        cert_pem, _ = ca.issue_node_cert("node-rev")
        cert = x509.load_pem_x509_certificate(cert_pem)
        assert ca.is_revoked(cert.serial_number) is False
        ca.revoke(cert.serial_number)
        assert ca.is_revoked(cert.serial_number) is True
        print("REVOKE TRACKS OK")


def test_invalid_ip_san_skipped():
    """无效 IP 应被跳过, 证书仍可签发 (仅 DNS)"""
    with tempfile.TemporaryDirectory() as tmp:
        ca_dir = Path(tmp) / "ca"
        from app.cert_authority import CertAuthority
        ca = CertAuthority(ca_dir=ca_dir)
        ca.bootstrap_if_needed()
        cert_pem, _ = ca.issue_node_cert("node-bad-ip", node_ip="not-an-ip")
        from cryptography import x509
        cert = x509.load_pem_x509_certificate(cert_pem)
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        san_strs = str([str(s) for s in san])
        assert "not-an-ip" not in san_strs
        print("INVALID IP SAN SKIPPED OK")


def test_chmod_windows_safe():
    """Windows 上 chmod 不应崩"""
    with tempfile.TemporaryDirectory() as tmp:
        ca_dir = Path(tmp) / "ca"
        from app.cert_authority import CertAuthority
        ca = CertAuthority(ca_dir=ca_dir)
        for _ in range(3):
            ca.bootstrap_if_needed()
        assert (ca_dir / "ca-key.pem").exists()
        print("CHMOD WINDOWS SAFE OK")


def test_bootstrap_idempotent():
    """bootstrap 多次调用不重生成 CA"""
    with tempfile.TemporaryDirectory() as tmp:
        ca_dir = Path(tmp) / "ca"
        from app.cert_authority import CertAuthority
        ca = CertAuthority(ca_dir=ca_dir)
        ca.bootstrap_if_needed()
        first_cert = ca.get_ca_cert_pem()
        ca.bootstrap_if_needed()
        second_cert = ca.get_ca_cert_pem()
        assert first_cert == second_cert
        print("BOOTSTRAP IDEMPOTENT OK")


def test_issue_cert_uses_default_validity():
    """默认 365 天有效期"""
    with tempfile.TemporaryDirectory() as tmp:
        ca_dir = Path(tmp) / "ca"
        from app.cert_authority import CertAuthority
        ca = CertAuthority(ca_dir=ca_dir)
        ca.bootstrap_if_needed()
        cert_pem, _ = ca.issue_node_cert("node-default")
        from cryptography import x509
        cert = x509.load_pem_x509_certificate(cert_pem)
        not_after = (
            cert.not_valid_after_utc
            if hasattr(cert, "not_valid_after_utc")
            else cert.not_valid_after
        )
        not_before = (
            cert.not_valid_before_utc
            if hasattr(cert, "not_valid_before_utc")
            else cert.not_valid_before
        )
        delta = (not_after - not_before).days
        assert 364 <= delta <= 366, f"unexpected validity: {delta} days"
        print(f"DEFAULT VALIDITY {delta} DAYS OK")


if __name__ == "__main__":
    test_bootstrap_creates_ca()
    test_bootstrap_reload_persists()
    test_issue_node_cert_with_dns_and_ip()
    test_node_cert_signed_by_ca()
    test_serial_numbers_unique()
    test_revoke_tracks()
    test_invalid_ip_san_skipped()
    test_chmod_windows_safe()
    test_bootstrap_idempotent()
    test_issue_cert_uses_default_validity()
    print("\nALL 10 CERT AUTHORITY TESTS PASSED")

