from __future__ import annotations

import os
import sys
import asyncio
import hmac
import threading
import uuid
import signal
import socket
import platform
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Callable, Awaitable
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, Depends, Header
from fastapi.responses import JSONResponse
import httpx

# 平台版本单一事实源 — 发布时只改这一处
PLATFORM_VERSION = "1.3.3"
import structlog

from app.models import (
    NodeInfo, NodeHeartbeat, NodeStatus, AttackCommand, AttackResult,
    AttackType, AttackStatus, EmergencyStopCommand
)
from app.crypto import node_crypto
from app.health import HealthMonitor
from app.attacks import AttackRegistry, SafeAttackBase

logger = structlog.get_logger(__name__)


# 全局状态
node_info: Optional[NodeInfo] = None
health_monitor: Optional[HealthMonitor] = None
http_client: Optional[httpx.AsyncClient] = None
current_attacks: Dict[str, asyncio.Task] = {}
attack_instances: Dict[str, Any] = {}  # attack_id -> attack_instance
_shutdown_event = asyncio.Event()

# BUG-2: 心跳线程化 — 独立 OS 线程 + threading.Event (不可跨线程复用 asyncio.Event)
_hb_thread: Optional[threading.Thread] = None
_hb_stop = threading.Event()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global node_info, health_monitor, http_client
    
    # 验证证书
    if not node_crypto.validate_cert_files():
        logger.error("cert_validation_failed")
        raise RuntimeError("Certificate validation failed")
    
    # 初始化节点信息
    base_info = NodeInfo(
        node_id=node_crypto.node_id,
        node_type=os.getenv("NODE_TYPE", "http"),
        ip="",  # 将由 HealthMonitor 填充
        hostname=platform.node(),
        cpu_cores=0,
        memory_gb=0,
        max_rps=int(os.getenv("MAX_RPS", "10000")),
        max_pps=int(os.getenv("MAX_PPS", "50000")),
        max_concurrent=int(os.getenv("MAX_CONCURRENT", "5000")),
        labels=dict(
            item.split("=") for item in os.getenv("NODE_LABELS", "").split(",") if "=" in item
        )
    )
    
    health_monitor = HealthMonitor(base_info)
    node_info = health_monitor.get_node_info()
    
    # HIGH-4 修复: 正确创建带 mTLS 的 httpx 客户端
    ssl_context = node_crypto.create_ssl_context()
    transport = httpx.AsyncHTTPTransport(verify=ssl_context)
    http_client = httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(30.0, connect=10.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
    )
    
    # 注册到 Controller
    await register_with_controller()

    # BUG-2: 心跳移入独立 OS 线程 — 大参数攻击饱和事件循环时心跳仍准点
    _hb_stop.clear()
    global _hb_thread
    _hb_thread = threading.Thread(target=_heartbeat_thread_main, name="ddos-heartbeat", daemon=True)
    _hb_thread.start()
    
    logger.info("attacker_node_started", node_id=node_crypto.node_id, type=node_info.node_type)
    
    yield
    
    # 关闭
    _shutdown_event.set()
    
    # 停止所有攻击
    for attack_id, task in current_attacks.items():
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    
    # 停止心跳线程 (threading.Event 跨线程安全; join 有界防卡退出)
    _hb_stop.set()
    if _hb_thread is not None:
        _hb_thread.join(timeout=5)
        _hb_thread = None
    
    # 注销
    await unregister_from_controller()
    
    if http_client:
        await http_client.aclose()
    
    logger.info("attacker_node_stopped", node_id=node_crypto.node_id)


app = FastAPI(
    title="DDoS Attack Node",
    description="分布式攻击节点 - 仅供授权内网教学演练使用",
    version=PLATFORM_VERSION,
    lifespan=lifespan
)


# ========== 依赖注入 ==========

async def verify_node_auth(
    x_node_id: str = Header(..., alias="X-Node-ID"),
    x_node_token: str = Header(..., alias="X-Node-Token")
):
    """验证 Controller 下发指令的认证"""
    # 双向认证：mTLS 已在传输层验证，这里再验证 Token
    if x_node_id != node_crypto.node_id:
        raise HTTPException(status_code=401, detail="Node ID mismatch")
    
    # 验证 Token (Controller 使用共享密钥生成)
    if not node_crypto.verify_controller_token(x_node_token):
        raise HTTPException(status_code=401, detail="Invalid controller token")
    
    return True


