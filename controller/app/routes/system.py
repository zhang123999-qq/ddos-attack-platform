"""v1.5.0 新增: 系统级路由

- GET /health - 健康检查
- GET /ready - 就绪检查
- GET /metrics - Prometheus 指标
- GET /ws/metrics - WebSocket 实时推送
- GET / - WebUI 仪表盘
- GET /api/v1/controller-info - 公开元信息
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.responses import HTMLResponse, Response

from app.auth import auth_config
from app.deps import ARTIFACTS_DIR, INSTALL_SCRIPT, get_orchestrator, public_base_url
from app.models import NodeStatus
from app.orchestrator import Orchestrator
from app.websocket import websocket_endpoint

# templates 在 main.py 中注入 (ui/__init__.py 设置)
try:
    from app.ui import templates
except ImportError:
    templates = None

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "healthy", "service": "ddos-controller", "version": "1.5.0", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/ready")
async def ready(orch: Orchestrator = Depends(get_orchestrator)):
    return {
        "status": "ready",
        "emergency_stop": orch.is_emergency_active(),
        "nodes_online": len([n for n in orch.get_nodes() if n.status == NodeStatus.ONLINE]),
        "active_attacks": len(orch.get_all_attacks())
    }


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics():
    from app.metrics import render_metrics
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)


@router.websocket("/ws/metrics")
async def ws_metrics(
    websocket: WebSocket,
    token: Optional[str] = Query(None),
    channels: str = Query("nodes,attacks,metrics,alerts,system"),
    client_id: str = Query("web-ui"),
):
    await websocket_endpoint(websocket, token, channels, client_id)


@router.get("/api/v1/controller-info", include_in_schema=False)
async def controller_info(request: Request):
    artifacts = []
    if ARTIFACTS_DIR:
        artifacts = sorted(
            p.name for p in ARTIFACTS_DIR.iterdir()
            if p.is_file() and p.name.endswith(".tar.gz")
        )
    return {
        "service": "ddos-controller",
        "version": "1.5.0",
        "base_url": public_base_url(request),
        "tls_fingerprint": auth_config.get_tls_fingerprint(),
        "artifacts": artifacts,
        "install_script_available": INSTALL_SCRIPT is not None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# WebUI 路由 (依赖 templates)
def _register_dashboard(app: FastAPI) -> None:
    if not templates:
        return
    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        host_header = request.headers.get("host") or request.url.netloc
        if ":" in host_header:
            _hostname, port = host_header.rsplit(":", 1)
            try:
                port = str(int(port))
            except ValueError:
                _hostname, port = host_header, "443"
        else:
            _hostname, port = host_header, "443"
        return templates.TemplateResponse(
            request=request, name="dashboard.html",
            context={
                "request": request,
                "controller_host": request.url.hostname,
                "controller_port": port,
                "token": auth_config.ui_token(),
            },
        )


def register(app: FastAPI) -> None:
    app.include_router(router)
    _register_dashboard(app)

