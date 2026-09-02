from __future__ import annotations

import os
import asyncio
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from pathlib import Path

# 平台版本单一事实源 — 发布时只改这一处 (health/controller-info/FastAPI 均引用)
PLATFORM_VERSION = "1.5.0"
# v1.5.0: A.1 白名单默认开启 + A.2 Node mTLS 完整链路 (内置 mini-CA)

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import structlog

from app.auth import auth_config
from app.orchestrator import Orchestrator
from app.audit import audit_logger
from app.node_commander import node_commander
from app.websocket import (
    broadcast_node_update, broadcast_node_heartbeat, broadcast_attack_start,
    broadcast_attack_update, broadcast_attack_stop, broadcast_emergency_stop,
    broadcast_rate_limit_status, broadcast_system_event, broadcast_audit_event,
)
from app.deps import init_resource_paths, INSTALL_SCRIPT, ARTIFACTS_DIR

# v1.5.0: 路由拆分 (按职责拆到 app/routes/*)
from app.routes import register_all_routes

logger = structlog.get_logger(__name__)

orchestrator: Optional[Orchestrator] = None


def get_orchestrator_dep() -> Orchestrator:
    """FastAPI Depends 兼容: 返回全局 orchestrator (供 routes 使用)"""
    global orchestrator
    if orchestrator is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")
    return orchestrator


@asynccontextmanager
async def lifespan(app: FastAPI):
    global orchestrator

    # v1.5.0 安全加固: 目标白名单默认开启 (fail-closed)。
    # ALLOW_ANY_TARGET=true 显式 opt-out (兼容 v1.3.0~v1.4.x 行为, 仅供受控教学)。
    # 留空 ALLOWED_TARGET_CIDRS + 未 opt-out → 拒绝所有非占位符目标。
    allow_any_target = os.getenv("ALLOW_ANY_TARGET", "false").lower() == "true"
    allowed_cidrs_str = os.getenv("ALLOWED_TARGET_CIDRS", "10.100.0.0/16,192.168.0.0/16,172.16.0.0/12")
    allowed_cidrs: list = [c.strip() for c in allowed_cidrs_str.split(",") if c.strip()]
    global_rps = int(os.getenv("GLOBAL_MAX_RPS", "50000"))
    global_pps = int(os.getenv("GLOBAL_MAX_PPS", "100000"))
    global_concurrent = int(os.getenv("GLOBAL_MAX_CONCURRENT_CONNECTIONS", "100000"))

    orchestrator = Orchestrator(
        allowed_cidrs, global_rps, global_pps, global_concurrent,
        allow_any_target=allow_any_target,
    )
    await orchestrator.start()

    # v1.5.0: 装载 mini-CA (首次启动生成, 后续加载)
    from app.cert_authority import cert_authority
    try:
        cert_authority.bootstrap_if_needed()
    except Exception as e:
        logger.error("ca_bootstrap_failed", error=str(e))
        # 不阻塞启动 — controller 可用旧 CA 降级, 但 enroll 端点会失败

    # v1.5.0: 装载状态持久层 (R-NEW-1) — SQLite 优雅降级, 失败仅 warn
    from app.state_store import state_store
    state_store.initialize()

    # v1.5.0 (A.3): 装载 Admin API 限流器 (单例, 进程内共享)

    # M-2 修复: 接线 audit → WebSocket 广播 (原先 broadcast_audit_event 为死代码)
    audit_logger.set_broadcast_hook(broadcast_audit_event)

    async def broadcast_limits():
        while True:
            await asyncio.sleep(10)
            if orchestrator:
                status = orchestrator.get_rate_limit_status()
                await broadcast_rate_limit_status(status)

    limit_task = asyncio.create_task(broadcast_limits())

    # v1.5.0 (O-NEW-1): 后台采集 Prometheus 指标 (5s 间隔)
    from app.metrics import collect_controller_metrics
    async def collect_metrics_loop():
        while True:
            await asyncio.sleep(5)
            try:
                collect_controller_metrics(orchestrator, audit_logger)
            except Exception as e:
                logger.debug("metrics_collect_error", error=str(e))

    metrics_task = asyncio.create_task(collect_metrics_loop())

    logger.info("controller_started",
                target_restrictions="disabled" if allow_any_target else f"enabled ({len(allowed_cidrs)} cidrs)",
                allow_any_target=allow_any_target,
                allowed_cidrs_count=len(allowed_cidrs))

    yield

    limit_task.cancel()
    metrics_task.cancel()
    for t in (limit_task, metrics_task):
        try:
            await t
        except asyncio.CancelledError:
            pass

    if orchestrator:
        await orchestrator.stop()
    # v1.5.0: 关闭状态持久层 (R-NEW-1)
    from app.state_store import state_store
    state_store.close()
    logger.info("controller_stopped")


