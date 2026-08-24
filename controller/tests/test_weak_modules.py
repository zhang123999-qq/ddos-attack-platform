# -*- coding: utf-8 -*-
"""薄弱模块补测: websocket 频道广播快照 / node_commander 失败路径 / audit 队列降级"""
import asyncio
import os
import sys

os.environ.setdefault("SHARED_SECRET", "weakmod-test-secret-32chars-abc123")
os.environ.setdefault("LOG_LEVEL", "error")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_ws_manager_channel_isolation():
    """频道订阅隔离: nodes 频道消息不应投递到未订阅该频道的连接。
    注意: broadcast 使用 send_text — mock 必须覆盖实际调用的方法"""
    from unittest.mock import AsyncMock, MagicMock
    from app.websocket import ConnectionManager

    mgr = ConnectionManager()
    ws_nodes = MagicMock()
    ws_nodes.send_text = AsyncMock()
    ws_all = MagicMock()
    ws_all.send_text = AsyncMock()

    # 按真实结构注入: _channels[channel] 存 websocket 对象本身
    mgr._channels.setdefault("nodes", set()).add(ws_nodes)
    mgr._channels.setdefault("attacks", set()).add(ws_all)

    async def check():
        await mgr.broadcast("nodes", {"event": "node_update"})

    asyncio.run(check())
    assert ws_nodes.send_text.await_count == 1
    assert ws_all.send_text.await_count == 0, "unsubscribed channel must not receive"
    print("WS CHANNEL ISOLATION OK")


def test_ws_broadcast_snapshot_survives_mutation():
    """广播期间连接断开(集合变更)不得抛 'Set changed size during iteration'"""
    from unittest.mock import AsyncMock, MagicMock
    from app.websocket import ConnectionManager

    mgr = ConnectionManager()
    ws1 = MagicMock(); ws1.send_text = AsyncMock()
    ws2 = MagicMock(); ws2.send_text = AsyncMock(side_effect=Exception("client gone"))
    mgr._channels.setdefault("nodes", set()).update([ws1, ws2])

    async def mutate_during_broadcast():
        task = asyncio.create_task(mgr.broadcast("nodes", {"x": 1}))
        await asyncio.sleep(0)
        mgr._channels["nodes"].clear()   # 广播遍历快照时清空原集合
        await task

    asyncio.run(mutate_during_broadcast())
    assert ws1.send_text.await_count == 1, "healthy connection must still receive"
    print("WS SNAPSHOT-ITERATION SAFETY OK")


def test_node_commander_unregistered_node():
    """未注册节点下发指令必须优雅返回 False, 不得抛异常"""
    from app.node_commander import NodeCommander
    nc = NodeCommander()
    ok = asyncio.run(nc.send_attack_command("ghost-node", {"any": "payload"}))
    assert ok is False
    print("NODE_COMMANDER GHOST NODE OK")


def test_audit_queue_full_degrades_to_sync_write():
    """队列满时 log_event 必须同步直写降级, 事件不得丢失且不抛异常。
    AuditLogger 单例在导入时已绑定 /var 路径 — 直接复用全局实例灌满队列。"""
    from app.audit import audit_logger
    from app.models import AuditEvent

    async def run():
        before_writes = audit_logger.file_handler.stream.tell() if audit_logger.file_handler.stream else 0
        n = audit_logger._queue.maxsize + 100
        for i in range(n):
            await audit_logger.log_event(AuditEvent(
                event_id=f"flood-{i}", event_type="config_change",
                actor="test", details={"i": i}))
        after = audit_logger.file_handler.stream.tell() if audit_logger.file_handler.stream else 0
        return n, after - before_writes

    n, written_bytes = asyncio.run(run())
    # 队列只吸收 maxsize 条, 其余 ~100 条走同步直写 → 文件必然增长
    assert written_bytes > 0, "overflow events must be sync-written, not dropped"
    print(f"AUDIT QUEUE-FULL DEGRADE OK ({n} events, file grew {written_bytes} bytes)")


def _accepts_none():
    import inspect
    from app.audit import AuditLogger
    try:
        sig = inspect.signature(AuditLogger.__init__)
        params = list(sig.parameters)[1:]
        return len(params) == 0 or "log_dir" in params
    except Exception:
        return False


if __name__ == "__main__":
    test_ws_manager_channel_isolation()
    test_ws_broadcast_snapshot_survives_mutation()
    test_node_commander_unregistered_node()
    test_audit_queue_full_degrades_to_sync_write()
    print("ALL WEAK-MODULE TESTS PASSED")
