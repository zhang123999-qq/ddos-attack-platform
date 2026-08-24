from __future__ import annotations

import os
import ssl
import hashlib
import hmac
import secrets
from typing import Optional, Tuple
from pathlib import Path
from fastapi import Request, HTTPException, Depends, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import structlog

from app.models import NodeInfo

logger = structlog.get_logger(__name__)

security = HTTPBearer(auto_error=False)


class AuthConfig:
    # C-4 修复: 弱密钥黑名单前缀
    INSECURE_PREFIXES = ("changeme", "insecure-default")

    def __init__(self):
        self.shared_secret = os.getenv("SHARED_SECRET", "").encode()
        self.ca_cert_path = os.getenv("TLS_CA_FILE", "/certs/ca-cert.pem")
        self.cert_path = os.getenv("TLS_CERT_FILE", "/certs/controller-cert.pem")
        self.key_path = os.getenv("TLS_KEY_FILE", "/certs/controller-key.pem")
        # 是否强制双向认证 (客户端证书)。
        # 默认 false: 仅服务端 TLS, 浏览器可直接访问 Web UI/REST;
        # 置 true 后节点必须出示 CA 签发的证书 (严格 mTLS), 但浏览器将被拒绝。
        self.verify_client = os.getenv("TLS_VERIFY_CLIENT", "false").lower() == "true"

        if not self.shared_secret:
            logger.warning("SHARED_SECRET not set, using insecure default")
            self.shared_secret = b"insecure-default-change-me-32chars"

        # 启动时校验密钥长度
        if len(self.shared_secret) < 32:
            logger.warning("SHARED_SECRET too short, minimum 32 bytes recommended")

        # C-4 加固: REQUIRE_SHARED_SECRET=true 时拒绝弱/默认密钥 (生产部署镜像默认开启)
        raw = os.getenv("SHARED_SECRET", "")
        require = os.getenv("REQUIRE_SHARED_SECRET", "false").lower() == "true"
        if require and (
            not raw
            or len(raw) < 32
            or any(raw.startswith(p) for p in self.INSECURE_PREFIXES)
        ):
            logger.error(
                "weak_shared_secret_rejected",
                hint="set SHARED_SECRET to >=32 random chars (openssl rand -hex 32)",
            )
            raise SystemExit(1)

    def create_ssl_context(self, purpose: ssl.Purpose = ssl.Purpose.CLIENT_AUTH) -> ssl.SSLContext:
        """创建 mTLS SSL 上下文"""
        context = ssl.create_default_context(purpose)
        context.verify_mode = ssl.CERT_REQUIRED if self.verify_client else ssl.CERT_NONE
        context.load_cert_chain(self.cert_path, self.key_path)
        context.load_verify_locations(self.ca_cert_path)
        # 仅允许 TLS 1.2+
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.set_ciphers("ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20")
        return context

    def verify_token(self, token: str) -> bool:
        """验证预共享 Token (HMAC-SHA256) — Controller API 调用者用"""
        expected = hmac.new(self.shared_secret, b"ddos-controller-auth", hashlib.sha256).hexdigest()
        return hmac.compare_digest(token, expected)

    def generate_node_token(self, node_id: str) -> str:
        """为节点生成专用 Token (HMAC-SHA256 with node_id)"""
        return hmac.new(self.shared_secret, node_id.encode(), hashlib.sha256).hexdigest()

    def verify_node_token(self, node_id: str, token: str) -> bool:
        """验证节点上报的 Token"""
        expected = self.generate_node_token(node_id)
        return hmac.compare_digest(token, expected)

    # ========== CRIT-6 修复：Controller → Attacker 指令下发 Token ==========

    def generate_controller_cmd_token(self) -> str:
        """Controller 下发指令给 Attacker 时使用的 Token"""
        return hmac.new(self.shared_secret, b"ddos-controller-cmd", hashlib.sha256).hexdigest()

    # ========== 一键安装 : 无状态 enroll token ==========

    ENROLL_DOMAIN = b"ddos-enroll:"

    def _enroll_bucket(self, dt=None, offset_hours: int = 0) -> str:
        """小时桶: UTC %Y%m%d%H — token 自然过期, 无需服务端存储"""
        from datetime import datetime, timedelta, timezone
        dt = dt or datetime.now(timezone.utc)
        if offset_hours:
            dt = dt + timedelta(hours=offset_hours)
        return dt.strftime("%Y%m%d%H")

    def generate_enroll_token(self, node_id: str, bucket: Optional[str] = None) -> str:
        """为指定节点派生 enroll token (绑定 node_id + 小时桶, 防跨节点挪用)"""
        bucket = bucket or self._enroll_bucket()
        msg = self.ENROLL_DOMAIN + f"{node_id}:{bucket}".encode()
        return hmac.new(self.shared_secret, msg, hashlib.sha256).hexdigest()

    def verify_enroll_token(self, node_id: str, token: str) -> bool:
        """校验 enroll token: 接受当前小时桶与上一小时桶 (边界平滑, 最长约 2h 有效)"""
        for bucket in (self._enroll_bucket(), self._enroll_bucket(offset_hours=-1)):
            expected = self.generate_enroll_token(node_id, bucket)
            if hmac.compare_digest(token, expected):
                return True
        return False

    def get_tls_fingerprint(self) -> str:
        """控制器证书 SHA-256 指纹 (冒号分隔大写十六进制), 供节点安装器钉扎校验;
        证书文件不可读时返回空串"""
        try:
            import cryptography.x509 as x509
            from cryptography.hazmat.primitives import serialization
            data = Path(self.cert_path).read_bytes()
            cert = x509.load_pem_x509_certificate(data)
            der = cert.public_bytes(serialization.Encoding.DER)
            digest = hashlib.sha256(der).hexdigest().upper()
            return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))
        except Exception:
            # cryptography 缺失或证书不存在 — 降级为对文件原样字节哈希
            try:
                digest = hashlib.sha256(Path(self.cert_path).read_bytes()).hexdigest().upper()
                return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))
            except Exception:
                return ""

    def generate_controller_auth_headers(self, target_node_id: str) -> dict:
        """生成 Controller 调用 Attacker API 的认证头"""
        return {
            "X-Node-ID": target_node_id,
            "X-Node-Token": self.generate_controller_cmd_token(),
        }


