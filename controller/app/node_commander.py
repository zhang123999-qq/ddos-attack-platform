# =============================================================================
# CRIT-1 修复: Controller → Attacker HTTP 指令下发
# 替代原来的空 _node_senders 回调机制
#
# v1.4.0 (TD-1 修复): 默认启用 HTTPS + TLS 校验, 由 NODE_INSECURE_PLAIN_HTTP=true
# 显式 opt-out; NODE_TLS_CA_FILE 指向 Controller CA (与节点 TLS_CA_FILE 一致),
# 校验 Attacker 节点出示的证书由该 CA 签发。可选 NODE_TLS_CERT_FILE/KEY_FILE
# 提供 mTLS 双向认证。移除 v1.3.x 永久 verify=False 的隐患 (实际链路走 HTTP
# 明文 + X-Node-Token 鉴权, 攻击者在内网 sniff 即可获得 token)。
# =============================================================================

import asyncio
import os
import ssl
import httpx
from typing import Dict, Optional
import structlog

from app.auth import auth_config

logger = structlog.get_logger(__name__)


class NodeCommander:
    """
    Controller 到 Attacker 节点的 HTTP 指令下发器
    通过 mTLS + Token 调用每个 Attacker 节点的 REST API
    """

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._nodes: Dict[str, str] = {}  # node_id -> base_url
        self._scheme: str = "https"  # 默认 HTTPS, NODE_INSECURE_PLAIN_HTTP=true 时 http
        self._node_port: int = int(os.getenv("NODE_PORT", "8080"))

    def _build_ssl_context(self) -> Optional[ssl.SSLContext]:
        """为 httpx 客户端构造 SSL 上下文 (Controller→Node 出站)。

        - 无 NODE_TLS_CA_FILE: 不启用 TLS (走明文 HTTP, 仅当 NODE_INSECURE_PLAIN_HTTP=true 允许)
        - 有 NODE_TLS_CA_FILE: 强制 TLS + 校验 Node 证书由该 CA 签发
        - 可选 NODE_TLS_CERT_FILE/NODE_TLS_KEY_FILE: mTLS 双向认证 (复用 Controller 自身证书)
        """
        ca_file = os.getenv("NODE_TLS_CA_FILE", "")
        if not ca_file:
            return None
        if not os.path.isfile(ca_file):
            logger.error("node_tls_ca_file_missing", path=ca_file,
                         hint="set NODE_TLS_CA_FILE to controller CA cert path, "
                              "or NODE_INSECURE_PLAIN_HTTP=true for legacy HTTP")
            raise RuntimeError(f"NODE_TLS_CA_FILE not found: {ca_file}")
        ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
        ctx.load_verify_locations(ca_file)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.set_ciphers("ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20")
        ctx.check_hostname = False  # 内网 IP 直连, 不验证 hostname (与 Node 端一致)
        cert_file = os.getenv("NODE_TLS_CERT_FILE", os.getenv("TLS_CERT_FILE", ""))
        key_file = os.getenv("NODE_TLS_KEY_FILE", os.getenv("TLS_KEY_FILE", ""))
        if cert_file and key_file:
            if not (os.path.isfile(cert_file) and os.path.isfile(key_file)):
                logger.error("node_mtls_cert_or_key_missing",
                             cert=cert_file, key=key_file)
                raise RuntimeError("NODE_TLS_CERT_FILE/KEY_FILE configured but missing")
            ctx.load_cert_chain(cert_file, key_file)
            logger.info("node_commander_mtls_enabled",
                        ca=ca_file, cert=cert_file)
        else:
            logger.info("node_commander_tls_server_auth_only", ca=ca_file)
        return ctx

    async def start(self):
        """创建可复用的 HTTP 客户端 (TD-1 修复)"""
        insecure = os.getenv("NODE_INSECURE_PLAIN_HTTP", "false").lower() == "true"
        banned = os.getenv("NODE_PLAIN_HTTP_BANNED", "false").lower() == "true"

        ssl_ctx = self._build_ssl_context()
        if ssl_ctx is None:
            if banned:
                logger.error("plain_http_banned_by_policy",
                             hint="set NODE_TLS_CA_FILE to enable HTTPS, "
                                  "or unset NODE_PLAIN_HTTP_BANNED for legacy")
                raise RuntimeError("NODE_PLAIN_HTTP_BANNED=true but NODE_TLS_CA_FILE not set")
            if insecure:
                logger.warning("node_commander_plain_http_enabled",
                               hint="set NODE_TLS_CA_FILE + NODE_TLS_CERT_FILE/KEY_FILE "
                                    "for production; NODE_PLAIN_HTTP_BANNED=true to enforce")
            else:
                # 默认 fail-closed: 没有 CA 配置直接退出
                logger.error("node_tls_required_but_not_configured",
                             hint="set NODE_TLS_CA_FILE to controller CA, "
                                  "or NODE_INSECURE_PLAIN_HTTP=true to explicitly allow HTTP")
                raise RuntimeError(
                    "Controller→Node TLS required. Set NODE_TLS_CA_FILE=<controller CA> "
                    "or NODE_INSECURE_PLAIN_HTTP=true (legacy only)."
                )
            self._scheme = "http"
        else:
            self._scheme = "https"

        limits = httpx.Limits(max_connections=200, max_keepalive_connections=50)
        client_kwargs = dict(
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=limits,
        )
        if ssl_ctx is not None:
            client_kwargs["verify"] = ssl_ctx
        # v1.4.0 (TD-1): 移除永久 verify=False, 显式 fail-closed

        self._client = httpx.AsyncClient(**client_kwargs)
        logger.info("node_commander_started", scheme=self._scheme,
                    port=self._node_port, tls=ssl_ctx is not None)

    async def stop(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    def register_node(self, node_id: str, node_ip: str, node_port: int = 8080):
        """注册节点通信地址"""
        base_url = f"{self._scheme}://{node_ip}:{node_port}"
        self._nodes[node_id] = base_url
        logger.info("node_commander_registered", node_id=node_id, base_url=base_url)

    def unregister_node(self, node_id: str):
        self._nodes.pop(node_id, None)

    async def send_attack_command(self, node_id: str, command: dict) -> bool:
        """向指定节点下发攻击指令"""
        base_url = self._nodes.get(node_id)
        if not base_url:
            logger.error("node_not_reachable", node_id=node_id)
            return False

        headers = auth_config.generate_controller_auth_headers(node_id)
        headers["Content-Type"] = "application/json"

        try:
            resp = await self._client.post(
                f"{base_url}/api/v1/attacks/execute",
                json=command,
                headers=headers,
            )
            resp.raise_for_status()
            logger.info("command_sent", node_id=node_id, attack_id=command.get("attack_id"))
            return True
        except httpx.HTTPError as e:
            logger.error("command_send_failed", node_id=node_id, error=str(e))
            return False

    async def send_stop_command(self, node_id: str, attack_id: str) -> bool:
        """向指定节点下发停止指令"""
        base_url = self._nodes.get(node_id)
        if not base_url:
            return False

        headers = auth_config.generate_controller_auth_headers(node_id)
        try:
            resp = await self._client.post(
                f"{base_url}/api/v1/attacks/{attack_id}/stop",
                headers=headers,
            )
            resp.raise_for_status()
            logger.info("stop_sent", node_id=node_id, attack_id=attack_id)
            return True
        except httpx.HTTPError as e:
            logger.error("stop_send_failed", node_id=node_id, error=str(e))
            return False

    async def send_emergency_stop(self, node_id: str, reason: str, issued_by: str) -> bool:
        """向指定节点下发紧急熔断"""
        base_url = self._nodes.get(node_id)
        if not base_url:
            return False

        headers = auth_config.generate_controller_auth_headers(node_id)
        headers["Content-Type"] = "application/json"
        try:
            resp = await self._client.post(
                f"{base_url}/api/v1/emergency_stop",
                json={"reason": reason, "issued_by": issued_by},
                headers=headers,
            )
            resp.raise_for_status()
            logger.info("emergency_stop_sent", node_id=node_id, reason=reason)
            return True
        except httpx.HTTPError as e:
            logger.error("emergency_stop_send_failed", node_id=node_id, error=str(e))
            return False

    async def broadcast_emergency_stop(self, reason: str, issued_by: str) -> int:
        """向所有已知节点广播紧急熔断，返回成功发送数量"""
        tasks = []
        for node_id in list(self._nodes.keys()):
            tasks.append(self.send_emergency_stop(node_id, reason, issued_by))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return sum(1 for r in results if r is True)

    async def send_emergency_reset(self, node_id: str) -> bool:
        """向指定节点下发熔断复位"""
        base_url = self._nodes.get(node_id)
        if not base_url:
            return False
        headers = auth_config.generate_controller_auth_headers(node_id)
        try:
            resp = await self._client.post(
                f"{base_url}/api/v1/emergency_stop/reset",
                headers=headers,
            )
            resp.raise_for_status()
            logger.info("emergency_reset_sent", node_id=node_id)
            return True
        except httpx.HTTPError as e:
            logger.error("emergency_reset_send_failed", node_id=node_id, error=str(e))
            return False

    async def broadcast_emergency_reset(self) -> int:
        """向所有已知节点广播熔断复位 (P1-1: 原先只有 Controller 本地复位, 节点永久锁死)"""
        tasks = [self.send_emergency_reset(node_id) for node_id in list(self._nodes.keys())]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return sum(1 for r in results if r is True)

    def has_node(self, node_id: str) -> bool:
        return node_id in self._nodes


# 全局实例
node_commander = NodeCommander()