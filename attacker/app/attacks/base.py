from __future__ import annotations

import asyncio
import os
import time
import ipaddress
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from ipaddress import ip_network, ip_address
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
    
    # 安全配置 (从环境变量加载)
    ALLOWED_TARGET_CIDRS: List[str] = []
    EMERGENCY_STOP = asyncio.Event()
    
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # 自动加载白名单 (M-1 修复: 兜底收窄为回环, 真实网段必须显式声明)
        cidrs_str = os.getenv("ALLOWED_TARGET_CIDRS", "127.0.0.0/8")
        cls.ALLOWED_TARGET_CIDRS = [c.strip() for c in cidrs_str.split(",") if c.strip()]
        cls._allowed_networks = [ip_network(c, strict=False) for c in cls.ALLOWED_TARGET_CIDRS]
        logger.info("attack_class_loaded", name=cls.NAME, allowed_cidrs=cls.ALLOWED_TARGET_CIDRS)
    
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
    
    # ========== 安全检查 (必须在 execute 前调用) ==========
    
    @classmethod
    def validate_target(cls, target_ip: str) -> bool:
        """验证目标 IP 是否在白名单内"""
        try:
            ip = ip_address(target_ip)
            return any(ip in net for net in cls._allowed_networks)
        except ValueError:
            return False
    
    @classmethod
    def pre_flight_check(cls, target: str) -> None:
        """启动前安全检查 - 失败抛出 SafetyError"""
        # 1. 目标白名单
        if not cls.validate_target(target):
            raise SafetyError(f"Target {target} not in allowed CIDRs: {cls.ALLOWED_TARGET_CIDRS}")
        
        # 2. 全局熔断
        if cls.EMERGENCY_STOP.is_set():
            raise SafetyError("Global emergency stop is active")
        
        # 3. 权限检查
        if cls.REQUIRES_ROOT and os.geteuid() != 0:
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
        
        try:
            # 子类实现具体攻击逻辑
            await self._run()
            self.result.status = AttackStatus.STOPPED
        except SafetyError as e:
            self.result.status = AttackStatus.FAILED
            self.result.errors.append(f"Safety: {e}")
            logger.warning("attack_safety_error", attack_id=self.attack_id, error=str(e))
        except asyncio.CancelledError:
            self.result.status = AttackStatus.EMERGENCY_STOPPED
            self.result.errors.append("Cancelled by emergency stop")
            logger.warning("attack_emergency_stopped", attack_id=self.attack_id)
        except Exception as e:
            self.result.status = AttackStatus.FAILED
            self.result.errors.append(f"Runtime: {e}")
            logger.error("attack_runtime_error", attack_id=self.attack_id, error=str(e), exc_info=True)
        finally:
            self.result.stopped_at = datetime.now(timezone.utc)
            if self._start_time:
                self.result.metrics["duration_seconds"] = time.monotonic() - self._start_time
            await self._cleanup()
        
        return self.result
    
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
                self.result.errors.append(str(e))
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