from __future__ import annotations

import os
import ssl
import hmac
import hashlib
from pathlib import Path
from typing import Optional
import structlog

logger = structlog.get_logger(__name__)


class NodeCrypto:
    """节点端 mTLS + Token 管理"""
    
    # 弱密钥黑名单前缀 (C-4 修复)
    INSECURE_PREFIXES = ("changeme", "insecure-default")

    def _is_weak_secret(self, raw: str) -> bool:
        if len(raw) < 32:
            return True
        return any(raw.startswith(p) for p in self.INSECURE_PREFIXES)

    def __init__(self):
        self.ca_cert = os.getenv("CONTROLLER_CA_CERT", "/certs/ca-cert.pem")
        self.node_cert = os.getenv("NODE_CERT", "/certs/node-cert.pem")
        self.node_key = os.getenv("NODE_KEY", "/certs/node-key.pem")
        self.controller_url = os.getenv("CONTROLLER_URL", "https://10.100.1.10:8443")
        self.node_id = os.getenv("NODE_ID", "unknown-node")
        self.node_token = os.getenv("NODE_TOKEN", "")
        self.shared_secret = os.getenv("SHARED_SECRET", "").encode()

        if not self.shared_secret:
            logger.warning("shared_secret_not_set_using_insecure_default")
            self.shared_secret = b"insecure-default-change-me-32chars"

        # C-4 加固: REQUIRE_SHARED_SECRET=true 时拒绝弱密钥启动 (生产/部署镜像默认开启)
        if os.getenv("REQUIRE_SHARED_SECRET", "false").lower() == "true":
            raw = os.getenv("SHARED_SECRET", "")
            if not raw or self._is_weak_secret(raw):
                logger.error(
                    "weak_shared_secret_rejected",
                    hint="set SHARED_SECRET to >=32 random chars (openssl rand -hex 32)",
                )
                raise SystemExit(1)
    
    def create_ssl_context(self) -> ssl.SSLContext:
        """创建连接 Controller 的 TLS 客户端上下文。

        双模式:
        - mTLS (NODE_CERT/NODE_KEY 存在): 客户端证书双向认证
        - 服务端验证 (仅 CONTROLLER_CA_CERT): enroll 安装器的默认发放物,
          TLS 加密 + 校验控制器身份, 客户端不出证 — 与 node-install.sh 一致
        """
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        if Path(self.node_cert).exists() and Path(self.node_key).exists():
            context.load_cert_chain(self.node_cert, self.node_key)
        elif os.getenv("NODE_CERT") or os.getenv("NODE_KEY"):
            # 显式配置了客户端证书但文件缺失 — 拒绝启动而非静默降级
            logger.error("node_cert_configured_but_missing",
                         cert=self.node_cert, key=self.node_key)
            raise SystemExit(1)
        context.verify_mode = ssl.CERT_REQUIRED
        if not Path(self.ca_cert).exists():
            logger.error("controller_ca_missing", path=self.ca_cert)
            raise SystemExit(1)
        context.load_verify_locations(self.ca_cert)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.set_ciphers("ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20")
        context.check_hostname = False  # 内网 IP 直连，不验证 hostname
        return context
    
    def get_node_token(self) -> str:
        """获取节点认证 Token"""
        if self.node_token:
            return self.node_token
        # 派生 Token: HMAC-SHA256(shared_secret, node_id)
        return hmac.new(self.shared_secret, self.node_id.encode(), hashlib.sha256).hexdigest()
    
    def verify_controller_token(self, token: str) -> bool:
        """验证 Controller 下发指令的 Token (可选，双向认证已由 mTLS 保证)"""
        expected = hmac.new(self.shared_secret, b"ddos-controller-cmd", hashlib.sha256).hexdigest()
        return hmac.compare_digest(token, expected)
    
    def get_auth_headers(self) -> dict:
        """获取 HTTP 请求的认证头"""
        return {
            "X-Node-ID": self.node_id,
            "X-Node-Token": self.get_node_token(),
        }
    
    def validate_cert_files(self) -> bool:
        """验证证书文件存在 — CA 必需; 客户端证书仅在显式要求 mTLS 时必需"""
        if not Path(self.ca_cert).exists():
            logger.error("cert_file_missing", path=self.ca_cert)
            return False
        # 未显式配置 NODE_CERT/NODE_KEY 时走服务端验证模式, 不强制客户端证书
        if os.getenv("NODE_CERT") or os.getenv("NODE_KEY"):
            for path in [self.node_cert, self.node_key]:
                if not Path(path).exists():
                    logger.error("cert_file_missing", path=path)
                    return False
        return True


node_crypto = NodeCrypto()