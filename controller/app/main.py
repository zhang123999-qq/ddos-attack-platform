from __future__ import annotations

import os
import uuid
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path

from fastapi import FastAPI, Request, Depends, HTTPException, Query, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import structlog

from app.auth import verify_controller_token, verify_node_token, auth_config
from app.models import (
    AttackCommand, AttackParams, AttackType, AttackStatus, AttackResult,
    NodeInfo, NodeHeartbeat, NodeStatus, Scenario, EmergencyStopCommand,
    TargetSpec, APIResponse, PaginatedResponse
)
from app.orchestrator import Orchestrator
from app.audit import audit_logger
from app.node_commander import node_commander
from app.websocket import (
    websocket_endpoint, manager, broadcast_node_update, broadcast_attack_start,
    broadcast_attack_update, broadcast_attack_stop, broadcast_emergency_stop,
    broadcast_rate_limit_status, broadcast_system_event
)

logger = structlog.get_logger(__name__)

orchestrator: Optional[Orchestrator] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global orchestrator

    allowed_cidrs = [c.strip() for c in os.getenv("ALLOWED_TARGET_CIDRS", "10.0.0.0/8").split(",")]
    global_rps = int(os.getenv("GLOBAL_MAX_RPS", "50000"))
    global_pps = int(os.getenv("GLOBAL_MAX_PPS", "100000"))
    global_concurrent = int(os.getenv("GLOBAL_MAX_CONCURRENT_CONNECTIONS", "100000"))

    orchestrator = Orchestrator(allowed_cidrs, global_rps, global_pps, global_concurrent)
    await orchestrator.start()

    async def broadcast_limits():
        while True:
            await asyncio.sleep(10)
            if orchestrator:
                status = orchestrator.get_rate_limit_status()
                await broadcast_rate_limit_status(status)

    limit_task = asyncio.create_task(broadcast_limits())

    logger.info("controller_started", allowed_cidrs=allowed_cidrs)

    yield

    limit_task.cancel()
    try:
        await limit_task
    except asyncio.CancelledError:
        pass

    if orchestrator:
        await orchestrator.stop()
    logger.info("controller_stopped")


app = FastAPI(
    title="DDoS Attack Platform Controller",
    description="内网红方攻击编排控制中心 - 仅供授权教学演练使用",
    version="1.1.0",
    lifespan=lifespan,
    docs_url="/docs" if os.getenv("ENABLE_WEB_UI", "true").lower() == "true" else None,
    redoc_url=None
)

# 静态文件和模板 — CRIT-5 修复: 静态目录不存在时静默降级
templates = None
if os.getenv("ENABLE_WEB_UI", "true").lower() == "true":
    ui_path = Path(__file__).parent.parent / "ui"
    if ui_path.exists():
        static_dir = ui_path / "static"
        if not static_dir.exists():
            static_dir.mkdir(parents=True, exist_ok=True)
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
        templates = Jinja2Templates(directory=str(ui_path / "templates"))


def get_orchestrator() -> Orchestrator:
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")
    return orchestrator


# ========== 健康检查 ==========

@app.get("/health")
async def health():
    return {"status": "healthy", "service": "ddos-controller", "version": "1.1.0", "timestamp": datetime.utcnow().isoformat()}


@app.get("/ready")
async def ready(orch: Orchestrator = Depends(get_orchestrator)):
    return {
        "status": "ready",
        "emergency_stop": orch.is_emergency_active(),
        "nodes_online": len([n for n in orch.get_nodes() if n.status == NodeStatus.ONLINE]),
        "active_attacks": len(orch.get_all_attacks())
    }


# ========== 节点管理 ==========

@app.post("/api/v1/nodes/register", response_model=APIResponse)
async def register_node(
    node: NodeInfo,
    auth_node: NodeInfo = Depends(verify_node_token),
    orch: Orchestrator = Depends(get_orchestrator)
):
    registered = await orch.register_node(node)
    await broadcast_node_update(registered.model_dump())
    return APIResponse(success=True, data=registered.model_dump(), message="Node registered")


@app.post("/api/v1/nodes/heartbeat", response_model=APIResponse)
async def node_heartbeat(
    hb: NodeHeartbeat,
    auth_node: NodeInfo = Depends(verify_node_token),
    orch: Orchestrator = Depends(get_orchestrator)
):
    await orch.node_heartbeat(hb)
    await broadcast_node_heartbeat(hb)
    return APIResponse(success=True)


