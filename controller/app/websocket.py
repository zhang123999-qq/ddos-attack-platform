from __future__ import annotations

import asyncio
import json
from typing import Dict, Set, Optional, Any
from datetime import datetime, timezone
from fastapi import WebSocket, WebSocketDisconnect, Query, Depends
import structlog

from app.auth import verify_controller_token, auth_config
from app.models import NodeHeartbeat, AttackResult

logger = structlog.get_logger(__name__)


class ConnectionManager:
    """WebSocket 连接管理器 - 支持多频道订阅"""
    
    def __init__(self):
        # channel -> set of websockets
        self._channels: Dict[str, Set[WebSocket]] = {}
        self._client_info: Dict[WebSocket, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
    
    async def connect(self, websocket: WebSocket, client_id: str, channels: list[str]):
        await websocket.accept()
        async with self._lock:
            self._client_info[websocket] = {
                "client_id": client_id,
                "connected_at": datetime.now(timezone.utc),
                "channels": set(channels)
            }
            for ch in channels:
                self._channels.setdefault(ch, set()).add(websocket)
        logger.info("ws_connected", client_id=client_id, channels=channels)
    
    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            info = self._client_info.pop(websocket, None)
            if info:
                for ch in info["channels"]:
                    self._channels.get(ch, set()).discard(websocket)
                    if not self._channels.get(ch):
                        self._channels.pop(ch, None)
                logger.info("ws_disconnected", client_id=info["client_id"])
    
    async def subscribe(self, websocket: WebSocket, channels: list[str]):
        async with self._lock:
            info = self._client_info.get(websocket)
            if info:
                for ch in channels:
                    info["channels"].add(ch)
                    self._channels.setdefault(ch, set()).add(websocket)
    
    async def unsubscribe(self, websocket: WebSocket, channels: list[str]):
        async with self._lock:
            info = self._client_info.get(websocket)
            if info:
                for ch in channels:
                    info["channels"].discard(ch)
                    self._channels.get(ch, set()).discard(websocket)
    
    async def broadcast(self, channel: str, message: dict):
        """向频道广播消息"""
        if channel not in self._channels:
            return

        dead = set()
        data = json.dumps(message, default=str)

        # P2 修复: 迭代快照副本 — await 期间并发断连会修改原集合,
        # 直接迭代将触发 "Set changed size during iteration"
        for ws in list(self._channels[channel]):
            try:
                await ws.send_text(data)
            except Exception:
                dead.add(ws)

        # 清理断开的连接
        # 注意: disconnect() 内部会自行获取 self._lock — asyncio.Lock 不可重入,
        # 此处若再包一层锁将造成自死锁 (任何客户端断连即冻结整个 WS 子系统)
        if dead:
            for ws in dead:
                await self.disconnect(ws)
    
    async def send_personal(self, websocket: WebSocket, message: dict):
        try:
            await websocket.send_text(json.dumps(message, default=str))
        except Exception:
            await self.disconnect(websocket)
    
    def get_channel_subscribers(self, channel: str) -> int:
        return len(self._channels.get(channel, set()))
    
    def get_total_connections(self) -> int:
        return len(self._client_info)


manager = ConnectionManager()


# 频道定义
class Channels:
    NODES = "nodes"           # 节点状态变更
    ATTACKS = "attacks"       # 攻击状态更新
    METRICS = "metrics"       # 实时指标 (高频)
    AUDIT = "audit"           # 审计日志
    ALERTS = "alerts"         # 告警
    SYSTEM = "system"         # 系统事件 (熔断、启停等)


async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(...),
    channels: str = Query("nodes,attacks,metrics,alerts,system"),
    client_id: str = Query("web-ui")
):
    """WebSocket 端点 - 需要 Token 认证"""
    # 验证 Token
    if not auth_config.verify_token(token):
        await websocket.close(code=4001, reason="Invalid token")
        return
    
    ch_list = [c.strip() for c in channels.split(",") if c.strip()]
    await manager.connect(websocket, client_id, ch_list)
    
    try:
        while True:
            # 心跳保持 + 接收订阅变更
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "subscribe":
                    await manager.subscribe(websocket, msg.get("channels", []))
                elif msg.get("type") == "unsubscribe":
                    await manager.unsubscribe(websocket, msg.get("channels", []))
                elif msg.get("type") == "ping":
                    await manager.send_personal(websocket, {"type": "pong", "ts": datetime.now(timezone.utc).isoformat()})
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("ws_error", client_id=client_id, error=str(e))
    finally:
        await manager.disconnect(websocket)


# ========== 广播辅助函数 ==========

async def broadcast_node_update(node_data: dict):
    await manager.broadcast(Channels.NODES, {
        "type": "node_update",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": node_data
    })


async def broadcast_node_heartbeat(heartbeat: NodeHeartbeat):
    await manager.broadcast(Channels.METRICS, {
        "type": "node_heartbeat",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": heartbeat.model_dump(mode='json')
    })


async def broadcast_attack_start(attack_data: dict):
    await manager.broadcast(Channels.ATTACKS, {
        "type": "attack_start",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": attack_data
    })


async def broadcast_attack_update(attack_id: str, node_id: str, result: AttackResult):
    await manager.broadcast(Channels.ATTACKS, {
        "type": "attack_update",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {
            "attack_id": attack_id,
            "node_id": node_id,
            "result": result.model_dump(mode='json')
        }
    })


async def broadcast_attack_stop(attack_id: str, reason: str):
    await manager.broadcast(Channels.ATTACKS, {
        "type": "attack_stop",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {"attack_id": attack_id, "reason": reason}
    })


async def broadcast_emergency_stop(reason: str, issued_by: str):
    await manager.broadcast(Channels.ALERTS, {
        "type": "emergency_stop",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": "critical",
        "data": {"reason": reason, "issued_by": issued_by}
    })
    await manager.broadcast(Channels.SYSTEM, {
        "type": "emergency_stop",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {"reason": reason, "issued_by": issued_by}
    })


async def broadcast_rate_limit_status(status: dict):
    await manager.broadcast(Channels.METRICS, {
        "type": "rate_limit_status",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": status
    })


async def broadcast_audit_event(event: dict):
    await manager.broadcast(Channels.AUDIT, {
        "type": "audit_event",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": event
    })


async def broadcast_system_event(event_type: str, data: dict):
    await manager.broadcast(Channels.SYSTEM, {
        "type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data
    })