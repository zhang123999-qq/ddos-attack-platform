"""v1.5.0 新增: Controller 内置 mini-CA — Node 端 mTLS 完整链路

设计目标:
- Controller 充当 mini-CA, 在 /api/v1/nodes/enroll 时直接签发 node 客户端证书
- 消除 v1.4.x "NODE_USE_TLS=false 内网 HTTP" 嗅探风险
- CA 私钥落盘 /var/lib/ddos-controller/ca/ (chmod 700, 私钥 chmod 600)
- 节点证书 1 年有效 (DAYS_VALID_NODE), CA 2 年 (DAYS_VALID_CA)
- 支持吊销列表 (CRL) - v1.5.0 暂存内存, 后续可落盘
"""
from __future__ import annotations

import datetime
import os
import secrets
import threading
from pathlib import Path
from typing import Optional, Tuple

import structlog

logger = structlog.get_logger(__name__)

# 延迟导入 cryptography, 避免冷启动期依赖问题
_crypto_cache: dict = {}


def _import_crypto():
    if not _crypto_cache:
        from cryptography import x509
        from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        _crypto_cache.update({
            "x509": x509,
            "NameOID": NameOID,
            "ExtendedKeyUsageOID": ExtendedKeyUsageOID,
            "hashes": hashes,
            "serialization": serialization,
            "rsa": rsa,
        })
    return _crypto_cache