# ========== Controller 通信 ==========

async def register_with_controller():
    """向 Controller 注册节点 — 带退避重试 (P2: 原实现失败即崩溃退出)"""
    url = f"{node_crypto.controller_url}/api/v1/nodes/register"
    headers = node_crypto.get_auth_headers()

    max_attempts = int(os.getenv("REGISTER_MAX_RETRIES", "10"))
    retry_delay = float(os.getenv("REGISTER_RETRY_DELAY", "3"))
    last_error: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            resp = await http_client.post(url, json=node_info.model_dump(mode='json'), headers=headers)
            resp.raise_for_status()
            logger.info("controller_registered", node_id=node_crypto.node_id, attempt=attempt)
            return
        except Exception as e:
            last_error = e
            logger.warning("controller_register_retry",
                           node_id=node_crypto.node_id, attempt=attempt,
                           max_attempts=max_attempts, error=str(e))
            if attempt < max_attempts:
                await asyncio.sleep(retry_delay * attempt)  # 线性退避

    logger.error("controller_register_failed", error=str(last_error))
    raise RuntimeError(f"Cannot register with controller after {max_attempts} attempts: {last_error}")


async def unregister_from_controller():
    """从 Controller 注销"""
    if not http_client:
        return
    url = f"{node_crypto.controller_url}/api/v1/nodes/{node_crypto.node_id}/unregister"
    headers = node_crypto.get_auth_headers()
    
    try:
        await http_client.post(url, headers=headers)
        logger.info("controller_unregistered", node_id=node_crypto.node_id)
    except Exception as e:
        logger.warning("controller_unregister_failed", error=str(e))


def _register_sync(client: "httpx.Client") -> bool:
    """BUG-4 兜底: 心跳线程内的同步全量重注册 (幂等)。
    控制器重启会清空节点表, 节点侧周期性重发 register 保证 ≤一个周期内自愈。"""
    try:
        if health_monitor is None:
            return False
        resp = client.post(
            f"{node_crypto.controller_url}/api/v1/nodes/register",
            json=health_monitor.get_node_info().model_dump(mode='json'),
            headers=node_crypto.get_auth_headers(),
        )
        resp.raise_for_status()
        logger.info("controller_registered_sync")
        return True
    except Exception as e:
        logger.warning("controller_register_sync_failed", error=str(e))
        return False


