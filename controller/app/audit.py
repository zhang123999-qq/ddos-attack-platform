from __future__ import annotations

import os
import json
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any, Dict
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
    """结构化审计日志 - JSONL 格式，支持文件轮转 + ELK 转发"""

    def __init__(self):
        self.log_path = Path(os.getenv("AUDIT_LOG_PATH", "/var/log/ddos-audit/audit.jsonl"))
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.retention_days = int(os.getenv("AUDIT_RETENTION_DAYS", "90"))

        self.file_handler = RotatingFileHandler(
            self.log_path,
            maxBytes=100 * 1024 * 1024,
            backupCount=10,
            encoding="utf-8"
        )
        self.file_handler.setFormatter(logging.Formatter("%(message)s"))

        self.logger = logging.getLogger("ddos.audit")
        self.logger.setLevel(logging.INFO)
        self.logger.addHandler(self.file_handler)
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

    async def start(self):
        self._running = True
        self._writer_task = asyncio.create_task(self._writer_loop())
        # C-3/M-4 修复: 启动按天保留清理任务 (AUDIT_RETENTION_DAYS 原先无任何引用)
        self._retention_task = asyncio.create_task(self._retention_loop())
        await self.log_event(AuditEvent(
            event_id=self._gen_id(),
            event_type="config_change",
            actor="system",
            details={"action": "audit_logger_started", "path": str(self.log_path)}
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
        if self._writer_task:
            self._running = False
            await self._queue.put(None)
            try:
                await asyncio.wait_for(self._writer_task, timeout=5.0)
            except asyncio.TimeoutError:
                pass
        for t in (self._retention_task,):
            if t:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

        # 4. 关闭文件处理器
        self.file_handler.close()

    async def _writer_loop(self):
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                if event is None:
                    break
                self._write(event)
                # audit 频道实时推送 (钩子异常不影响落盘)
                if self._broadcast_hook is not None:
                    try:
                        import asyncio as _aio
                        payload = event.model_dump(mode='json')
                        task = _aio.create_task(self._safe_broadcast(payload))
                        self._bg_tasks.add(task)
                        task.add_done_callback(self._bg_tasks.discard)
                    except Exception:
                        pass
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"Audit writer error: {e}")

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
        self.logger.info(event.model_dump_json())

    async def log_event(self, event: AuditEvent):
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._write(event)

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


structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        add_audit_fields,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.JSONRenderer()
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)