"""v1.5.0 新增: 内部调试端点 (仅供运维/开发)

- GET /api/v1/internal/node_commander_status - node_commander 内部状态
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import verify_controller_token
from app.node_commander import node_commander

router = APIRouter(prefix="/api/v1/internal", tags=["internal"])


@router.get("/node_commander_status", include_in_schema=False)
async def node_commander_status(auth: str = Depends(verify_controller_token)):
    return {
        "node_count": len(node_commander._nodes),
        "nodes": list(node_commander._nodes.keys()),
    }


def register(app) -> None:
    app.include_router(router)