@app.post("/api/v1/nodes/{node_id}/unregister", response_model=APIResponse)
async def unregister_node(
    node_id: str,
    auth_node: NodeInfo = Depends(verify_node_token),
    orch: Orchestrator = Depends(get_orchestrator)
):
    await orch.unregister_node(node_id)
    await broadcast_node_update({"node_id": node_id, "status": NodeStatus.OFFLINE.value})
    return APIResponse(success=True, message="Node unregistered")


# ========== 攻击编排 ==========

class LaunchAttackRequest(BaseModel):
    attack_type: AttackType
    target: TargetSpec
    duration: int = 60
    rps: int = 1000
    concurrency: int = 100
    scenario_id: Optional[str] = None
    node_ids: List[str] = []
    method: str = "GET"
    headers: Dict[str, str] = {}
    body: Optional[str] = None
    use_https: bool = False
    verify_ssl: bool = False
    source_ip_spoof: bool = False
    spoof_cidr: Optional[str] = None
    interface: Optional[str] = None
    slowloris_interval: int = 15
    reflector_type: Optional[str] = None
    reflector_list: Optional[List[str]] = None


@app.post("/api/v1/attacks/launch", response_model=APIResponse)
async def launch_attack(
    req: LaunchAttackRequest,
    background_tasks: BackgroundTasks,
    auth: str = Depends(verify_controller_token),
    orch: Orchestrator = Depends(get_orchestrator)
):
    if orch.is_emergency_active():
        raise HTTPException(status_code=409, detail="Emergency stop is active. Reset before launching new attacks.")

    params = AttackParams(
        target=req.target,
        duration=req.duration,
        rps=req.rps,
        concurrency=req.concurrency,
        method=req.method,
        headers=req.headers,
        body=req.body,
        use_https=req.use_https,
        verify_ssl=req.verify_ssl,
        source_ip_spoof=req.source_ip_spoof,
        spoof_cidr=req.spoof_cidr,
        interface=req.interface,
        slowloris_interval=req.slowloris_interval,
        reflector_type=req.reflector_type,
        reflector_list=req.reflector_list
    )

    command = AttackCommand(
        attack_id=f"atk-{uuid.uuid4().hex[:12]}",
        attack_type=req.attack_type,
        params=params,
        scenario_id=req.scenario_id,
        node_ids=req.node_ids
    )

    try:
        result = await orch.launch_attack(command)
        await broadcast_attack_start({
            "attack_id": command.attack_id,
            "type": req.attack_type.value,
            "target": req.target.model_dump(),
            "target_nodes": result.get("target_nodes", []),
        })
        return APIResponse(success=True, data=result, message="Attack launched")
    except ValueError as e:
        await audit_logger.log_target_validation_failure("controller", str(req.target.ip), str(e))
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error("launch_attack_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Launch failed: {e}")


@app.post("/api/v1/attacks/{attack_id}/stop", response_model=APIResponse)
async def stop_attack(
    attack_id: str,
    reason: str = "manual",
    auth: str = Depends(verify_controller_token),
    orch: Orchestrator = Depends(get_orchestrator)
):
    result = await orch.stop_attack(attack_id, reason)
    await broadcast_attack_stop(attack_id, reason)
    return APIResponse(success=result["stopped"], data=result, message="Attack stop requested")


@app.post("/api/v1/emergency_stop", response_model=APIResponse)
async def emergency_stop(
    command: EmergencyStopCommand,
    auth: str = Depends(verify_controller_token),
    orch: Orchestrator = Depends(get_orchestrator)
):
    result = await orch.emergency_stop(command)
    await broadcast_emergency_stop(command.reason, command.issued_by)
    return APIResponse(success=True, data=result, message="Emergency stop executed")


@app.post("/api/v1/emergency_stop/reset", response_model=APIResponse)
async def reset_emergency_stop(
    auth: str = Depends(verify_controller_token),
    orch: Orchestrator = Depends(get_orchestrator)
):
    orch.reset_emergency_stop()
    await broadcast_system_event("emergency_stop_reset", {"reset_by": "controller"})
    return APIResponse(success=True, message="Emergency stop reset")


@app.get("/api/v1/attacks", response_model=APIResponse)
async def list_attacks(
    auth: str = Depends(verify_controller_token),
    orch: Orchestrator = Depends(get_orchestrator)
):
    attacks = orch.get_all_attacks()
    return APIResponse(success=True, data=attacks)


