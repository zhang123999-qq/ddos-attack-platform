"""v1.5.0 新增: Controller Prometheus 指标导出 (O-NEW-1)

提供 /metrics 端点 (GET, 无认证 - 内部监控专用, 应在内网/VLAN 中部署),
暴露以下核心指标:
- ddos_nodes_online: 当前在线节点数 (Gauge)
- ddos_attacks_active: 当前进行中攻击数 (Gauge)
- ddos_attacks_total: 历史启动的攻击总数 (Counter)
- ddos_attacks_completed_total: 完成的攻击数 (Counter, 按 status 标签)
- ddos_audit_queue_depth: 审计队列当前长度 (Gauge)
- ddos_audit_queue_overflow_total: 队列满溢出次数 (Counter, NEW-8)
- ddos_rate_limit_used_rps: 全局 RPS 配额已用 (Gauge)
- ddos_rate_limit_used_pps: 全局 PPS 配额已用 (Gauge)
- ddos_emergency_stop_active: 熔断状态 (Gauge, 0/1)
- ddos_target_validation_failures_total: 越权目标拦截次数 (Counter)
- ddos_enroll_success_total / ddos_enroll_failed_total: 节点 enroll 计数
"""
from __future__ import annotations

import structlog
from prometheus_client import Counter, Gauge, CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST

logger = structlog.get_logger(__name__)

# 使用独立 registry 避免污染全局 (便于多进程/多 app 隔离)
REGISTRY = CollectorRegistry()

# 节点 / 攻击 / 配额
NODES_ONLINE = Gauge(
    "ddos_nodes_online", "Current online nodes",
    registry=REGISTRY,
)
ATTACKS_ACTIVE = Gauge(
    "ddos_attacks_active", "Currently running attacks",
    registry=REGISTRY,
)
ATTACKS_TOTAL = Counter(
    "ddos_attacks_total", "Total attacks launched",
    registry=REGISTRY,
)
ATTACKS_COMPLETED_TOTAL = Counter(
    "ddos_attacks_completed_total", "Total attacks completed",
    ["status"],  # running/stopped/failed/emergency_stopped
    registry=REGISTRY,
)

# 配额 (Gauge - 反映当前已用)
RATE_LIMIT_USED_RPS = Gauge(
    "ddos_rate_limit_used_rps", "RPS quota currently in use",
    registry=REGISTRY,
)
RATE_LIMIT_USED_PPS = Gauge(
    "ddos_rate_limit_used_pps", "PPS quota currently in use",
    registry=REGISTRY,
)
RATE_LIMIT_USED_CONCURRENT = Gauge(
    "ddos_rate_limit_used_concurrent", "Concurrent connection quota in use",
    registry=REGISTRY,
)

# 熔断 / 安全
EMERGENCY_STOP_ACTIVE = Gauge(
    "ddos_emergency_stop_active", "Emergency stop state (0=inactive, 1=active)",
    registry=REGISTRY,
)
TARGET_VALIDATION_FAILURES = Counter(
    "ddos_target_validation_failures_total",
    "Target whitelist violations blocked",
    ["reason"],  # placeholder / out_of_cidr
    registry=REGISTRY,
)

# 审计
AUDIT_QUEUE_DEPTH = Gauge(
    "ddos_audit_queue_depth", "Audit queue depth (0-maxsize)",
    registry=REGISTRY,
)
AUDIT_QUEUE_OVERFLOW_TOTAL = Counter(
    "ddos_audit_queue_overflow_total",
    "Audit queue overflow events (queue full, drop oldest)",
    registry=REGISTRY,
)
AUDIT_EVENTS_TOTAL = Counter(
    "ddos_audit_events_total",
    "Audit events by type",
    ["event_type"],
    registry=REGISTRY,
)

# Enroll
ENROLL_SUCCESS = Counter(
    "ddos_enroll_success_total", "Successful node enrollments",
    registry=REGISTRY,
)
ENROLL_FAILED = Counter(
    "ddos_enroll_failed_total", "Failed enrollment attempts",
    ["reason"],  # bad_token / cert_issue_failed / invalid_node_id
    registry=REGISTRY,
)

# 证书签发 (v1.5.0)
NODE_CERTS_ISSUED = Counter(
    "ddos_node_certs_issued_total", "Node client certificates issued",
    registry=REGISTRY,
)
NODE_CERTS_REVOKED = Counter(
    "ddos_node_certs_revoked_total", "Node client certificates revoked",
    registry=REGISTRY,
)

# v1.5.0 (A.3 / R-NEW-2): Admin API 限流指标
ADMIN_RATE_LIMITED_TOTAL = Counter(
    "ddos_admin_rate_limited_total",
    "Admin API requests rate-limited (rejected with 429)",
    ["scope"],
    registry=REGISTRY,
)
ADMIN_RATE_LIMIT_BLOCKED_TOTAL = Counter(
    "ddos_admin_rate_limit_blocked_total",
    "Admin API rate-limit block events (per scope)",
    ["scope"],
    registry=REGISTRY,
)
ADMIN_RATE_LIMIT_TOKENS = Gauge(
    "ddos_admin_rate_limit_tokens",
    "Current available tokens per admin scope",
    ["scope"],
    registry=REGISTRY,
)


def collect_controller_metrics(orchestrator=None, audit_logger=None) -> None:
    """从 orchestrator/audit 拉取当前状态刷新 Gauge

    定期调用 (由 lifespan 中后台任务驱动, 5s 一次)
    """
    try:
        if orchestrator is not None:
            # 节点数
            try:
                online = len(orchestrator.get_nodes())
            except Exception:
                online = 0
            NODES_ONLINE.set(online)

            # 攻击数
            try:
                active = len(orchestrator.get_all_attacks())
            except Exception:
                active = 0
            ATTACKS_ACTIVE.set(active)

            # 熔断
            try:
                EMERGENCY_STOP_ACTIVE.set(1 if orchestrator.is_emergency_active() else 0)
            except Exception:
                EMERGENCY_STOP_ACTIVE.set(0)

            # 配额
            try:
                usage = orchestrator.get_rate_limit_status()
                RATE_LIMIT_USED_RPS.set(float(usage.get("used_rps", 0)))
                RATE_LIMIT_USED_PPS.set(float(usage.get("used_pps", 0)))
                RATE_LIMIT_USED_CONCURRENT.set(float(usage.get("used_concurrent", 0)))
            except Exception:
                pass

        if audit_logger is not None:
            try:
                AUDIT_QUEUE_DEPTH.set(float(audit_logger._queue.qsize()))
            except Exception:
                pass
    except Exception as e:
        logger.debug("collect_metrics_error", error=str(e))


def render_metrics() -> tuple[bytes, str]:
    """返回 (body, content_type) - 可直接用于 FastAPI Response"""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
