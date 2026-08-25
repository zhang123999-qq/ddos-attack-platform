from __future__ import annotations

from enum import Enum
from typing import Any, ClassVar, Dict, List, Optional, Literal
from pydantic import BaseModel, Field, field_validator
from datetime import datetime, timezone
import ipaddress
import re


class AttackType(str, Enum):
    HTTP_FLOOD = "http_flood"
    SYN_FLOOD = "syn_flood"
    SLOWLORIS = "slowloris"
    UDP_FLOOD = "udp_flood"
    UDP_REFLECTION = "udp_reflection"


class NodeStatus(str, Enum):
    REGISTERING = "registering"
    ONLINE = "online"
    ATTACKING = "attacking"
    OFFLINE = "offline"
    ERROR = "error"


class AttackStatus(str, Enum):
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    EMERGENCY_STOPPED = "emergency_stopped"


class TargetSpec(BaseModel):
    # v1.3.0 方案A: 字段语义扩展为 host — 接受 IPv4/IPv6/CIDR/域名, 不再做目标限制
    ip: str
    port: int = 80
    protocol: Literal["tcp", "udp"] = "tcp"
    path: str = "/"
    host_header: Optional[str] = None

    # 场景模板占位符 (如 TARGET_IP_PLACEHOLDER) — 加载期合法, 执行前必须被 overrides 覆盖
    PLACEHOLDER_RE: ClassVar[re.Pattern] = re.compile(r"^[A-Z][A-Z0-9_]*_PLACEHOLDER$")

    # 域名合法性 (RFC 1123): 段 = 字母/数字/连字符, 不以连字符开头结尾; 总长 ≤253
    HOSTNAME_RE: ClassVar[re.Pattern] = re.compile(
        r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*\.?$"
    )

    @field_validator('ip')
    @classmethod
    def validate_ip(cls, v: str) -> str:
        if cls.PLACEHOLDER_RE.match(v):
            return v
        try:
            ipaddress.ip_address(v)
            return v
        except ValueError:
            pass
        try:
            ipaddress.ip_network(v, strict=False)
            return v
        except ValueError:
            pass
        # 域名: 允许下划线缺失的宽松场景不存在 — 严格 RFC 1123 主机名
        if cls.HOSTNAME_RE.match(v) and "." in v.rstrip("."):
            return v.lower().rstrip(".")
        raise ValueError(f"Invalid target host (IP/CIDR/domain): {v}")

    def is_placeholder(self) -> bool:
        return bool(self.PLACEHOLDER_RE.match(self.ip))

    def is_hostname(self) -> bool:
        """目标是域名 (非 IP/CIDR) — scapy 类攻击需先解析"""
        try:
            ipaddress.ip_address(self.ip)
            return False
        except ValueError:
            pass
        try:
            ipaddress.ip_network(self.ip, strict=False)
            return False
        except ValueError:
            return True


class AttackParams(BaseModel):
    # 通用参数
    target: TargetSpec
    duration: int = Field(default=60, ge=1, le=3600)
    rps: int = Field(default=1000, ge=1, le=100000)  # requests/second per node
    concurrency: int = Field(default=100, ge=1, le=10000)
    
    # HTTP 特有
    method: Literal["GET", "POST", "HEAD"] = "GET"
    headers: Dict[str, str] = Field(default_factory=dict)
    body: Optional[str] = None
    use_https: bool = False
    verify_ssl: bool = False
    
    # SYN/UDP 特有
    source_ip_spoof: bool = False
    spoof_cidr: Optional[str] = None
    interface: Optional[str] = None
    
    # Slowloris 特有
    slowloris_interval: int = Field(default=15, ge=5, le=60)
    
    # UDP Reflection 特有
    reflector_type: Optional[Literal["ntp", "dns", "memcached", "ssdp", "snmp"]] = None
    reflector_list: Optional[List[str]] = None

    model_config = {"use_enum_values": True}


class AttackCommand(BaseModel):
    attack_id: str
    attack_type: AttackType
    params: AttackParams
    scenario_id: Optional[str] = None
    node_ids: List[str] = Field(default_factory=list)  # 空 = 所有匹配类型的节点
    priority: int = Field(default=0, ge=0, le=100)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AttackResult(BaseModel):
    attack_id: str
    node_id: str
    status: AttackStatus
    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    errors: List[str] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)


class NodeInfo(BaseModel):
    node_id: str
    node_type: Literal["http", "raw", "mixed"] = "mixed"
    supported_attacks: List[AttackType] = Field(default_factory=list)
    ip: str
    hostname: str
    cpu_cores: int
    memory_gb: float
    network_interfaces: List[str] = Field(default_factory=list)
    max_rps: int = 10000
    max_pps: int = 50000
    max_concurrent: int = 5000
    status: NodeStatus = NodeStatus.REGISTERING
    last_heartbeat: Optional[datetime] = None
    registered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    labels: Dict[str, str] = Field(default_factory=dict)


class NodeHeartbeat(BaseModel):
    node_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    cpu_percent: float
    memory_percent: float
    network_mbps: float
    active_connections: int
    current_attacks: List[str] = Field(default_factory=list)
    status: NodeStatus = NodeStatus.ONLINE


class ScenarioStep(BaseModel):
    attack_type: AttackType
    params: AttackParams
    node_selector: Dict[str, str] = Field(default_factory=dict)  # label 选择器
    delay_after: int = 0  # 秒


class Scenario(BaseModel):
    scenario_id: str
    name: str
    description: str
    steps: List[ScenarioStep] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EmergencyStopCommand(BaseModel):
    reason: str
    issued_by: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    target_node_ids: List[str] = Field(default_factory=list)  # 空 = 全网


class AuditEvent(BaseModel):
    event_id: str
    event_type: Literal[
        "attack_start", "attack_stop", "attack_complete", "attack_failed",
        "emergency_stop", "node_register", "node_heartbeat", "node_disconnect",
        "config_change", "auth_failure", "target_validation_failure",
        # 一键安装引导新增审计类别
        "node_enroll_success", "node_enroll_failed",
        "enroll_command_issued"
    ]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    actor: str  # user/node/system
    node_id: Optional[str] = None
    attack_id: Optional[str] = None
    scenario_id: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    success: bool = True
    error_message: Optional[str] = None


class APIResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    message: Optional[str] = None


class PaginatedResponse(BaseModel):
    items: List[Any]
    total: int
    page: int
    page_size: int