@app.get("/api/v1/attacks/{attack_id}", response_model=APIResponse)
async def get_attack(
    attack_id: str,
    auth: str = Depends(verify_controller_token),
    orch: Orchestrator = Depends(get_orchestrator)
):
    status = orch.get_attack_status(attack_id)
    if not status:
        raise HTTPException(status_code=404, detail="Attack not found")
    return APIResponse(success=True, data=status)


# ========== 结果收集 ==========

@app.post("/api/v1/results", response_model=APIResponse)
async def collect_result(
    result: AttackResult,
    auth_node: NodeInfo = Depends(verify_node_token),
    orch: Orchestrator = Depends(get_orchestrator)
):
    orch.collect_result(result)
    await broadcast_attack_update(result.attack_id, result.node_id, result)
    return APIResponse(success=True)


# ========== 节点查询 ==========

@app.get("/api/v1/nodes", response_model=APIResponse)
async def list_nodes(
    auth: str = Depends(verify_controller_token),
    orch: Orchestrator = Depends(get_orchestrator)
):
    nodes = orch.get_nodes()
    return APIResponse(success=True, data=[n.model_dump() for n in nodes])


@app.get("/api/v1/nodes/{node_id}", response_model=APIResponse)
async def get_node(
    node_id: str,
    auth: str = Depends(verify_controller_token),
    orch: Orchestrator = Depends(get_orchestrator)
):
    node = orch.get_nodes()
    node = next((n for n in node if n.node_id == node_id), None)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return APIResponse(success=True, data=node.model_dump())


# ========== 限流状态 ==========

@app.get("/api/v1/rate-limits", response_model=APIResponse)
async def get_rate_limits(
    auth: str = Depends(verify_controller_token),
    orch: Orchestrator = Depends(get_orchestrator)
):
    status = orch.get_rate_limit_status()
    return APIResponse(success=True, data=status)


# ========== 场景管理 ==========

@app.get("/api/v1/scenarios", response_model=APIResponse)
async def list_scenarios(
    auth: str = Depends(verify_controller_token),
    orch: Orchestrator = Depends(get_orchestrator)
):
    scenarios = orch.get_scenarios()
    return APIResponse(success=True, data=[s.model_dump() for s in scenarios])


@app.get("/api/v1/scenarios/{scenario_id}", response_model=APIResponse)
async def get_scenario(
    scenario_id: str,
    auth: str = Depends(verify_controller_token),
    orch: Orchestrator = Depends(get_orchestrator)
):
    scenario = orch.get_scenarios()
    scenario = next((s for s in scenario if s.scenario_id == scenario_id), None)
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return APIResponse(success=True, data=scenario.model_dump())


class RunScenarioRequest(BaseModel):
    overrides: Optional[Dict[str, Any]] = None


@app.post("/api/v1/scenarios/{scenario_id}/run", response_model=APIResponse)
async def run_scenario(
    scenario_id: str,
    req: RunScenarioRequest,
    auth: str = Depends(verify_controller_token),
    orch: Orchestrator = Depends(get_orchestrator)
):
    if orch.is_emergency_active():
        raise HTTPException(status_code=409, detail="Emergency stop is active")

    run_id = await orch.run_scenario(scenario_id, req.overrides)
    return APIResponse(success=True, data={"run_id": run_id}, message="Scenario started")


@app.post("/api/v1/scenarios/{scenario_id}/stop", response_model=APIResponse)
async def stop_scenario(
    scenario_id: str,
    auth: str = Depends(verify_controller_token),
    orch: Orchestrator = Depends(get_orchestrator)
):
    await orch.stop_scenario(scenario_id)
    return APIResponse(success=True, message="Scenario stop requested")


# ========== WebSocket ==========

@app.websocket("/ws/metrics")
async def ws_metrics(
    websocket: WebSocket,
    token: str = Query(...),
    channels: str = Query("nodes,attacks,metrics,alerts,system"),
    client_id: str = Query("web-ui")
):
    await websocket_endpoint(websocket, token, channels, client_id)


# ========== Web UI ==========

if templates:

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        return templates.TemplateResponse("dashboard.html", {"request": request, "controller_host": request.url.hostname})


# ========== 内部调试端点 ==========

@app.get("/api/v1/internal/node_commander_status", include_in_schema=False)
async def node_commander_status(auth: str = Depends(verify_controller_token)):
    return {
        "node_count": len(node_commander._nodes),
        "nodes": list(node_commander._nodes.keys()),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.getenv("CONTROLLER_HOST", "0.0.0.0"),
        port=int(os.getenv("CONTROLLER_PORT", "8443")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
        access_log=True,
        # HIGH-9 修复: 不使用分离的 ssl_* 参数，改用 ssl_context
    )