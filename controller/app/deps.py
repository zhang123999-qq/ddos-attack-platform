"""v1.5.0 新增: 共享依赖/工具 (main.py 拆分配套)

集中管理:
- get_orchestrator: FastAPI Depends 单例获取
- _public_base_url: 安装命令/CA 分发基址
- _audit: 审计事件便捷封装
- INSTALL_SCRIPT / ARTIFACTS_DIR 资源路径 (从 main.py 平移)
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Request

import structlog

from app.auth import auth_config
from app.models import AuditEvent
from app.audit import audit_logger
from app.orchestrator import Orchestrator

logger = structlog.get_logger(__name__)


# ========== 资源路径 (lifespan 注入) ==========

INSTALL_SCRIPT: Optional[Path] = None
ARTIFACTS_DIR: Optional[Path] = None


def _find_resource_path(env_key: str, *candidates: str) -> Optional[Path]:
    """按 环境变量 → 候选路径 顺序定位安装脚本/制品目录

    兼容: 本地仓库运行 / 容器内 /app 布局 / PyInstaller 部署布局
    """
    env_val = os.getenv(env_key)
    if env_val and Path(env_val).exists():
        return Path(env_val)
    for cand in candidates:
        p = Path(cand)
        if p.exists():
            return p
    return None


def init_resource_paths() -> None:
    """lifespan 启动时调用, 解析资源路径全局变量"""
    global INSTALL_SCRIPT, ARTIFACTS_DIR
    INSTALL_SCRIPT = _find_resource_path(
        "INSTALL_SCRIPT_PATH",
        Path(__file__).parent.parent.parent / "deploy" / "node-install.sh",
        "/app/deploy/node-install.sh",
        Path(sys.executable or "").parent / "node-install.sh",
    )
    ARTIFACTS_DIR = _find_resource_path(
        "ARTIFACTS_DIR",
        Path(__file__).parent.parent.parent / "artifacts",
        "/app/artifacts",
    )


# ========== FastAPI Depends 单例 ==========

def get_orchestrator(request: Request) -> Orchestrator:
    """FastAPI Depends: 拉取 lifespan 中创建的全局 orchestrator

    设计: 路由模块用 Depends(get_orchestrator) 而非直接 import main.orchestrator,
    避免循环导入 (路由注册晚于 orchestrator 创建)。
    """
    orch = getattr(request.app.state, "orchestrator", None) or getattr(
        sys.modules.get("app.main", sys.modules[__name__]), "orchestrator", None
    )
    if orch is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")
    return orch


# ========== 工具 ==========

NODE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{1,62}$")


def public_base_url(request: Request) -> str:
    """对外可访问的控制器基址 (scheme+host), 供安装命令/CA 分发拼接"""
    host = request.headers.get("host") or request.url.netloc
    return f"{request.url.scheme}://{host}"


async def audit_event(event_type: str, actor: str, details: dict) -> None:
    """审计事件便捷封装 — 路由模块可统一调用"""
    await audit_logger.log_event(AuditEvent(
        event_id=uuid.uuid4().hex,
        event_type=event_type,
        actor=actor,
        details=details,
    ))


async def sleep_jitter_on_auth_fail(seconds: float = 1.0) -> None:
    """认证失败后 sleep 拖慢爆破 (enroll 等无状态端点用)"""
    await asyncio.sleep(seconds)
