from __future__ import annotations

import os
import json
import asyncio
from datetime import datetime
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
        self._writer_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self):
        self._running = True
        self._writer_task = asyncio.create_task(self._writer_loop())
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

        # 4. 关闭文件处理器
        self.file_handler.close()

    async def _writer_loop(self):
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                if event is None:
                    break
                self._write(event)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"Audit writer error: {e}")

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
                "target": command.params.target.model_dump(),
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
            details=result.model_dump(),
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
            details=node.model_dump()
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
        return f"audit-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{os.urandom(4).hex()}"


audit_logger = AuditLogger()


# Structlog 配置
def add_audit_fields(logger, method_name, event_dict: EventDict) -> EventDict:
    event_dict.setdefault("timestamp", datetime.utcnow().isoformat() + "Z")
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