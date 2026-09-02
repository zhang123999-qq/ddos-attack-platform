"""v1.5.0 新增: 节点管理路由

- POST /api/v1/nodes/register - 节点注册
- POST /api/v1/nodes/heartbeat - 心跳上报
- POST /api/v1/nodes/{node_id}/unregister - 节点注销
- GET /api/v1/nodes - 节点列表
- GET /api/v1/nodes/enroll-command - 生成安装命令
- GET /api/v1/nodes/{node_id} - 节点详情
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.auth import auth_config, verify_controller_token, verify_node_token
from app.deps import NODE_ID_RE, audit_event, get_orchestrator, public_base_url
from app.models import APIResponse, NodeInfo, NodeHeartbeat, NodeStatus
from app.orchestrator import Orchestrator
from app.websocket import broadcast_node_update

# 每个子模块独立 router, 避免在 register() 之前被 import
router = APIRouter(prefix="/api/v1/nodes", tags=["nodes"])


# nodes/{node_id} 静态路由必须在动态段之前注册 — 借由 include_in_schema=False 单独 @app.get
# 这里改用 router.add_api_route 的 order 控制 (FastAPI 0.93+)


@router.post("/register", response_model=APIResponse)
async def register_node(
    request: Request,
    node: NodeInfo,
    auth_node: NodeInfo = Depends(verify_node_token),
    orch: Orchestrator = Depends(get_orchestrator),
):
    # S-3 修复: 注册身份必须与已认证的 X-Node-ID 一致
    if node.node_id != auth_node.node_id:
        raise HTTPException(status_code=403, detail="Node ID mismatch with authenticated identity")
    # BUG-18 防护: 节点上报回环地址时, 用 TLS 连接的真实来源 IP 替代
    if node.ip in ("127.0.0.1", "::1", "0.0.0.0", ""):
        client_host = request.client.host if request.client else node.ip
        node = node.model_copy(update={"ip": client_host})
    registered = await orch.register_node(node)
    await broadcast_node_update(registered.model_dump(mode='json'))
    return APIResponse(success=True, data=registered.model_dump(mode='json'), message="Node registered")


@router.post("/heartbeat", response_model=APIResponse)
async def node_heartbeat(
    hb: NodeHeartbeat,
    auth_node: NodeInfo = Depends(verify_node_token),
    orch: Orchestrator = Depends(get_orchestrator),
):
    if hb.node_id != auth_node.node_id:
        raise HTTPException(status_code=403, detail="Node ID mismatch with authenticated identity")
    await orch.node_heartbeat(hb)
    from app.websocket import broadcast_node_heartbeat
    await broadcast_node_heartbeat(hb)
    return APIResponse(success=True)


# 静态路由 enroll-command 必须在 /{node_id} 之前
@router.get("/enroll-command", response_model=APIResponse)
async def enroll_command(
    request: Request,
    type: str = Query("http", pattern="^(http|raw)$"),
    node_id: str = Query(..., min_length=2, max_length=63),
    auth: str = Depends(verify_controller_token),
):
    """管理员生成节点一键安装命令 (WebUI「添加节点」数据源)"""
    # v1.5.0 (A.3): 限流
    from app.admin_rate_limit import admin_rate_limiter
    await admin_rate_limiter.check_or_raise("nodes.enroll_command", cost=2)
    if not NODE_ID_RE.match(node_id):
        raise HTTPException(status_code=400, detail="node_id 仅允许字母数字/-/_ , 2-63 字符")

    token = auth_config.generate_enroll_token(node_id)
    fingerprint = auth_config.get_tls_fingerprint().replace(":", "").lower()
    base = public_base_url(request)
    from app.deps import INSTALL_SCRIPT
    script_src = (
        f"{base}/install.sh"
        if INSTALL_SCRIPT
        else "https://raw.githubusercontent.com/zhang123999-qq/ddos-attack-platform/master/deploy/node-install.sh"
    )
    cmd = (
        f"bash <(curl -Lsk {script_src}) "
        f"-e {base} "
        f"-t {token} "
        f"--id {node_id} "
        f"--type {type}"
        + (f" --fingerprint {fingerprint}" if fingerprint else "")
    )
    now = datetime.now(timezone.utc)
    expiry = now.replace(minute=59, second=59, microsecond=0) + timedelta(hours=1)
    await audit_event("enroll_command_issued", "authenticated_user",
                      {"node_id": node_id, "type": type})
    return APIResponse(success=True, data={
        "command": cmd,
        "node_id": node_id,
        "type": type,
        "expires_at": expiry.isoformat(),
        "tls_fingerprint": auth_config.get_tls_fingerprint(),
    })


@router.get("", response_model=APIResponse)
async def list_nodes(
    auth: str = Depends(verify_controller_token),
    orch: Orchestrator = Depends(get_orchestrator),
):
    nodes = orch.get_nodes()
    return APIResponse(success=True, data=[n.model_dump(mode='json') for n in nodes])


@router.post("/{node_id}/unregister", response_model=APIResponse)
async def unregister_node(
    node_id: str,
    auth_node: NodeInfo = Depends(verify_node_token),
    orch: Orchestrator = Depends(get_orchestrator),
):
    if node_id != auth_node.node_id:
        raise HTTPException(status_code=403, detail="Cannot unregister another node's identity")
    await orch.unregister_node(node_id)
    await broadcast_node_update({"node_id": node_id, "status": NodeStatus.OFFLINE.value})
    return APIResponse(success=True, message="Node unregistered")


@router.get("/{node_id}", response_model=APIResponse)
async def get_node(
    node_id: str,
    auth: str = Depends(verify_controller_token),
    orch: Orchestrator = Depends(get_orchestrator),
):
    # BUG-6: 读全量节点字典 — offline 节点详情同样可查
    node = orch.get_node_by_id(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return APIResponse(success=True, data=node.model_dump(mode='json'))


def register(app) -> None:
    """挂载到主应用 — 注意: 静态路由顺序敏感"""
    # 必须先注册 enroll-command 和 get node_id 详情 (动态段之前)
    app.include_router(router)
