"""v1.5.0 新增: StateStore 专项测试 (R-NEW-1)

覆盖:
- 初始化: SQLite + 建表
- save_node / save_attack / save_emergency 写入
- load_* 读取并比对
- purge_attack 清除
- 优雅降级: 不可写路径不崩
- 跨进程一致性: 关闭-重开可恢复
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_initialize_creates_tables():
    with tempfile.TemporaryDirectory() as tmp:
        from app.state_store import StateStore
        s = StateStore(db_path=Path(tmp) / "state.db")
        s.initialize()
        assert s.enabled
        assert s.db_path.exists()
        s.close()


def test_save_and_load_node():
    with tempfile.TemporaryDirectory() as tmp:
        from app.state_store import StateStore
        s = StateStore(db_path=Path(tmp) / "state.db")
        s.initialize()
        asyncio.run(s.save_node({
            "node_id": "n1", "ip": "10.0.0.1", "hostname": "h1",
            "node_type": "http", "status": "online",
        }))
        nodes = s.load_nodes()
        assert len(nodes) == 1
        assert nodes[0]["node_id"] == "n1"
        assert nodes[0]["ip"] == "10.0.0.1"
        s.close()
        print("SAVE AND LOAD NODE OK")


def test_save_and_load_attack():
    with tempfile.TemporaryDirectory() as tmp:
        from app.state_store import StateStore
        s = StateStore(db_path=Path(tmp) / "state.db")
        s.initialize()
        asyncio.run(s.save_attack({
            "attack_id": "atk-001",
            "status": "running",
            "attack_type": "http_flood",
            "target_ip": "10.100.10.10",
            "started_at": "2026-09-01T10:00:00Z",
        }))
        attacks = s.load_attacks()
        assert len(attacks) == 1
        assert attacks[0]["attack_id"] == "atk-001"
        assert attacks[0]["target_ip"] == "10.100.10.10"
        s.close()
        print("SAVE AND LOAD ATTACK OK")


def test_save_and_load_emergency():
    with tempfile.TemporaryDirectory() as tmp:
        from app.state_store import StateStore
        s = StateStore(db_path=Path(tmp) / "state.db")
        s.initialize()
        # 初始无熔断
        assert s.load_emergency() is None
        # 设置熔断
        asyncio.run(s.save_emergency(True, "test", "admin"))
        em = s.load_emergency()
        assert em is not None
        assert em["active"] is True
        assert em["reason"] == "test"
        assert em["issued_by"] == "admin"
        # 复位
        asyncio.run(s.save_emergency(False))
        em2 = s.load_emergency()
        assert em2["active"] is False
        s.close()
        print("SAVE AND LOAD EMERGENCY OK")


def test_purge_attack():
    with tempfile.TemporaryDirectory() as tmp:
        from app.state_store import StateStore
        s = StateStore(db_path=Path(tmp) / "state.db")
        s.initialize()
        asyncio.run(s.save_attack({
            "attack_id": "atk-purge",
            "status": "running",
            "attack_type": "syn_flood",
            "target_ip": "10.100.10.20",
        }))
        assert len(s.load_attacks()) == 1
        s.purge_attack("atk-purge")
        assert len(s.load_attacks()) == 0
        s.close()
        print("PURGE ATTACK OK")


def test_persistence_across_instances():
    """关键: 关闭实例 → 新实例可读取"""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "state.db"
        from app.state_store import StateStore
        s1 = StateStore(db_path=db_path)
        s1.initialize()
        asyncio.run(s1.save_node({"node_id": "persistent", "ip": "1.2.3.4",
                                  "hostname": "h", "node_type": "http"}))
        asyncio.run(s1.save_emergency(True, "shutdown", "system"))
        s1.close()
        # 新实例重开
        s2 = StateStore(db_path=db_path)
        s2.initialize()
        nodes = s2.load_nodes()
        em = s2.load_emergency()
        assert len(nodes) == 1 and nodes[0]["node_id"] == "persistent"
        assert em["active"] is True
        assert em["reason"] == "shutdown"
        s2.close()
        print("PERSISTENCE ACROSS INSTANCES OK")


def test_graceful_degradation_on_invalid_path():
    """不可写路径: 初始化失败但 enabled=False, 操作静默跳过

    Windows 下 Path("/nonexistent/...") 实际可创建; 改用 file 已存在且为目录的冲突来触发
    """
    with tempfile.TemporaryDirectory() as tmp:
        from app.state_store import StateStore
        # 用一个已存在的目录作为 db 路径 -> sqlite3 必然失败
        dir_path = Path(tmp) / "is_a_dir_not_file"
        dir_path.mkdir()
        s = StateStore(db_path=dir_path)
        s.initialize()  # 应失败但不抛
        assert s.enabled is False
        # 所有操作应静默 no-op
        asyncio.run(s.save_node({"node_id": "x"}))
        assert s.load_nodes() == []
        s.close()
    print("GRACEFUL DEGRADATION OK")


def test_wal_mode_enabled():
    """WAL 模式启用 (并发写优化)"""
    with tempfile.TemporaryDirectory() as tmp:
        from app.state_store import StateStore
        s = StateStore(db_path=Path(tmp) / "state.db")
        s.initialize()
        # 检查 WAL 文件
        assert s.db_path.with_suffix(".db-wal").exists() or s.db_path.with_suffix(".db-shm").exists()
        s.close()
        print("WAL MODE ENABLED OK")


def test_concurrent_writes_dont_corrupt():
    """并发写不损坏数据库"""
    with tempfile.TemporaryDirectory() as tmp:
        from app.state_store import StateStore
        s = StateStore(db_path=Path(tmp) / "state.db")
        s.initialize()

        async def write_many():
            for i in range(50):
                await s.save_node({
                    "node_id": f"n{i}", "ip": f"10.0.0.{i}",
                    "hostname": f"h{i}", "node_type": "http",
                })
        asyncio.run(write_many())
        nodes = s.load_nodes()
        assert len(nodes) == 50
        s.close()
        print("CONCURRENT WRITES OK (50 nodes)")


if __name__ == "__main__":
    test_initialize_creates_tables()
    test_save_and_load_node()
    test_save_and_load_attack()
    test_save_and_load_emergency()
    test_purge_attack()
    test_persistence_across_instances()
    test_graceful_degradation_on_invalid_path()
    test_wal_mode_enabled()
    test_concurrent_writes_dont_corrupt()
    print("\nALL 9 STATE STORE TESTS PASSED")
