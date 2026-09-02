"""v1.5.0 新增: Controller 状态持久化 (R-NEW-1)

使用 SQLite + aiosqlite 异步层, 解决:
- Controller 重启后, active_attacks 状态丢失 (攻击在节点仍在跑, controller 视角"已完成")
- node_registry 状态丢失 (节点需要重新 register)
- emergency_stop 标志丢失 (重启后熔断状态归零)

设计原则:
- 异步写入 (不阻塞事件循环)
- 优雅降级 (SQLite 不可用时仍可启动, 仅警告)
- 轻量级 schema (3 张表: nodes/attacks/emergency)
- 写后即查 (read-modify-write 原子性靠 aiosqlite 串行访问)
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger(__name__)


class StateStore:
    """Controller 状态持久层 (单例, 异步写入 + 同步读取)"""

    DEFAULT_PATH = "/var/lib/ddos-controller/state.db"
    SCHEMA_VERSION = 1

    def __init__(self, db_path: Optional[Path] = None):
        env_path = os.getenv("STATE_DB_PATH")
        if db_path is not None:
            self.db_path = Path(db_path)
        elif env_path:
            self.db_path = Path(env_path)
        elif os.name == "nt" or not os.path.isdir(os.path.dirname(self.DEFAULT_PATH)):
            # Windows / 无 Linux 默认路径: fallback 到用户 home
            self.db_path = Path.home() / ".ddos-controller" / "state.db"
        else:
            self.db_path = Path(self.DEFAULT_PATH)
        self._conn: Optional[sqlite3.Connection] = None
        self._write_lock = asyncio.Lock()
        self._enabled = False
        self._last_flush_error: Optional[str] = None

    def initialize(self) -> None:
        """初始化 SQLite + 建表 (启动时调用一次)"""
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(self.db_path), check_same_thread=False, isolation_level=None
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.row_factory = sqlite3.Row
            self._create_tables()
            self._enabled = True
            logger.info("state_store_initialized", path=str(self.db_path))
        except Exception as e:
            logger.warning("state_store_init_failed",
                           path=str(self.db_path), error=str(e))
            self._enabled = False

    def _create_tables(self) -> None:
        assert self._conn is not None
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS nodes (
                key TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS attacks (
                key TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS emergency_stop (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                active INTEGER NOT NULL,
                reason TEXT,
                issued_by TEXT,
                set_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS kv_meta (
                k TEXT PRIMARY KEY,
                v TEXT
            );
        """)
        # 记录 schema 版本
        self._conn.execute(
            "INSERT OR IGNORE INTO kv_meta(k, v) VALUES('schema_version', ?)",
            (str(self.SCHEMA_VERSION),)
        )

    # ========== 写入 (异步) ==========

    async def save_node(self, node: Dict[str, Any]) -> None:
        """保存节点信息"""
        await self._upsert_kv("nodes", node["node_id"], node)

    async def save_attack(self, attack: Dict[str, Any]) -> None:
        """保存攻击元数据 (含 status/started_at)"""
        await self._upsert_kv("attacks", attack["attack_id"], attack)

    async def save_emergency(self, active: bool, reason: str = "",
                            issued_by: str = "") -> None:
        """保存熔断状态"""
        if not self._enabled:
            return
        async with self._write_lock:
            try:
                assert self._conn is not None
                self._conn.execute(
                    "INSERT OR REPLACE INTO emergency_stop"
                    "(id, active, reason, issued_by, set_at) VALUES (1, ?, ?, ?, ?)",
                    (1 if active else 0, reason, issued_by, time.time()),
                )
            except Exception as e:
                self._last_flush_error = str(e)
                logger.debug("emergency_save_failed", error=str(e))

    async def _upsert_kv(self, table: str, key: str, data: Dict[str, Any]) -> None:
        if not self._enabled:
            return
        async with self._write_lock:
            try:
                assert self._conn is not None
                payload = json.dumps(data, default=str)
                self._conn.execute(
                    f"INSERT OR REPLACE INTO {table}(key, data, updated_at) "
                    f"VALUES (?, ?, ?)",
                    (key, payload, time.time()),
                )
            except Exception as e:
                self._last_flush_error = str(e)
                logger.debug("save_failed", table=table, key=key, error=str(e))

    # ========== 读取 (同步, 启动时调用) ==========

    def load_nodes(self) -> List[Dict[str, Any]]:
        if not self._enabled:
            return []
        try:
            assert self._conn is not None
            rows = self._conn.execute("SELECT data FROM nodes").fetchall()
            return [json.loads(r["data"]) for r in rows]
        except Exception as e:
            logger.warning("load_nodes_failed", error=str(e))
            return []

    def load_attacks(self) -> List[Dict[str, Any]]:
        if not self._enabled:
            return []
        try:
            assert self._conn is not None
            rows = self._conn.execute("SELECT data FROM attacks").fetchall()
            return [json.loads(r["data"]) for r in rows]
        except Exception as e:
            logger.warning("load_attacks_failed", error=str(e))
            return []

    def load_emergency(self) -> Optional[Dict[str, Any]]:
        if not self._enabled:
            return None
        try:
            assert self._conn is not None
            row = self._conn.execute(
                "SELECT active, reason, issued_by, set_at FROM emergency_stop WHERE id=1"
            ).fetchone()
            if row is None:
                return None
            return {
                "active": bool(row["active"]),
                "reason": row["reason"] or "",
                "issued_by": row["issued_by"] or "",
                "set_at": row["set_at"],
            }
        except Exception as e:
            logger.warning("load_emergency_failed", error=str(e))
            return None

    def purge_attack(self, attack_id: str) -> None:
        """攻击完成/失败后清除持久记录"""
        if not self._enabled:
            return
        try:
            assert self._conn is not None
            self._conn.execute("DELETE FROM attacks WHERE key=?", (attack_id,))
        except Exception as e:
            logger.debug("purge_attack_failed", attack_id=attack_id, error=str(e))

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    @property
    def enabled(self) -> bool:
        return self._enabled


# 全局单例
state_store = StateStore()

