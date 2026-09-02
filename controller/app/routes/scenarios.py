"""v1.5.0 新增: 场景管理路由

- GET /api/v1/scenarios - 列表
- GET /api/v1/scenarios/{id} - 详情
- POST /api/v1/scenarios/{id}/run - 运行
- POST /api/v1/scenarios/{id}/stop - 停止
- GET /api/v1/rate-limits - 限流状态
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import verify_controller_token
from app.deps import get_orchestrator
from app.models import APIResponse
from app.orchestrator import Orchestrator

router = APIRouter(prefix="/api/v1", tags=["scenarios"])


class RunScenarioRequest(BaseModel):
    overrides: Optional[Dict[str, Any]] = None


@router.get("/scenarios", response_model=APIResponse)
async def list_scenarios(
    auth: str = Depends(verify_controller_token),
    orch: Orchestrator = Depends(get_orchestrator),
):
    scenarios = orch.get_scenarios()
    return APIResponse(success=True, data=[s.model_dump(mode='json') for s in scenarios])


@router.get("/scenarios/{scenario_id}", response_model=APIResponse)
async def get_scenario(
    scenario_id: str,
    auth: str = Depends(verify_controller_token),
    orch: Orchestrator = Depends(get_orchestrator),
):
    scenarios = orch.get_scenarios()
    scenario = next((s for s in scenarios if s.scenario_id == scenario_id), None)
    if not scenario:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return APIResponse(success=True, data=scenario.model_dump(mode='json'))


@router.post("/scenarios/{scenario_id}/run", response_model=APIResponse)
async def run_scenario(
    scenario_id: str,
    req: RunScenarioRequest,
    auth: str = Depends(verify_controller_token),
    orch: Orchestrator = Depends(get_orchestrator),
):
    # v1.5.0 (A.3): 限流
    from app.admin_rate_limit import admin_rate_limiter
    await admin_rate_limiter.check_or_raise("scenarios.run", cost=3)
    if orch.is_emergency_active():
        raise HTTPException(status_code=409, detail="Emergency stop is active")
    try:
        run_id = await orch.run_scenario(scenario_id, req.overrides)
    except ValueError as e:
        # H-2 修复: overrides 缺失/非法同步返回 400, 不再 200+静默失败
        raise HTTPException(status_code=400, detail=str(e))
    return APIResponse(success=True, data={"run_id": run_id}, message="Scenario started")


@router.post("/scenarios/{scenario_id}/stop", response_model=APIResponse)
async def stop_scenario(
    scenario_id: str,
    auth: str = Depends(verify_controller_token),
    orch: Orchestrator = Depends(get_orchestrator),
):
    # v1.5.0 (A.3): 限流
    from app.admin_rate_limit import admin_rate_limiter
    await admin_rate_limiter.check_or_raise("scenarios.stop")
    await orch.stop_scenario(scenario_id)
    return APIResponse(success=True, message="Scenario stop requested")


@router.get("/rate-limits", response_model=APIResponse)
async def get_rate_limits(
    auth: str = Depends(verify_controller_token),
    orch: Orchestrator = Depends(get_orchestrator),
):
    return APIResponse(success=True, data=orch.get_rate_limit_status())


def register(app) -> None:
    app.include_router(router)