app = FastAPI(
    title="DDoS Attack Platform Controller",
    description="内网红方攻击编排控制中心 - 仅供授权教学演练使用",
    version=PLATFORM_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if os.getenv("ENABLE_WEB_UI", "true").lower() == "true" else None,
    redoc_url=None
)

# 静态文件和模板 — CRIT-5 修复: 静态目录不存在时静默降级
templates = None
if os.getenv("ENABLE_WEB_UI", "true").lower() == "true":
    # PyInstaller 冻结环境: 资源解包到 sys._MEIPASS/ui; 源码运行: controller/ui
    if getattr(sys, "frozen", False):
        ui_path = Path(getattr(sys, "_MEIPASS", ".")) / "ui"
    else:
        ui_path = Path(__file__).parent.parent / "ui"
    if ui_path.exists():
        static_dir = ui_path / "static"
        if not static_dir.exists():
            static_dir.mkdir(parents=True, exist_ok=True)
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
        templates = Jinja2Templates(directory=str(ui_path / "templates"))


# v1.5.0: 路由拆分 (B.4) — 注册各 routes/* 模块
register_all_routes(app)
if __name__ == "__main__":
    import ssl
    import uvicorn

    # HIGH-9 真实修复: uvicorn 无 ssl_context 参数, 使用 ssl_certfile/ssl_keyfile/
    # ssl_ca_certs/ssl_cert_reqs 四参数启用 TLS (0.27+ 均支持)。
    # 证书缺失时降级为明文 HTTP 并告警, 避免开发环境 crashloop。
    tls_kwargs: Dict[str, Any] = {}
    cert_file = os.getenv("TLS_CERT_FILE", "/certs/controller-cert.pem")
    key_file = os.getenv("TLS_KEY_FILE", "/certs/controller-key.pem")
    ca_file = os.getenv("TLS_CA_FILE", "/certs/ca-cert.pem")
    if Path(cert_file).exists() and Path(key_file).exists():
        tls_kwargs = {
            "ssl_certfile": cert_file,
            "ssl_keyfile": key_file,
            "ssl_ca_certs": ca_file if Path(ca_file).exists() else None,
            # 默认仅服务端 TLS; TLS_VERIFY_CLIENT=true 时强制双向认证
            "ssl_cert_reqs": ssl.CERT_REQUIRED if auth_config.verify_client else ssl.CERT_NONE,
        }
        logger.info("tls_enabled", verify_client=auth_config.verify_client)
    else:
        logger.warning("tls_disabled_missing_certs", cert=cert_file, key=key_file)

    # PyInstaller 冻结环境下无 app 包可导入 — 直接传应用对象;
    # 源码运行保持字符串引用以启用 reload 语义
    app_target = "app.main:app"
    if getattr(sys, "frozen", False):
        app_target = app
    uvicorn.run(
        app_target,
        host=os.getenv("CONTROLLER_HOST", "0.0.0.0"),
        port=int(os.getenv("CONTROLLER_PORT", "8443")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
        access_log=True,
        **tls_kwargs,
    )