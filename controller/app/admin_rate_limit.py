"""v1.5.0 新增: Admin API 限流器 (R-NEW-2 降级方案)

目标: 防止 admin token 泄露/暴力试错下, 攻击 launch/emergency_stop 端点被滥用

设计:
- 令牌桶 (asyncio.Lock 串行化)
- 限流范围: 所有 /api/v1/attacks/* / /api/v1/scenarios/* / /api/v1/nodes/* 写入端点
- 桶容量 60, 恢复速率 1 token/s (即 60 RPM)
- 触发限流时:
  1. 立即返回 429 (含 Retry-After header)
  2. 增加 admin_rate_limited_total 指标
  3. 写 audit 事件 (actor=client_ip, details=path)
- 不区分用户 (RBAC 暂未实施, 共享桶); 后续 RBAC 接入可改为 per-user

未来扩展:
- 引入 user identity 后改为 (user, scope) 双键桶
- 引入 IP 白名单 (内部服务调用不限)
"""
from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class _Bucket:
    """单作用域令牌桶"""
    capacity: int
    refill_per_sec: float
    tokens: float
    last_refill: float

    def try_consume(self, n: int = 1) -> tuple[bool, float]:
        """尝试消费 n 个 token, 返回 (ok, retry_after_seconds)"""
        now = time.monotonic()
        elapsed = now - self.last_refill
        # 恢复 (不超过 capacity)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_sec)
        self.last_refill = now
        if self.tokens >= n:
            self.tokens -= n
            return True, 0.0
        # 不足, 计算需等待多久才能再消费 1 个
        deficit = n - self.tokens
        wait = deficit / self.refill_per_sec
        return False, wait


class AdminAPIRateLimiter:
    """Controller admin 端点限流器 (单例)"""

    def __init__(self, capacity=None, refill_per_sec=None):
        """capacity=60 tokens, refill 1/s = 60 RPM 默认上限

        显式传入 capacity 优先 (测试场景); 否则读 ADMIN_RATE_LIMIT_RPM 环境变量 (0=禁用)
        """
        import os
        rpm_env = os.getenv("ADMIN_RATE_LIMIT_RPM")
        if capacity is None:
            capacity = int(rpm_env) if rpm_env else 60
        if capacity <= 0:
            self.capacity = 0  # 0 = 禁用
            self.refill_per_sec = 0.0
        else:
            self.capacity = capacity
            self.refill_per_sec = (refill_per_sec if refill_per_sec is not None
                                    else capacity / 60.0)
        self._buckets: dict = {}  # scope -> _Bucket
        # 使用 threading.Lock (try_consume 是同步调用, 跨 asyncio.run 兼容)
        self._lock = threading.Lock()
        # 指标计数器 (延迟到 metrics 模块加载)
        self._metric_limited = None
        self._metric_blocked = None

    def _get_metric(self):
        """延迟获取 metric 引用 (避免循环依赖)"""
        if self._metric_limited is None:
            try:
                from app.metrics import (
                    ADMIN_RATE_LIMITED_TOTAL, ADMIN_RATE_LIMIT_BLOCKED_TOTAL
                )
                self._metric_limited = ADMIN_RATE_LIMITED_TOTAL
                self._metric_blocked = ADMIN_RATE_LIMIT_BLOCKED_TOTAL
            except Exception:
                pass
        return self._metric_limited, self._metric_blocked

    async def check(self, scope: str, cost: int = 1) -> tuple[bool, float]:
        """检查 scope 是否允许 cost 个 token

        返回: (allowed, retry_after)
        - allowed=True: 已扣 token
        - allowed=False: 拒绝, retry_after 秒后可重试
        """
        if self.capacity == 0:
            return True, 0.0
        # threading.Lock + try_consume 同步, 用 asyncio.to_thread 避免阻塞 event loop
        return await asyncio.to_thread(self._check_sync, scope, cost)

    def _check_sync(self, scope: str, cost: int) -> tuple[bool, float]:
        with self._lock:
            now = time.monotonic()
            if scope not in self._buckets:
                self._buckets[scope] = _Bucket(
                    capacity=self.capacity,
                    refill_per_sec=self.refill_per_sec,
                    tokens=self.capacity,
                    last_refill=now,
                )
            bucket = self._buckets[scope]
            ok, wait = bucket.try_consume(cost)
            if not ok:
                _, blocked_metric = self._get_metric()
                if blocked_metric is not None:
                    try:
                        blocked_metric.labels(scope=scope).inc()
                    except Exception:
                        pass
            return ok, wait

    async def check_or_raise(self, scope: str, cost: int = 1) -> None:
        """FastAPI 依赖版本: 限流失败抛 HTTPException(429)"""
        from fastapi import HTTPException
        ok, retry_after = await self.check(scope, cost)
        if not ok:
            limited_metric, _ = self._get_metric()
            if limited_metric is not None:
                try:
                    limited_metric.labels(scope=scope).inc()
                except Exception:
                    pass
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded for {scope}. Retry after {retry_after:.1f}s",
                headers={"Retry-After": str(int(retry_after) + 1)},
            )

    def check_sync(self, scope: str, cost: int = 1) -> tuple[bool, float]:
        """同步入口 — 测试与非异步上下文用 (如 pytest 跨 event loop 边界)"""
        if self.capacity == 0:
            return True, 0.0
        return self._check_sync(scope, cost)

    def get_stats(self) -> dict:
        """返回当前所有 scope 的桶状态 (供 /metrics 或调试)"""
        return {
            scope: {
                "capacity": b.capacity,
                "tokens": round(b.tokens, 2),
                "refill_per_sec": b.refill_per_sec,
            }
            for scope, b in self._buckets.items()
        }


# 全局单例
admin_rate_limiter = AdminAPIRateLimiter()
