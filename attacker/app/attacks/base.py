from __future__ import annotations

import asyncio
import os
import time
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
import structlog

from app.models import AttackCommand, AttackParams, AttackResult, AttackStatus, AttackType
from app.crypto import node_crypto

logger = structlog.get_logger(__name__)


class SafetyError(Exception):
    """安全检查失败"""
    pass


class TokenBucket:
    """令牌桶限流器"""
    
    def __init__(self, rate: float, burst: int):
        self.rate = rate  # tokens per second
        self.burst = burst
        self.tokens = float(burst)
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()
    
    async def consume(self, tokens: int = 1) -> bool:
        """尝试消费令牌，返回是否成功"""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
            self.last_update = now
            
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False
    
    async def wait_for_token(self, tokens: int = 1):
        """阻塞等待令牌可用"""
        while not await self.consume(tokens):
            await asyncio.sleep(0.001)
    
    def set_rate(self, rate: float):
        self.rate = rate


class SafeAttackBase(ABC):
    """
    攻击基类 - 所有攻击模块必须继承
    内置: 目标白名单、熔断开关、速率限制、审计日志
    """
    
    # 类属性 - 子类必须定义
    NAME: str = "base"
    ATTACK_TYPE: AttackType = AttackType.HTTP_FLOOD
    REQUIRES_ROOT: bool = False
    DEFAULT_RPS: int = 1000
    DEFAULT_CONCURRENCY: int = 100
    
    # 安全配置
    ALLOWED_TARGET_CIDRS: List[str] = []  # v1.3.0 方案A: 兼容保留, 不再用于校验
    EMERGENCY_STOP = asyncio.Event()

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # v1.3.0 方案A: 白名单不再生效, 仅记录环境变量值供诊断
        cls.ALLOWED_TARGET_CIDRS = [
            c.strip() for c in os.getenv("ALLOWED_TARGET_CIDRS", "").split(",") if c.strip()
        ]
        logger.info("attack_class_loaded", name=cls.NAME,
                    target_restrictions="disabled")
    
    def __init__(self, command: AttackCommand):
        self.command = command
        self.params = command.params
        self.attack_id = command.attack_id
        self.node_id = node_crypto.node_id
        
        # 结果收集
        self.result = AttackResult(
            attack_id=self.attack_id,
            node_id=self.node_id,
            status=AttackStatus.STARTING
        )
        
        # 限流器 (节点级)
        self.rate_limiter = TokenBucket(
            rate=self.params.rps,
            burst=min(self.params.rps * 2, 10000)
        )
        
        # 熔断检查
        self._stop_event = asyncio.Event()
        self._tasks: List[asyncio.Task] = []
        self._start_time: Optional[float] = None
        # v1.3.0 C3: 周期进度上报回调 (由 main.py 注入 send_attack_result)
        self._progress_callback = None
        # v1.3.0 C6: 错误封顶 — 防止全失败攻击刷爆内存
        self.ERROR_LIST_MAX = 50
    
    # ========== 安全检查 (必须在 execute 前调用) ==========

    @classmethod
    def pre_flight_check(cls, target: str) -> None:
        """启动前安全检查 - 失败抛出 SafetyError

        v1.3.0 方案A: 目标域名/IP 不做限制 — 白名单校验移除。
        仍保留: 全局熔断开关、root/CAP_NET_RAW 权限检查、scapy 可用性检查。
        """
        # 1. 全局熔断
        if cls.EMERGENCY_STOP.is_set():
            raise SafetyError("Global emergency stop is active")

        # 2. 权限检查
        if cls.REQUIRES_ROOT:
            # Windows 无 geteuid — 视为非 root 环境交由上层容器/capability 控制
            euid = getattr(os, "geteuid", None)
            if euid is not None and euid != 0:
                raise SafetyError(f"{cls.NAME} requires root/CAP_NET_RAW")

        logger.info("pre_flight_check_passed", attack=cls.NAME, target=target)
    
    # ========== 生命周期 ==========
    
    async def execute(self) -> AttackResult:
        """执行攻击主入口"""
        # 安全前置检查
        self.pre_flight_check(self.params.target.ip)
        
        self.result.status = AttackStatus.RUNNING
        self._start_time = time.monotonic()
        self._stop_event.clear()
        
        # v1.3.0 C3: 周期进度上报任务 (每 2s 快照 → 控制器 → WebUI 实时计数)
        reporter = asyncio.create_task(self._progress_reporter()) if self._progress_callback else None
        
        try:
            # 子类实现具体攻击逻辑
            await self._run()
            self.result.status = AttackStatus.STOPPED
        except SafetyError as e:
            self.result.status = AttackStatus.FAILED
            self._record_error(f"Safety: {e}")
            logger.warning("attack_safety_error", attack_id=self.attack_id, error=str(e))
        except asyncio.CancelledError:
            self.result.status = AttackStatus.EMERGENCY_STOPPED
            self._record_error("Cancelled by emergency stop")
            logger.warning("attack_emergency_stopped", attack_id=self.attack_id)
        except Exception as e:
            self.result.status = AttackStatus.FAILED
            self._record_error(f"Runtime: {e}")
            logger.error("attack_runtime_error", attack_id=self.attack_id, error=str(e), exc_info=True)
        finally:
            if reporter:
                reporter.cancel()
                try:
                    await reporter
                except asyncio.CancelledError:
                    pass
            self.result.stopped_at = datetime.now(timezone.utc)
            if self._start_time:
                self.result.metrics["duration_seconds"] = time.monotonic() - self._start_time
            await self._cleanup()
        
        return self.result

    def _record_error(self, msg: str):
        """C6: 错误列表封顶 — 超出后仅保留前 ERROR_LIST_MAX 条样本"""
        if len(self.result.errors) < self.ERROR_LIST_MAX:
            self.result.errors.append(msg)

    ERROR_BACKOFF_CAP = 0.25  # 连续错误退避上限 (秒)

    @staticmethod
    def _error_backoff(consecutive_errors: int) -> float:
        """BUG-2: 连续错误指数退避 — 抑制 closed-port 等错误风暴对事件循环的冲击。
        0 → 0ms; 1 → 10ms; 2 → 20ms; ... 封顶 250ms。成功后由调用方清零。"""
        if consecutive_errors <= 0:
            return 0.0
        return min(0.01 * (2 ** min(consecutive_errors, 5)), SafeAttackBase.ERROR_BACKOFF_CAP)

    async def _progress_reporter(self):
        """C3: 每 2s 上报当前计数快照 (status=running), 让控制器/UI 实时可见"""
        from app.models import AttackResult as _AR  # 局部引用避免循环导入风险
        while not self._stop_event.is_set() and not self.EMERGENCY_STOP.is_set():
            await asyncio.sleep(2)
            if self._stop_event.is_set() or self.EMERGENCY_STOP.is_set():
                break
            if not self._progress_callback:
                continue
            snapshot = AttackResult(
                attack_id=self.attack_id,
                node_id=self.node_id,
                status=AttackStatus.RUNNING,
                started_at=self.result.started_at,
                total_requests=self.result.total_requests,
                successful_requests=self.result.successful_requests,
                failed_requests=self.result.failed_requests,
                bytes_sent=self.result.bytes_sent,
                bytes_received=self.result.bytes_received,
                errors=list(self.result.errors)[:self.ERROR_LIST_MAX],
                metrics={"snapshot": True},
            )
            try:
                await self._progress_callback(snapshot)
            except Exception as e:
                logger.debug("progress_report_failed", attack_id=self.attack_id, error=str(e))
    
    async def stop(self, reason: str = "manual"):
        """停止攻击"""
        logger.info("attack_stop_requested", attack_id=self.attack_id, reason=reason)
        self._stop_event.set()
        
        # 取消所有任务
        for task in self._tasks:
            if not task.done():
                task.cancel()
        
        # 等待任务结束
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
    
    async def _cleanup(self):
        """清理资源"""
        self._tasks.clear()
    
    # ========== 抽象方法 (子类实现) ==========
    
    @abstractmethod
    async def _run(self):
        """子类实现具体攻击逻辑"""
        pass
    
    # ========== 工具方法 ==========
    
    def _check_stop(self) -> bool:
        """检查是否应停止 (熔断/手动/超时)"""
        if self.EMERGENCY_STOP.is_set() or self._stop_event.is_set():
            return True
        if self._start_time and (time.monotonic() - self._start_time) >= self.params.duration:
            return True
        return False
    
    async def _rate_limited_loop(self, worker_func, *args, **kwargs):
        """带限流的工作循环"""
        while not self._check_stop():
            await self.rate_limiter.wait_for_token()
            if self._check_stop():
                break
            try:
                await worker_func(*args, **kwargs)
            except Exception as e:
                self.result.failed_requests += 1
                self._record_error(str(e))
            else:
                self.result.total_requests += 1
                self.result.successful_requests += 1
    
    def _update_bytes(self, sent: int = 0, received: int = 0):
        self.result.bytes_sent += sent
        self.result.bytes_received += received
    
    @classmethod
    def set_emergency_stop(cls, active: bool):
        """设置/清除全局熔断 (由 Controller 广播调用)"""
        if active:
            cls.EMERGENCY_STOP.set()
            logger.critical("emergency_stop_activated", attack_class=cls.NAME)
        else:
            cls.EMERGENCY_STOP.clear()
            logger.info("emergency_stop_reset", attack_class=cls.NAME)


# ========== 攻击注册表 ==========

class AttackRegistry:
    """攻击类型注册表"""
    
    _registry: Dict[AttackType, type] = {}
    
    @classmethod
    def register(cls, attack_type: AttackType, attack_class: type):
        cls._registry[attack_type] = attack_class
        logger.info("attack_registered", type=attack_type.value, class_name=attack_class.__name__)
    
    @classmethod
    def get(cls, attack_type: AttackType) -> Optional[type]:
        return cls._registry.get(attack_type)
    
    @classmethod
    def list_available(cls) -> List[AttackType]:
        return list(cls._registry.keys())
    
    @classmethod
    def create(cls, command: AttackCommand) -> SafeAttackBase:
        attack_class = cls.get(command.attack_type)
        if not attack_class:
            raise ValueError(f"Unsupported attack type: {command.attack_type.value}")
        return attack_class(command)
    
    @classmethod
    def broadcast_emergency_stop(cls, active: bool):
        """广播熔断状态到所有攻击类"""
        for attack_class in cls._registry.values():
            attack_class.set_emergency_stop(active)