"""v1.5.0 新增: 攻击编排路由

- POST /api/v1/attacks/launch - 启动攻击
- POST /api/v1/attacks/{id}/stop - 停止攻击
- POST /api/v1/emergency_stop - 熔断
- POST /api/v1/emergency_stop/reset - 复位
- GET /api/v1/attacks - 列出
- GET /api/v1/attacks/{id} - 详情
- POST /api/v1/results - 节点上报结果
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import verify_controller_token, verify_node_token
from app.audit import audit_logger
from app.deps import get_orchestrator
from app.models import (
    APIResponse, AttackCommand, AttackParams, AttackResult, AttackType,
    EmergencyStopCommand, NodeInfo, TargetSpec,
)
from app.orchestrator import Orchestrator
from app.websocket import (
    broadcast_attack_start, broadcast_attack_stop, broadcast_attack_update,
    broadcast_emergency_stop, broadcast_system_event,
)
import structlog

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["attacks"])


class LaunchAttackRequest(BaseModel):
    attack_type: AttackType
    target: TargetSpec
    duration: int = Field(default=60, ge=1, le=3600)
    rps: int = Field(default=1000, ge=1, le=100000)
    concurrency: int = Field(default=100, ge=1, le=10000)
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


@router.post("/attacks/launch", response_model=APIResponse)
async def launch_attack(
    req: LaunchAttackRequest,
    background_tasks: BackgroundTasks,
    auth: str = Depends(verify_controller_token),
    orch: Orchestrator = Depends(get_orchestrator),
):
    # v1.5.0 (A.3 / R-NEW-2): Admin API 限流
    from app.admin_rate_limit import admin_rate_limiter
    await admin_rate_limiter.check_or_raise("attacks.launch", cost=2)

    if orch.is_emergency_active():
        raise HTTPException(status_code=409, detail="Emergency stop is active. Reset before launching new attacks.")

    params = AttackParams(
        target=req.target, duration=req.duration, rps=req.rps,
        concurrency=req.concurrency, method=req.method, headers=req.headers,
        body=req.body, use_https=req.use_https, verify_ssl=req.verify_ssl,
        source_ip_spoof=req.source_ip_spoof, spoof_cidr=req.spoof_cidr,
        interface=req.interface, slowloris_interval=req.slowloris_interval,
        reflector_type=req.reflector_type, reflector_list=req.reflector_list,
    )
    command = AttackCommand(
        attack_id=f"atk-{uuid.uuid4().hex[:12]}",
        attack_type=req.attack_type, params=params,
        scenario_id=req.scenario_id, node_ids=req.node_ids,
    )

    try:
        result = await orch.launch_attack(command)
        await broadcast_attack_start({
            "attack_id": command.attack_id,
            "type": req.attack_type.value,
            "target": req.target.model_dump(mode='json'),
            "target_nodes": result.get("target_nodes", []),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "command": command.model_dump(mode='json'),
            "status": "running",
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


# 列表 / 详情 / 停止 / 熔断
@router.get("/attacks", response_model=APIResponse)
async def list_attacks(
    auth: str = Depends(verify_controller_token),
    orch: Orchestrator = Depends(get_orchestrator),
):
    return APIResponse(success=True, data=orch.get_all_attacks())


@router.get("/attacks/{attack_id}", response_model=APIResponse)
async def get_attack(
    attack_id: str,
    auth: str = Depends(verify_controller_token),
    orch: Orchestrator = Depends(get_orchestrator),
):
    if attack_id in ("launch", "stop"):
        raise HTTPException(
            status_code=405,
            detail=f"'{attack_id}' is an action path, not an attack id (use POST /api/v1/attacks/{attack_id})"
        )
    status = orch.get_attack_status(attack_id)
    if not status:
        raise HTTPException(status_code=404, detail="Attack not found")
    return APIResponse(success=True, data=status)


@router.post("/attacks/{attack_id}/stop", response_model=APIResponse)
async def stop_attack(
    attack_id: str,
    reason: str = "manual",
    auth: str = Depends(verify_controller_token),
    orch: Orchestrator = Depends(get_orchestrator),
):
    # v1.5.0 (A.3): 限流
    from app.admin_rate_limit import admin_rate_limiter
    await admin_rate_limiter.check_or_raise("attacks.stop")
    result = await orch.stop_attack(attack_id, reason)
    await broadcast_attack_stop(attack_id, reason)
    return APIResponse(success=result["stopped"], data=result, message="Attack stop requested")


@router.post("/emergency_stop", response_model=APIResponse)
async def emergency_stop(
    command: EmergencyStopCommand,
    auth: str = Depends(verify_controller_token),
    orch: Orchestrator = Depends(get_orchestrator),
):
    # v1.5.0 (A.3): 限流 — emergency_stop 高成本, 限流更严 (cost=5, 等同于 12 RPM)
    from app.admin_rate_limit import admin_rate_limiter
    await admin_rate_limiter.check_or_raise("emergency_stop", cost=5)
    result = await orch.emergency_stop(command)
    await broadcast_emergency_stop(command.reason, command.issued_by)
    return APIResponse(success=True, data=result, message="Emergency stop executed")


@router.post("/emergency_stop/reset", response_model=APIResponse)
async def reset_emergency_stop(
    auth: str = Depends(verify_controller_token),
    orch: Orchestrator = Depends(get_orchestrator),
):
    # v1.5.0 (A.3): 限流
    from app.admin_rate_limit import admin_rate_limiter
    await admin_rate_limiter.check_or_raise("emergency_reset", cost=3)
    orch.reset_emergency_stop()
    await broadcast_system_event("emergency_stop_reset", {"reset_by": "controller"})
    return APIResponse(success=True, message="Emergency stop reset")


@router.post("/results", response_model=APIResponse)
async def collect_result(
    result: AttackResult,
    auth_node: NodeInfo = Depends(verify_node_token),
    orch: Orchestrator = Depends(get_orchestrator),
):
    # v1.5.0 (A.3): 节点上报限流 (防止节点伪造高频上报)
    from app.admin_rate_limit import admin_rate_limiter
    await admin_rate_limiter.check_or_raise("results.collect")
    if result.node_id != auth_node.node_id:
        raise HTTPException(status_code=403, detail="Node ID mismatch with authenticated identity")
    orch.collect_result(result)
    await broadcast_attack_update(result.attack_id, result.node_id, result)
    return APIResponse(success=True)


def register(app) -> None:
    app.include_router(router)
