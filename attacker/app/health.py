from __future__ import annotations

import asyncio
import os
import psutil
import platform
import socket
import time
from typing import List, Dict, Any
from datetime import datetime
import structlog

from app.models import NodeInfo, NodeHeartbeat, NodeStatus, AttackType
from app.crypto import node_crypto

logger = structlog.get_logger(__name__)


class HealthMonitor:
    """节点健康监控 - CRIT-2 修复: 完全同步实现，避免 asyncio.run() 在运行中的循环内崩溃"""

    def __init__(self, node_info: NodeInfo):
        self.node_info = node_info
        self._current_attacks: List[str] = []
        self._last_net_io = psutil.net_io_counters()
        self._last_time = time.time()

    def get_node_info(self) -> NodeInfo:
        cpu_cores = psutil.cpu_count(logical=True) or 1

        mem = psutil.virtual_memory()
        memory_gb = round(mem.total / (1024**3), 2)

        interfaces = []
        for name, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                    interfaces.append(f"{name}:{addr.address}")

        supported = self._get_supported_attacks()

        return NodeInfo(
            node_id=self.node_info.node_id,
            node_type=self.node_info.node_type,
            supported_attacks=supported,
            ip=interfaces[0].split(":")[1] if interfaces else "127.0.0.1",
            hostname=platform.node(),
            cpu_cores=cpu_cores,
            memory_gb=memory_gb,
            network_interfaces=interfaces,
            max_rps=self.node_info.max_rps,
            max_pps=self.node_info.max_pps,
            max_concurrent=self.node_info.max_concurrent,
            labels=self.node_info.labels
        )

    def _get_supported_attacks(self) -> List[AttackType]:
        attack_types_str = os.getenv("ATTACK_TYPES", "http_flood,slowloris")
        supported = []
        for atk in attack_types_str.split(","):
            atk = atk.strip()
            if atk:
                try:
                    supported.append(AttackType(atk))
                except ValueError:
                    pass
        return supported

    async def collect_heartbeat(self) -> NodeHeartbeat:
        """异步心跳采集"""
        cpu_percent = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        memory_percent = mem.percent

        net_io = psutil.net_io_counters()
        now = time.time()
        dt = now - self._last_time
        if dt > 0:
            bytes_sent = net_io.bytes_sent - self._last_net_io.bytes_sent
            bytes_recv = net_io.bytes_recv - self._last_net_io.bytes_recv
            network_mbps = (bytes_sent + bytes_recv) * 8 / dt / 1_000_000
        else:
            network_mbps = 0.0
        self._last_net_io = net_io
        self._last_time = now

        try:
            connections = len(psutil.net_connections(kind="inet"))
        except (psutil.AccessDenied, psutil.Error):
            connections = 0

        return NodeHeartbeat(
            node_id=self.node_info.node_id,
            cpu_percent=cpu_percent,
            memory_percent=memory_percent,
            network_mbps=round(network_mbps, 2),
            active_connections=connections,
            current_attacks=self._current_attacks.copy(),
            status=NodeStatus.ATTACKING if self._current_attacks else NodeStatus.ONLINE
        )

    def _snapshot_network_mbps(self) -> float:
        """同步网络速率快照"""
        net_io = psutil.net_io_counters()
        now = time.time()
        dt = now - self._last_time
        if dt > 0:
            total = (net_io.bytes_sent - self._last_net_io.bytes_sent) + \
                    (net_io.bytes_recv - self._last_net_io.bytes_recv)
            network_mbps = total * 8 / dt / 1_000_000
        else:
            network_mbps = 0.0
        self._last_net_io = net_io
        self._last_time = now
        return round(network_mbps, 2)

    def add_attack(self, attack_id: str):
        if attack_id not in self._current_attacks:
            self._current_attacks.append(attack_id)

    def remove_attack(self, attack_id: str):
        if attack_id in self._current_attacks:
            self._current_attacks.remove(attack_id)

    def get_prometheus_metrics(self) -> str:
        """CRIT-2 修复: 完全同步的 Prometheus 指标导出 (不再使用 asyncio.run)"""
        cpu = psutil.cpu_percent()
        mem = psutil.virtual_memory().percent
        net_mbps = self._snapshot_network_mbps()
        connections = 0
        try:
            connections = len(psutil.net_connections(kind="inet"))
        except (psutil.AccessDenied, psutil.Error):
            pass

        node_id = self.node_info.node_id
        return (
            f'ddos_node_cpu_percent{{node_id="{node_id}"}} {cpu}\n'
            f'ddos_node_memory_percent{{node_id="{node_id}"}} {mem}\n'
            f'ddos_node_active_attacks{{node_id="{node_id}"}} {len(self._current_attacks)}\n'
            f'ddos_node_network_mbps{{node_id="{node_id}"}} {net_mbps}\n'
            f'ddos_node_connections{{node_id="{node_id}"}} {connections}\n'
        )