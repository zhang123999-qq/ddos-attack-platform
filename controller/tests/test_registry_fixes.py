# -*- coding: utf-8 -*-
"""v1.3.3 回归修复测试: BUG-4 心跳语义 / BUG-6 离线节点可见性 / BUG-3 版本号 / OBS-8 保留字路由

运行 (controller/ 目录): python -m pytest tests/test_registry_fixes.py -v
"""
import os
import sys
import hmac
import hashlib
import asyncio
from datetime import datetime, timedelta, timezone

# v1.3.3: 环境变量必须在 import app.main 之前设置 — auth_config 单例在导入时读取
os.environ.setdefault("SHARED_SECRET", "test-secret-32chars-abcdef1234567890")
os.environ.setdefault("ENABLE_WEB_UI", "true")
os.environ.setdefault("LOG_LEVEL", "error")
# v1.4.1: TD-1 fail-closed default 需测试显式 opt-out 走 HTTP
# (测试环境无 Node, 真实环境用 controller-install.sh 注入)
os.environ.setdefault("NODE_INSECURE_PLAIN_HTTP", "true")
os.environ.setdefault("NODE_PLAIN_HTTP_BANNED", "false")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.models import NodeInfo, NodeHeartbeat, NodeStatus  # noqa: E402

SECRET = os.environ["SHARED_SECRET"].encode()


def node_token(node_id: str) -> str:
    return hmac.new(SECRET, node_id.encode(), hashlib.sha256).hexdigest()


def admin_token() -> str:
    return hmac.new(SECRET, b"ddos-controller-auth", hashlib.sha256).hexdigest()


ADMIN = {"Authorization": f"Bearer {admin_token()}"}


# ---------- BUG-4: registry.heartbeat 服务器时钟 + 未知节点不静默 ----------

def test_heartbeat_unknown_node_does_not_materialize_entry():
    """控制器失忆后收到未知节点心跳: 不再静默更新(也不盲建残缺条目), 仅记录指标"""
    from app.registry import NodeRegistry
    reg = NodeRegistry()
    hb = NodeHeartbeat(
        node_id="ghost-node", cpu_percent=1.0, memory_percent=2.0,
        network_mbps=0.0, active_connections=0, current_attacks=[],
        status=NodeStatus.ONLINE,
    )
    asyncio.run(reg.heartbeat(hb))
    # 关键断言: 不创建条目 (自愈由节点周期 re-register 完成)
    assert reg.get_node("ghost-node") is None
    # 指标仍被记录 (可观测性)
    assert "ghost-node" in reg._heartbeats
    print("BUG-4 UNKNOWN HEARTBEAT NO-SILENT OK")


def test_heartbeat_uses_server_clock_and_updates_status():
    """已注册节点的心跳: last_heartbeat 用服务器时间记账, status 跟随心跳"""
    from app.registry import NodeRegistry
    reg = NodeRegistry()
    info = NodeInfo(node_id="node-a", ip="10.0.0.9", hostname="a",
                    cpu_cores=2, memory_gb=4, supported_attacks=["http_flood"])
    asyncio.run(reg.register(info))

    before = datetime.now(timezone.utc) - timedelta(seconds=5)
    # 模拟节点时钟漂移: 心跳体携带一个"过去"的时间戳 — 不应污染记账
    stale_hb = NodeHeartbeat(
        node_id="node-a", cpu_percent=5.0, memory_percent=5.0,
        network_mbps=0.0, active_connections=0, current_attacks=[],
        status=NodeStatus.ATTACKING, timestamp=before,
    )
    asyncio.run(reg.heartbeat(stale_hb))
    entry = reg.get_node("node-a")
    assert entry.status == NodeStatus.ATTACKING
    assert entry.last_heartbeat > before, "server-clock bookkeeping required"
    assert entry.last_heartbeat <= datetime.now(timezone.utc) + timedelta(seconds=1)
    print("BUG-4 SERVER-CLOCK HEARTBEAT OK")


# ---------- BUG-6: offline 节点详情可见 ----------

