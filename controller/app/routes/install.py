"""v1.5.0 新增: 安装脚本 / CA / 制品分发路由

- GET /install.sh - 节点安装脚本 (替换 __CONTROLLER_URL__ 占位符)
- GET /artifacts/ca-cert.pem - CA 证书
- GET /artifacts/* - 制品目录挂载
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.auth import auth_config
from app.deps import ARTIFACTS_DIR, INSTALL_SCRIPT, audit_event, public_base_url

router = APIRouter()


@router.get("/install.sh", include_in_schema=False)
async def serve_install_script(request: Request):
    """分发节点安装器; __CONTROLLER_URL__ 占位符按请求地址替换"""
    if not INSTALL_SCRIPT:
        raise HTTPException(status_code=404, detail="install script not bundled")
    base = public_base_url(request)
    body = INSTALL_SCRIPT.read_bytes().decode("utf-8")
    body = body.replace("__CONTROLLER_URL__", base)
    await audit_event("config_change", "system", {
        "action": "install_script_served", "controller_url": base
    })
    return PlainTextResponse(body, media_type="text/x-shellscript; charset=utf-8")


@router.get("/artifacts/ca-cert.pem", include_in_schema=False)
async def serve_ca_cert():
    ca = Path(auth_config.ca_cert_path)
    if not ca.exists():
        raise HTTPException(status_code=404, detail="CA cert not available on controller")
    return FileResponse(ca, media_type="application/x-pem-file", filename="ca-cert.pem")


def register(app) -> None:
    app.include_router(router)
    # 制品目录挂载 (必须在静态路由注册后, 避免覆盖 install.sh / ca-cert.pem)
    if ARTIFACTS_DIR:
        app.mount("/artifacts", StaticFiles(directory=str(ARTIFACTS_DIR)), name="artifacts")
