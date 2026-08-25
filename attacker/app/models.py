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
    # v1.3.0 方案A: 接受 IPv4/IPv6/CIDR/域名, 不再做目标限制
    ip: str
    port: int = 80
    protocol: Literal["tcp", "udp"] = "tcp"
    path: str = "/"
    host_header: Optional[str] = None

    HOSTNAME_RE: ClassVar[re.Pattern] = re.compile(
        r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*\.?$"
    )

    @field_validator('ip')
    @classmethod
    def validate_target(cls, v: str) -> str:
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
        if cls.HOSTNAME_RE.match(v) and "." in v.rstrip("."):
            return v.lower().rstrip(".")
        raise ValueError(f"Invalid target host (IP/CIDR/domain): {v}")

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

    @property
    def url(self) -> str:
        scheme = "https" if self.protocol == "tcp" and self.port == 443 else "http"
        return f"{scheme}://{self.ip}:{self.port}{self.path}"


class AttackParams(BaseModel):
    target: TargetSpec
    duration: int = Field(default=60, ge=1, le=3600)
    rps: int = Field(default=1000, ge=1, le=100000)
    concurrency: int = Field(default=100, ge=1, le=10000)
    
    # HTTP
    method: Literal["GET", "POST", "HEAD"] = "GET"
    headers: Dict[str, str] = Field(default_factory=dict)
    body: Optional[str] = None
    use_https: bool = False
    verify_ssl: bool = False
    
    # SYN/UDP
    source_ip_spoof: bool = False
    spoof_cidr: Optional[str] = None
    interface: Optional[str] = None
    
    # Slowloris
    slowloris_interval: int = Field(default=15, ge=5, le=60)
    
    # UDP Reflection
    reflector_type: Optional[Literal["ntp", "dns", "memcached", "ssdp", "snmp"]] = None
    reflector_list: Optional[List[str]] = None


class AttackCommand(BaseModel):
    attack_id: str
    attack_type: AttackType
    params: AttackParams
    scenario_id: Optional[str] = None
    node_ids: List[str] = Field(default_factory=list)
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


class EmergencyStopCommand(BaseModel):
    reason: str
    issued_by: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    target_node_ids: List[str] = Field(default_factory=list)