def test_offline_node_detail_visible():
    """注册→注销后 GET /nodes/{id} 必须返回 offline 条目而非 404"""
    with TestClient(app) as client:
        headers = {"X-Node-ID": "n-off", "X-Node-Token": node_token("n-off")}
        body = {"node_id": "n-off", "node_type": "http", "ip": "127.0.0.1",
                "hostname": "off", "cpu_cores": 1, "memory_gb": 1}
        r = client.post("/api/v1/nodes/register", json=body, headers=headers)
        assert r.status_code == 200, r.text

        r = client.post("/api/v1/nodes/n-off/unregister", headers=headers)
        assert r.status_code == 200, r.text

        r = client.get("/api/v1/nodes/n-off", headers=ADMIN)
        assert r.status_code == 200, f"offline detail must be visible, got {r.status_code}: {r.text}"
        data = r.json()["data"]
        assert data["node_id"] == "n-off"
        assert data["status"] == "offline"

        # 真正不存在的节点仍然 404
        r = client.get("/api/v1/nodes/never-existed", headers=ADMIN)
        assert r.status_code == 404
        print("BUG-6 OFFLINE NODE DETAIL VISIBLE OK")


# ---------- BUG-3 回归: /health version 与 app.version 一致 ----------

def test_health_version_matches_app():
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["version"] == str(app.version), \
            "health.version must derive from app.version (BUG-3 regression guard)"
        print(f"BUG-3 HEALTH VERSION OK ({app.version})")


def test_health_version_is_current_release():
    """发布守护: /health 版本必须是 PLATFORM_VERSION, 防止再出现硬编码旧版本。
    v1.4.0 (TD-3 修复): 用元组比较版本号, 避免每次发版都要改测试
    实际只需断言 served == PLATFORM_VERSION (一致性) 且 >= (1, 3, 3) (不退化)"""
    from app.main import PLATFORM_VERSION
    parts = PLATFORM_VERSION.split(".")
    ver_tuple = tuple(int(p) for p in parts)
    assert ver_tuple >= (1, 3, 3), f"release version regressed: {PLATFORM_VERSION}"
    with TestClient(app) as client:
        served = client.get("/health").json()["version"]
    assert served == PLATFORM_VERSION
    print(f"RELEASE VERSION CONSISTENT ({served})")


# ---------- OBS-8: 动态段保留字返回 405 而非误导性 404 ----------

def test_reserved_attack_path_returns_405():
    with TestClient(app) as client:
        r = client.get("/api/v1/attacks/launch", headers=ADMIN)
        assert r.status_code == 405, f"expected 405 for reserved word, got {r.status_code}"
        assert "launch" in r.json()["detail"]
        print("OBS-8 RESERVED PATH 405 OK")


# ---------- OBS-7: LOG_LEVEL 驱动 structlog 过滤级别 ----------

def test_structlog_level_follows_env():
    """audit.configure 的过滤边界必须由 LOG_LEVEL 决定 (具体类名暴露过滤级别)"""
    import importlib
    saved = os.environ.get("LOG_LEVEL")

    os.environ["LOG_LEVEL"] = "DEBUG"
    import app.audit as audit_mod
    importlib.reload(audit_mod)
    debug_logger = audit_mod.structlog.get_logger("t-debug").bind()
    assert type(debug_logger).__name__ == "BoundLoggerFilteringAtDebug", \
        f"LOG_LEVEL=debug got {type(debug_logger).__name__}"

    os.environ["LOG_LEVEL"] = "ERROR"
    importlib.reload(audit_mod)
    err_logger = audit_mod.structlog.get_logger("t-err").bind()
    assert type(err_logger).__name__ == "BoundLoggerFilteringAtError", \
        f"LOG_LEVEL=error got {type(err_logger).__name__}"

    if saved is not None:
        os.environ["LOG_LEVEL"] = saved
    else:
        os.environ.pop("LOG_LEVEL", None)
    importlib.reload(audit_mod)
    print("OBS-7 STRUCTLOG LEVEL FROM ENV OK")
