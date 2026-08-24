# =============================================================================
# CRIT-1 修复: Controller → Attacker HTTP 指令下发
# 替代原来的空 _node_senders 回调机制
# =============================================================================

import asyncio
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

    async def start(self):
        """创建可复用的 HTTP 客户端"""
        # 内网环境，可以跳过完整的 TLS 验证 (mTLS 由 nginx/envoy 终结时)
        limits = httpx.Limits(max_connections=200, max_keepalive_connections=50)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=limits,
            verify=False,  # 内网环境，mTLS 由 SSL 反向代理处理
        )

    async def stop(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    def register_node(self, node_id: str, node_ip: str, node_port: int = 8080):
        """注册节点通信地址"""
        base_url = f"http://{node_ip}:{node_port}"
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