class CertAuthority:
    """Controller 内置 mini-CA (单例)

    用法:
        ca = CertAuthority(ca_dir=Path("/var/lib/ddos-controller/ca"))
        ca.bootstrap_if_needed()
        cert_pem, key_pem = ca.issue_node_cert(node_id, node_ip)
    """

    DEFAULT_CA_DIR = "/var/lib/ddos-controller/ca"
    DAYS_VALID_CA = 730
    DAYS_VALID_NODE = 365
    CA_KEY_FILE = "ca-key.pem"
    CA_CERT_FILE = "ca-cert.pem"

    def __init__(self, ca_dir: Optional[Path] = None):
        # 解析 ca_dir: 优先级 显式参数 > 环境变量 > 默认 Linux 路径 > 用户 home 兜底
        if ca_dir is not None:
            self.ca_dir = Path(ca_dir)
        else:
            env_dir = os.getenv("CA_STORAGE_DIR")
            if env_dir:
                self.ca_dir = Path(env_dir)
            elif os.name == "nt" or not os.path.isdir(self.DEFAULT_CA_DIR):
                # Windows 或 Linux 上默认路径不可用 → fallback 到用户 home
                self.ca_dir = Path.home() / ".ddos-controller" / "ca"
            else:
                self.ca_dir = Path(self.DEFAULT_CA_DIR)
        self._revoked: dict = {}
        self._lock = threading.RLock()
        self._ca_key = None
        self._ca_cert = None

    def bootstrap_if_needed(self) -> None:
        """如果 CA 私钥/证书不存在则生成, 否则加载。"""
        ca_key = self.ca_dir / self.CA_KEY_FILE
        ca_cert = self.ca_dir / self.CA_CERT_FILE
        try:
            self.ca_dir.mkdir(parents=True, exist_ok=True)
            self._safe_chmod(self.ca_dir, 0o700)
        except (PermissionError, OSError) as e:
            logger.warning("ca_dir_create_failed", path=str(self.ca_dir), error=str(e))
            return
        if ca_key.exists() and ca_cert.exists():
            self._load_existing(ca_key, ca_cert)
            return
        self._generate_new(ca_key, ca_cert)

    def _load_existing(self, key_path: Path, cert_path: Path) -> None:
        c = _import_crypto()
        with self._lock:
            try:
                self._ca_key = c["serialization"].load_pem_private_key(
                    key_path.read_bytes(), password=None
                )
                self._ca_cert = c["x509"].load_pem_x509_certificate(cert_path.read_bytes())
                # 兼容新旧版 cryptography 库的过期时间属性
                expires = (
                    self._ca_cert.not_valid_after_utc
                    if hasattr(self._ca_cert, "not_valid_after_utc")
                    else self._ca_cert.not_valid_after
                )
                logger.info("ca_loaded", cert=str(cert_path), expires=expires.isoformat())
            except Exception as e:
                logger.error("ca_load_failed", error=str(e))
                raise

    @staticmethod
    def _safe_chmod(path: Path, mode: int) -> None:
        """跨平台 chmod - Windows 不支持 Unix 模式位, 静默降级为 warning"""
        try:
            path.chmod(mode)
        except (OSError, NotImplementedError) as e:
            logger.warning("chmod_failed", path=str(path), mode=oct(mode), error=str(e))

    def _generate_new(self, key_path: Path, cert_path: Path) -> None:
        c = _import_crypto()
        with self._lock:
            logger.info("ca_generating_new", dir=str(self.ca_dir))
            self._ca_key = c["rsa"].generate_private_key(public_exponent=65537, key_size=4096)
            subject = c["x509"].Name([
                c["x509"].NameAttribute(c["NameOID"].COMMON_NAME, "DDoS Lab Node CA"),
                c["x509"].NameAttribute(c["NameOID"].ORGANIZATION_NAME, "Internal Security Team"),
                c["x509"].NameAttribute(c["NameOID"].ORGANIZATIONAL_UNIT_NAME, "Red Team Controller"),
            ])
            now = datetime.datetime.now(datetime.timezone.utc)
            self._ca_cert = (
                c["x509"].CertificateBuilder()
                .subject_name(subject)
                .issuer_name(subject)
                .public_key(self._ca_key.public_key())
                .serial_number(c["x509"].random_serial_number())
                .not_valid_before(now - datetime.timedelta(minutes=5))
                .not_valid_after(now + datetime.timedelta(days=self.DAYS_VALID_CA))
                .add_extension(c["x509"].BasicConstraints(ca=True, path_length=0), critical=True)
                .add_extension(
                    c["x509"].KeyUsage(
                        digital_signature=True, content_commitment=False,
                        key_encipherment=False, data_encipherment=False,
                        key_agreement=False, key_cert_sign=True,
                        crl_sign=True, encipher_only=False, decipher_only=False,
                    ), critical=True
                )
                .add_extension(
                    c["x509"].SubjectKeyIdentifier.from_public_key(self._ca_key.public_key()),
                    critical=False,
                )
                .sign(self._ca_key, c["hashes"].SHA256())
            )
            key_path.write_bytes(self._ca_key.private_bytes(
                encoding=c["serialization"].Encoding.PEM,
                format=c["serialization"].PrivateFormat.PKCS8,
                encryption_algorithm=c["serialization"].NoEncryption(),
            ))
            self._safe_chmod(key_path, 0o600)
            cert_path.write_bytes(self._ca_cert.public_bytes(c["serialization"].Encoding.PEM))
            self._safe_chmod(cert_path, 0o644)
            logger.info("ca_generated",
                        cert=str(cert_path), key=str(key_path),
                        expires_days=self.DAYS_VALID_CA)

    def get_ca_cert_pem(self) -> bytes:
        """返回 CA 证书 PEM (用于 enroll 时下发到节点)"""
        c = _import_crypto()
        return self._ca_cert.public_bytes(c["serialization"].Encoding.PEM)

    def issue_node_cert(
        self,
        node_id: str,
        node_ip: Optional[str] = None,
        node_dns: Optional[str] = None,
        validity_days: Optional[int] = None,
    ):
        """为节点签发客户端证书

        返回 (cert_pem, key_pem)
        - SAN: 始终含 DNS:node_id; 可选 IP:node_ip
        - extendedKeyUsage: clientAuth
        - 有效期: 默认 DAYS_VALID_NODE (365 天)
        """
        c = _import_crypto()
        if self._ca_cert is None or self._ca_key is None:
            # 兜底: 懒加载 (避免 import 顺序问题)
            self.bootstrap_if_needed()
            if self._ca_cert is None:
                raise RuntimeError(
                    f"CA not bootstrapped (ca_dir={self.ca_dir}, "
                    f"check permissions or set CA_STORAGE_DIR)"
                )
        if validity_days is None:
            validity_days = self.DAYS_VALID_NODE
        with self._lock:
            node_key = c["rsa"].generate_private_key(public_exponent=65537, key_size=2048)
            subject = c["x509"].Name([
                c["x509"].NameAttribute(c["NameOID"].COMMON_NAME, node_id),
                c["x509"].NameAttribute(c["NameOID"].ORGANIZATION_NAME, "Internal Security Team"),
                c["x509"].NameAttribute(c["NameOID"].ORGANIZATIONAL_UNIT_NAME, "Red Team Attacker"),
            ])
            san_entries = [c["x509"].DNSName(node_id)]
            if node_dns and node_dns != node_id:
                san_entries.append(c["x509"].DNSName(node_dns))
            if node_ip:
                try:
                    import ipaddress as _ip
                    ip_obj = _ip.ip_address(node_ip)  # 验证 + 类型判定
                    # cryptography 用统一的 IPAddress 类 (传入 ipaddress.IPv4Address/IPv6Address)
                    san_entries.append(c["x509"].IPAddress(ip_obj))
                except ValueError:
                    logger.warning("invalid_node_ip_san_skipped", ip=node_ip, node_id=node_id)

            now = datetime.datetime.now(datetime.timezone.utc)
            # 序列号: 时间戳 + 4 字节随机 (避免碰撞, 便于审计追溯)
            serial = int(now.timestamp()) * (1 << 32) + secrets.randbelow(1 << 32)
            try:
                cert = (
                    c["x509"].CertificateBuilder()
                    .subject_name(subject)
                    .issuer_name(self._ca_cert.subject)
                    .public_key(node_key.public_key())
                    .serial_number(serial)
                    .not_valid_before(now - datetime.timedelta(minutes=5))
                    .not_valid_after(now + datetime.timedelta(days=validity_days))
                    .add_extension(c["x509"].BasicConstraints(ca=False, path_length=None), critical=True)
                    .add_extension(
                        c["x509"].KeyUsage(
                            digital_signature=True, content_commitment=True,
                            key_encipherment=True, data_encipherment=False,
                            key_agreement=False, key_cert_sign=False,
                            crl_sign=False, encipher_only=False, decipher_only=False,
                        ), critical=True
                    )
                    .add_extension(
                        c["x509"].ExtendedKeyUsage([c["ExtendedKeyUsageOID"].CLIENT_AUTH]),
                        critical=True,
                    )
                    .add_extension(c["x509"].SubjectAlternativeName(san_entries), critical=False)
                    .add_extension(
                        c["x509"].SubjectKeyIdentifier.from_public_key(node_key.public_key()),
                        critical=False,
                    )
                    .sign(self._ca_key, c["hashes"].SHA256())
                )
            except Exception as e:
                logger.error("node_cert_sign_failed", node_id=node_id, error=str(e))
                raise

            cert_pem = cert.public_bytes(c["serialization"].Encoding.PEM)
            key_pem = node_key.private_bytes(
                encoding=c["serialization"].Encoding.PEM,
                format=c["serialization"].PrivateFormat.PKCS8,
                encryption_algorithm=c["serialization"].NoEncryption(),
            )
            logger.info("node_cert_issued",
                        node_id=node_id, serial=serial,
                        validity_days=validity_days,
                        san_count=len(san_entries))
            return cert_pem, key_pem

    def revoke(self, serial: int) -> None:
        """吊销证书 (内存记录, v1.5.0 简化实现)"""
        with self._lock:
            self._revoked[serial] = datetime.datetime.now(datetime.timezone.utc)
            logger.warning("node_cert_revoked", serial=serial)

    def is_revoked(self, serial: int) -> bool:
        with self._lock:
            return serial in self._revoked


# 全局单例
cert_authority = CertAuthority()