auth_config = AuthConfig()


async def verify_controller_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> str:
    """验证 Controller API 调用者的 Token (管理员/教学控制台)"""
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    token = credentials.credentials
    if not auth_config.verify_token(token):
        logger.warning("controller_auth_failure", token_prefix=token[:8] if len(token) > 8 else token)
        raise HTTPException(status_code=401, detail="Invalid token")

    # MED-1 修复：返回 Token 中编码的身份标识
    return "authenticated_user"


async def verify_node_token(
    request: Request,
    x_node_id: str = Header(..., alias="X-Node-ID"),
    x_node_token: str = Header(..., alias="X-Node-Token"),
) -> NodeInfo:
    """验证攻击节点的身份 (mTLS + Token 双重认证)"""
    # 1. 验证 mTLS 客户端证书 (由反向代理/SSL层完成)
    # 2. 验证预共享 Token
    if not auth_config.verify_node_token(x_node_id, x_node_token):
        logger.warning("node_auth_failure", node_id=x_node_id)
        raise HTTPException(status_code=401, detail="Invalid node credentials")

    # 返回基本节点信息
    return NodeInfo(
        node_id=x_node_id,
        ip=request.client.host if request.client else "unknown",
        hostname=x_node_id,
        cpu_cores=0,
        memory_gb=0,
    )


def create_client_ssl_context(ca_file: str, cert_file: str, key_file: str) -> ssl.SSLContext:
    """为节点创建连接 Controller 的 mTLS 客户端上下文"""
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_cert_chain(cert_file, key_file)
    context.load_verify_locations(ca_file)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers("ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20")
    return context