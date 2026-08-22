"""
攻击模块注册表 — 导入所有攻击类型以触发 AttackRegistry.register()
"""
from app.attacks.base import AttackRegistry, SafeAttackBase, SafetyError
from app.models import AttackType

# 无条件导入的模块
from app.attacks import http_flood, slowloris

# scapy 依赖的模块 (可能导入失败 → 日志警告，不阻塞)
try:
    from app.attacks import syn_flood
except ImportError:
    pass

try:
    from app.attacks import udp_flood
except ImportError:
    pass

__all__ = ["AttackRegistry", "SafeAttackBase", "SafetyError", "AttackType"]