def _heartbeat_thread_main():
    """BUG-2: 心跳主循环运行在独立 OS 线程。
    - 不与攻击 worker 共享 asyncio 事件循环 → 错误风暴不再延迟心跳
    - 独立同步 httpx.Client (AsyncClient 非线程安全, 不可跨线程复用)
    - BUG-4: 收到 401/403/404 (控制器不认识本节点) 时立即全量重注册;
      另按 REGISTER_REFRESH_INTERVAL 周期性幂等重注册兜底控制器重启场景"""
    interval = max(3, int(os.getenv("HEARTBEAT_INTERVAL", "10")))
    refresh_period = max(interval, int(os.getenv("REGISTER_REFRESH_INTERVAL", "60")))
    refresh_every_beats = max(1, refresh_period // interval)

    try:
        ssl_ctx = node_crypto.create_ssl_context()
    except SystemExit:
        logger.error("heartbeat_thread_abort_tls_context")
        return
    client = httpx.Client(verify=ssl_ctx, timeout=httpx.Timeout(10.0, connect=5.0))
    beats_since_register = 0

    try:
        while not _hb_stop.is_set():
            try:
                if health_monitor is None or node_info is None:
                    break
                hb = health_monitor.collect_heartbeat()
                resp = client.post(
                    f"{node_crypto.controller_url}/api/v1/nodes/heartbeat",
                    json=hb.model_dump(mode='json'),
                    headers=node_crypto.get_auth_headers(),
                )
                if resp.status_code == 200:
                    beats_since_register += 1
                else:
                    logger.warning("heartbeat_thread_non_ok", status=resp.status_code)
                    if resp.status_code in (401, 403, 404):
                        # 身份仍有效但控制器已失忆 → 立刻重建注册
                        if _register_sync(client):
                            beats_since_register = 0
            except Exception as e:
                logger.warning("heartbeat_thread_failed", error=str(e))

            if _hb_stop.is_set():
                break
            # 周期性幂等重注册: 覆盖"控制器重启但未拒绝过心跳"的窗口
            if beats_since_register >= refresh_every_beats:
                _register_sync(client)
                beats_since_register = 0

            _hb_stop.wait(interval)
    finally:
        client.close()


async def send_attack_result(result: AttackResult):
    """发送攻击结果到 Controller"""
    if not http_client:
        return
    
    url = f"{node_crypto.controller_url}/api/v1/results"
    headers = node_crypto.get_auth_headers()
    
    try:
        resp = await http_client.post(url, json=result.model_dump(mode='json'), headers=headers)
        resp.raise_for_status()
    except Exception as e:
        logger.warning("send_result_failed", attack_id=result.attack_id, error=str(e))


# ========== 核心逻辑 ==========

async def execute_attack(command: AttackCommand) -> AttackResult:
    """执行攻击指令"""
    attack_id = command.attack_id
    
    # 创建攻击实例
    try:
        attack_instance = AttackRegistry.create(command)
    except ValueError as e:
        result = AttackResult(
            attack_id=attack_id,
            node_id=node_crypto.node_id,
            status=AttackStatus.FAILED,
            errors=[str(e)]
        )
        await send_attack_result(result)
        return result
    
    attack_instances[attack_id] = attack_instance
    health_monitor.add_attack(attack_id)
    # v1.3.0 C3: 注入周期进度上报回调 — 运行期间每 2s 快照上报控制器
    attack_instance._progress_callback = send_attack_result
    
    # 创建执行任务
    async def run_attack():
        try:
            result = await attack_instance.execute()
            await send_attack_result(result)
        except asyncio.CancelledError:
            # BUG-19: stop_attack() 先 instance.stop() 再 task.cancel()。
            # 正常路径下 execute() 已返回带计数的 result 并上报;
            # 这里只补发【execute 从未完成】时的占位结果, 且必须保留实例已累计的计数,
            # 不能用全新空 result 覆盖控制器已有的统计 (原实现导致 total 归零)。
            partial = attack_instance.result if attack_instance else None
            cancelled_result = AttackResult(
                attack_id=attack_id,
                node_id=node_crypto.node_id,
                status=AttackStatus.EMERGENCY_STOPPED,
                errors=["Cancelled by controller"],
            )
            if partial is not None:
                cancelled_result.total_requests = partial.total_requests
                cancelled_result.successful_requests = partial.successful_requests
                cancelled_result.failed_requests = partial.failed_requests
                cancelled_result.bytes_sent = partial.bytes_sent
                cancelled_result.bytes_received = partial.bytes_received
            await send_attack_result(cancelled_result)
        except Exception as e:
            logger.error("attack_execution_error", attack_id=attack_id, error=str(e))
            result = AttackResult(
                attack_id=attack_id,
                node_id=node_crypto.node_id,
                status=AttackStatus.FAILED,
                errors=[f"Execution error: {e}"]
            )
            await send_attack_result(result)
        finally:
            health_monitor.remove_attack(attack_id)
            attack_instances.pop(attack_id, None)
            current_attacks.pop(attack_id, None)
    
    task = asyncio.create_task(run_attack())
    current_attacks[attack_id] = task
    
    return AttackResult(
        attack_id=attack_id,
        node_id=node_crypto.node_id,
        status=AttackStatus.STARTING
    )


async def stop_attack(attack_id: str, reason: str = "manual"):
    """停止指定攻击"""
    instance = attack_instances.get(attack_id)
    if instance:
        await instance.stop(reason)
    
    task = current_attacks.get(attack_id)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def emergency_stop(command: EmergencyStopCommand):
    """紧急熔断 - 停止所有攻击"""
    logger.critical("emergency_stop_received", reason=command.reason, by=command.issued_by)
    
    # 广播到所有攻击基类
    SafeAttackBase.set_emergency_stop(True)
    
    # 停止所有当前攻击
    stop_tasks = []
    for attack_id, instance in attack_instances.items():
        stop_tasks.append(instance.stop(f"emergency: {command.reason}"))
    
    for attack_id, task in current_attacks.items():
        if not task.done():
            task.cancel()
    
    if stop_tasks:
        await asyncio.gather(*stop_tasks, return_exceptions=True)
    
    # 等待任务清理
    await asyncio.sleep(0.5)


# ========== API 端点 ==========

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "node_id": node_crypto.node_id,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/metrics")
async def metrics(request: Request):
    """Prometheus 指标 — 设置 METRICS_TOKEN 环境变量后启用 Bearer 认证"""
    expected_token = os.getenv("METRICS_TOKEN", "")
    if expected_token:
        auth_header = request.headers.get("Authorization", "")
        if not hmac.compare_digest(auth_header, f"Bearer {expected_token}"):
            raise HTTPException(status_code=401, detail="Metrics token required")
    if health_monitor:
        return health_monitor.get_prometheus_metrics()
    return ""


@app.post("/api/v1/attacks/execute", response_model=dict)
async def execute_attack_endpoint(
    command: AttackCommand,
    auth: bool = Depends(verify_node_auth)
):
    """Controller 下发攻击指令"""
    logger.info("attack_command_received", attack_id=command.attack_id, type=command.attack_type.value)
    result = await execute_attack(command)
    return {"success": True, "attack_id": result.attack_id, "status": result.status.value}


@app.post("/api/v1/attacks/{attack_id}/stop", response_model=dict)
async def stop_attack_endpoint(
    attack_id: str,
    reason: str = "manual",
    auth: bool = Depends(verify_node_auth)
):
    """Controller 下发停止指令"""
    logger.info("stop_command_received", attack_id=attack_id, reason=reason)
    await stop_attack(attack_id, reason)
    return {"success": True, "attack_id": attack_id}


@app.post("/api/v1/emergency_stop", response_model=dict)
async def emergency_stop_endpoint(
    command: EmergencyStopCommand,
    auth: bool = Depends(verify_node_auth)
):
    """Controller 下发紧急熔断"""
    await emergency_stop(command)
    return {"success": True, "message": "Emergency stop executed"}


@app.post("/api/v1/emergency_stop/reset", response_model=dict)
async def emergency_reset_endpoint(auth: bool = Depends(verify_node_auth)):
    """P1-1 修复: Controller 广播复位 — 清除全局熔断, 节点恢复可接受攻击指令"""
    SafeAttackBase.set_emergency_stop(False)
    logger.info("emergency_stop_reset_received", node_id=node_crypto.node_id)
    return {"success": True, "message": "Emergency stop reset"}


@app.get("/api/v1/attacks", response_model=dict)
async def list_current_attacks(auth: bool = Depends(verify_node_auth)):
    """查询当前正在进行的攻击"""
    attacks = []
    for attack_id, instance in attack_instances.items():
        attacks.append({
            "attack_id": attack_id,
            "status": instance.result.status.value if hasattr(instance, 'result') else "unknown",
            "total_requests": instance.result.total_requests if hasattr(instance, 'result') else 0
        })
    return {"node_id": node_crypto.node_id, "attacks": attacks}


@app.get("/api/v1/info", response_model=dict)
async def get_node_info(auth: bool = Depends(verify_node_auth)):
    """获取节点详细信息"""
    return node_info.model_dump(mode='json') if node_info else {}


# 信号处理
def setup_signals():
    def signal_handler(signum, frame):
        logger.info("signal_received", signal=signum)
        _shutdown_event.set()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)


if __name__ == "__main__":
    import uvicorn
    
    setup_signals()

    # PyInstaller 冻结环境下无 app 包可导入 — 直接传应用对象
    app_target = "app.main:app"
    if getattr(sys, "frozen", False):
        app_target = app
    uvicorn.run(
        app_target,
        host="0.0.0.0",
        port=int(os.getenv("NODE_PORT", "8080")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
        access_log=True
    )