from __future__ import annotations

import os
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any
from logging.handlers import RotatingFileHandler
import logging
import structlog
from structlog.types import EventDict

from app.models import AuditEvent, NodeInfo, AttackCommand, AttackResult, AttackStatus

# =============================================================================
# CRIT-4 修复: 确保关闭事件在 writer 停止前写入队列并排空
# HIGH-1 修复: log_attack_result 使用枚举值比较
# =============================================================================


class AuditLogger:
    """结构化审计事件分发器

    v1.3.0 方案B: 攻击日志不做磁盘存储 (AUDIT_FILE_ENABLED 默认 false)。
    事件仅经内存队列 → WebSocket 实时广播 + 会话级环形缓冲 (最近 500 条)。
    设 AUDIT_FILE_ENABLED=true 可恢复旧的 JSONL 落盘 + 轮转行为。
    """

    # 会话级环形缓冲 — 供 WebUI 审计面板回看, 不落盘
    MEMORY_BUFFER_MAX = 500

    def __init__(self):
        self.file_enabled = os.getenv("AUDIT_FILE_ENABLED", "false").lower() == "true"

        if self.file_enabled:
            # 降级链: AUDIT_LOG_PATH|/var/log → cwd → ~/.local/state → tmpdir
            candidates = [
                Path(os.getenv("AUDIT_LOG_PATH", "/var/log/ddos-audit/audit.jsonl")),
                Path.cwd() / "audit.jsonl",
                Path.home() / ".local" / "state" / "ddos-audit" / "audit.jsonl",
                Path(__import__("tempfile").gettempdir()) / "ddos-audit.jsonl",
            ]
            self.log_path = candidates[0]
            for cand in candidates:
                try:
                    cand.parent.mkdir(parents=True, exist_ok=True)
                    probe = cand.parent / ".write_test"
                    probe.touch()
                    probe.unlink()
                    self.log_path = cand
                    break
                except (PermissionError, OSError):
                    continue
            self.file_handler = RotatingFileHandler(
                self.log_path,
                maxBytes=100 * 1024 * 1024,
                backupCount=10,
                encoding="utf-8"
            )
            self.file_handler.setFormatter(logging.Formatter("%(message)s"))
            logging.getLogger("ddos.audit").addHandler(self.file_handler)
        else:
            self.log_path = None
            self.file_handler = None

        self.retention_days = int(os.getenv("AUDIT_RETENTION_DAYS", "90"))
        self.memory_buffer: list = []  # 环形缓冲 (deque 语义, 手动裁剪)

        self.logger = logging.getLogger("ddos.audit")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

        self._queue: asyncio.Queue = asyncio.Queue(maxsize=10000)
        self._bg_tasks: set = set()  # 广播任务引用, 防 GC
        self._writer_task: Optional[asyncio.Task] = None
        self._retention_task: Optional[asyncio.Task] = None
        self._running = False
        # M-2 修复: 广播钩子由 main.py 注入 (broadcast_audit_event), 解耦避免循环导入;
        # 使 WebSocket audit 频道真正收到推送 (原先为死代码)
        self._broadcast_hook: Optional[Any] = None

    def set_broadcast_hook(self, hook) -> None:
        """注入 async callable(event_dict); 由应用装配层调用"""
        self._broadcast_hook = hook

    async def recent_events(self) -> list:
        """会话内最近审计事件 (内存缓冲, 不含历史落盘数据)"""
        return list(self.memory_buffer)

    async def start(self):
        self._running = True
        # v1.3.3: Queue 在 __init__ 时绑定首个事件循环 — 二次 lifespan (测试场景)
        # 会命中 "bound to a different event loop"。每次 start 重建队列, writer 归零。
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=10000)
        if self._writer_task and not self._writer_task.done():
            self._writer_task.cancel()
            try:
                await self._writer_task
            except (asyncio.CancelledError, RuntimeError):
                pass
        self._writer_task = asyncio.create_task(self._writer_loop())
        if self.file_enabled:
            # C-3/M-4 修复: 启动按天保留清理任务 (仅落盘模式需要)
            self._retention_task = asyncio.create_task(self._retention_loop())
        await self.log_event(AuditEvent(
            event_id=self._gen_id(),
            event_type="config_change",
            actor="system",
            details={"action": "audit_logger_started",
                     "file_enabled": self.file_enabled,
                     "path": str(self.log_path) if self.log_path else None}
        ))

    async def stop(self):
        """CRIT-4 修复: 先记录关闭事件并排空队列，再停止 writer"""
        # 1. 记录关闭事件
        await self.log_event(AuditEvent(
            event_id=self._gen_id(),
            event_type="config_change",
            actor="system",
            details={"action": "audit_logger_stopping"}
        ))

        # 2. 等待队列接近排空 (允许少量残留在路上)
        await asyncio.sleep(0.5)

        # 3. 发送哨兵停止 writer
        # v1.3.3: Queue 绑定创建时的旧事件循环 — 跨循环复用 (测试/多次 lifespan) 时
        # put/get 会抛 "bound to a different event loop"。此时直接取消 writer,
        # 不再尝试通过队列发哨兵 (队列本身已不可用)。
        if self._writer_task:
            self._running = False
            try:
                self._queue.put_nowait(None)
                await asyncio.wait_for(self._writer_task, timeout=5.0)
            except (asyncio.TimeoutError, RuntimeError):
                self._writer_task.cancel()
                try:
                    await self._writer_task
                except (asyncio.CancelledError, RuntimeError):
                    pass
        for t in (self._retention_task,):
            if t:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

        # 4. 关闭文件处理器 (仅落盘模式)
        if self.file_handler:
            self.file_handler.close()

    async def _writer_loop(self):
        # v1.3.3: 捕获范围收窄到 QueueEmpty — 修复跨事件循环复用时
        # "Queue is bound to a different event loop" 异常被 except Exception 吞掉后
        # while self._running 立即自旋、打满 stdout 并饿死事件循环的问题。
        while True:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if event is None:
                break
            # 内存环形缓冲 (会话级, 不落盘)
            self.memory_buffer.append(event.model_dump(mode='json'))
            if len(self.memory_buffer) > self.MEMORY_BUFFER_MAX:
                del self.memory_buffer[:len(self.memory_buffer) - self.MEMORY_BUFFER_MAX]
            # v1.3.0 方案B: 仅落盘模式写文件
            if self.file_enabled:
                self._write(event)
            # audit 频道实时推送 (钩子异常不影响主流程)
            if self._broadcast_hook is not None:
                try:
                    import asyncio as _aio
                    payload = event.model_dump(mode='json')
                    task = _aio.create_task(self._safe_broadcast(payload))
                    self._bg_tasks.add(task)
                    task.add_done_callback(self._bg_tasks.discard)
                except Exception:
                    pass

    async def _safe_broadcast(self, payload: dict):
        try:
            if self._broadcast_hook is not None:
                await self._broadcast_hook(payload)
        except Exception:
            pass

    # ========== 按天保留清理 ==========

    async def _retention_loop(self):
        """每小时扫描一次轮转备份, 删除超过 AUDIT_RETENTION_DAYS 的历史文件。
        当前文件 (audit.jsonl) 不删 — 大小由 RotatingFileHandler 控制。"""
        import asyncio as _aio
        while True:
            try:
                cutoff = datetime.now(timezone.utc).timestamp() - self.retention_days * 86400
                pattern = f"{self.log_path.name}.*"
                parent = self.log_path.parent
                if parent.exists():
                    removed = 0
                    for f in parent.glob(pattern):
                        try:
                            if f.is_file() and f.stat().st_mtime < cutoff:
                                f.unlink()
                                removed += 1
                        except OSError:
                            pass
                    if removed:
                        print(f"Audit retention: removed {removed} expired file(s)")
            except Exception as e:
                print(f"Audit retention error: {e}")
            await _aio.sleep(3600)

    def _write(self, event: AuditEvent):
        if self.file_enabled:
            self.logger.info(event.model_dump_json())

    async def log_event(self, event: AuditEvent):
        try:
            self._queue.put_nowait(event)
        except RuntimeError:
            # v1.3.3: 跨事件循环复用 (测试/多次 lifespan) — 队列不可用时直接写内存缓冲
            self._buffer_only(event)
        except asyncio.QueueFull:
            # 队列满: 丢弃最旧事件保持实时流 (不阻塞、不落盘)
            # v1.5.0 (M-3): 暴露溢出指标, 便于监控审计风暴
            try:
                from app.metrics import AUDIT_QUEUE_OVERFLOW_TOTAL
                AUDIT_QUEUE_OVERFLOW_TOTAL.inc()
            except Exception:
                pass
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(event)
            except Exception:
                pass

    def _buffer_only(self, event: AuditEvent):
        """绕过队列直接维护内存环形缓冲, 保证审计事件不因队列失效而丢失"""
        self.memory_buffer.append(event.model_dump(mode='json'))
        if len(self.memory_buffer) > self.MEMORY_BUFFER_MAX:
            del self.memory_buffer[:len(self.memory_buffer) - self.MEMORY_BUFFER_MAX]

    # ========== 便捷方法 ==========

    async def log_attack_start(self, actor: str, command: AttackCommand, node_ids: list[str]):
        await self.log_event(AuditEvent(
            event_id=self._gen_id(),
            event_type="attack_start",
            actor=actor,
            attack_id=command.attack_id,
            scenario_id=command.scenario_id,
            details={
                "attack_type": command.attack_type.value,
                "target": command.params.target.model_dump(mode='json'),
                "duration": command.params.duration,
                "rps": command.params.rps,
                "concurrency": command.params.concurrency,
                "target_nodes": node_ids,
            }
        ))

    async def log_attack_stop(self, actor: str, attack_id: str, node_id: str, reason: str = "manual"):
        await self.log_event(AuditEvent(
            event_id=self._gen_id(),
            event_type="attack_stop",
            actor=actor,
            attack_id=attack_id,
            node_id=node_id,
            details={"reason": reason}
        ))

    async def log_attack_result(self, result: AttackResult):
        # HIGH-1 修复: 使用 AttackStatus 枚举值比较，移除不存在的 "completed"
        is_success = result.status in (AttackStatus.STOPPED,)
        await self.log_event(AuditEvent(
            event_id=self._gen_id(),
            event_type="attack_complete" if is_success else "attack_failed",
            actor="node",
            attack_id=result.attack_id,
            node_id=result.node_id,
            details=result.model_dump(mode='json'),
            success=is_success
        ))

    async def log_emergency_stop(self, actor: str, reason: str, target_nodes: list[str]):
        await self.log_event(AuditEvent(
            event_id=self._gen_id(),
            event_type="emergency_stop",
            actor=actor,
            details={"reason": reason, "target_nodes": target_nodes}
        ))

    async def log_node_register(self, node: NodeInfo):
        await self.log_event(AuditEvent(
            event_id=self._gen_id(),
            event_type="node_register",
            actor="node",
            node_id=node.node_id,
            details=node.model_dump(mode='json')
        ))

    async def log_node_heartbeat(self, node_id: str, cpu: float, mem: float, net_mbps: float):
        await self.log_event(AuditEvent(
            event_id=self._gen_id(),
            event_type="node_heartbeat",
            actor="node",
            node_id=node_id,
            details={"cpu_percent": cpu, "memory_percent": mem, "network_mbps": net_mbps}
        ))

    async def log_auth_failure(self, actor: str, ip: str, reason: str):
        await self.log_event(AuditEvent(
            event_id=self._gen_id(),
            event_type="auth_failure",
            actor=actor,
            details={"client_ip": ip, "reason": reason},
            success=False,
            error_message=reason
        ))

    async def log_target_validation_failure(self, actor: str, target: str, reason: str):
        await self.log_event(AuditEvent(
            event_id=self._gen_id(),
            event_type="target_validation_failure",
            actor=actor,
            details={"target": target, "reason": reason},
            success=False,
            error_message=reason
        ))

    @staticmethod
    def _gen_id() -> str:
        return f"audit-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{os.urandom(4).hex()}"


audit_logger = AuditLogger()


# Structlog 配置
def add_audit_fields(logger, method_name, event_dict: EventDict) -> EventDict:
    event_dict.setdefault("timestamp", datetime.now(timezone.utc).isoformat() + "Z")
    event_dict.setdefault("service", "ddos-controller")
    return event_dict


# OBS-7: 过滤级别接通 LOG_LEVEL 环境变量 (原硬编码 INFO, debug 级日志永远不可见)
_LOG_LEVEL_NAME = os.getenv("LOG_LEVEL", "INFO").upper()
_LOG_LEVEL = getattr(logging, _LOG_LEVEL_NAME, logging.INFO)

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        add_audit_fields,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.JSONRenderer()
    ],
    wrapper_class=structlog.make_filtering_bound_logger(_LOG_LEVEL),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)