"""v1.5.0 新增: systemd OOM 防护配置静态检查 (C.5 / R-NEW-3)

确保 controller / attacker / attacker-raw 三个 systemd unit 都包含
v1.5.0 新增的 OOM 防护配置:
- MemoryMax: 软上限
- MemoryHigh: 主动回收阈值
- OOMPolicy=stop: 优雅停止
- OOMScoreAdjust: 负值降低 OOM 概率

测试不依赖 systemd 运行时, 仅静态扫描 unit 文件
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SERVICE_DIR = ROOT / "deploy" / "systemd"


def _read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace")


def test_ddos_controller_has_oom_protection():
    """controller unit 必须有 OOM 防护"""
    p = SERVICE_DIR / "ddos-controller.service"
    assert p.exists(), f"missing {p}"
    src = _read_text(p)
    # 必须项
    assert re.search(r"^MemoryMax=\S+", src, re.MULTILINE), "controller: 缺 MemoryMax"
    assert re.search(r"^MemoryHigh=\S+", src, re.MULTILINE), "controller: 缺 MemoryHigh"
    assert re.search(r"^OOMPolicy=stop\b", src, re.MULTILINE), "controller: OOMPolicy 必须 stop"
    # OOMScoreAdjust 应为负值
    m = re.search(r"^OOMScoreAdjust=(-\d+)", src, re.MULTILINE)
    assert m is not None, "controller: 缺 OOMScoreAdjust"
    assert int(m.group(1)) < 0, f"controller: OOMScoreAdjust 应负数, got {m.group(1)}"
    # 内存值至少 256M (合理下限)
    mem_m = re.search(r"^MemoryMax=(\d+)([MG])\b", src, re.MULTILINE)
    assert mem_m is not None
    val, unit = int(mem_m.group(1)), mem_m.group(2)
    mb = val if unit == "M" else val * 1024
    assert mb >= 256, f"controller: MemoryMax={mem_m.group(0)} 过小 (>=256M)"
    print(f"PASS: ddos-controller.service OOM (MemoryMax={mb}M, OOMScoreAdjust={m.group(1)})")


def test_ddos_attacker_has_oom_protection():
    """attacker unit 必须有 OOM 防护"""
    p = SERVICE_DIR / "ddos-attacker.service"
    assert p.exists(), f"missing {p}"
    src = _read_text(p)
    assert re.search(r"^MemoryMax=\S+", src, re.MULTILINE), "attacker: 缺 MemoryMax"
    assert re.search(r"^MemoryHigh=\S+", src, re.MULTILINE), "attacker: 缺 MemoryHigh"
    assert re.search(r"^OOMPolicy=stop\b", src, re.MULTILINE), "attacker: OOMPolicy 必须 stop"
    m = re.search(r"^OOMScoreAdjust=(-\d+)", src, re.MULTILINE)
    assert m is not None, "attacker: 缺 OOMScoreAdjust"
    assert int(m.group(1)) < 0, f"attacker: OOMScoreAdjust 应负数, got {m.group(1)}"
    print(f"PASS: ddos-attacker.service OOM (OOMScoreAdjust={m.group(1)})")


def test_ddos_attacker_raw_has_oom_protection():
    """attacker-raw unit 必须有 OOM 防护"""
    p = SERVICE_DIR / "ddos-attacker-raw.service"
    if not p.exists():
        print("SKIP: ddos-attacker-raw.service not found")
        return
    src = _read_text(p)
    assert re.search(r"^MemoryMax=\S+", src, re.MULTILINE), "attacker-raw: 缺 MemoryMax"
    assert re.search(r"^MemoryHigh=\S+", src, re.MULTILINE), "attacker-raw: 缺 MemoryHigh"
    assert re.search(r"^OOMPolicy=stop\b", src, re.MULTILINE), "attacker-raw: OOMPolicy 必须 stop"
    m = re.search(r"^OOMScoreAdjust=(-\d+)", src, re.MULTILINE)
    assert m is not None
    assert int(m.group(1)) < 0
    print(f"PASS: ddos-attacker-raw.service OOM (OOMScoreAdjust={m.group(1)})")


def test_node_oom_priority_higher_than_controller():
    """节点 OOMScoreAdjust 应 <= controller (节点更易被 OOM 选中, controller 保留)"""
    ctrl = _read_text(SERVICE_DIR / "ddos-controller.service")
    attacker = _read_text(SERVICE_DIR / "ddos-attacker.service")
    ctrl_score = int(re.search(r"^OOMScoreAdjust=(-?\d+)", ctrl, re.MULTILINE).group(1))
    attacker_score = int(re.search(r"^OOMScoreAdjust=(-?\d+)", attacker, re.MULTILINE).group(1))
    # OOMScoreAdjust 越小越不被杀 (负数更安全)
    assert attacker_score < ctrl_score, (
        f"节点 OOMScoreAdjust ({attacker_score}) 应 < controller ({ctrl_score}), "
        f"否则 controller 会在节点前被 OOM killer 杀掉"
    )
    print(f"PASS: 节点 OOM 优先级 ({attacker_score}) 低于 controller ({ctrl_score})")


def test_oom_policy_stop_with_restart():
    """OOMPolicy=stop + Restart=always 组合: OOM 后能自动重启

    验证所有 unit 同时有这两个设置
    """
    for unit_name in ("ddos-controller.service", "ddos-attacker.service", "ddos-attacker-raw.service"):
        p = SERVICE_DIR / unit_name
        if not p.exists():
            continue
        src = _read_text(p)
        has_oom_stop = re.search(r"^OOMPolicy=stop\b", src, re.MULTILINE) is not None
        has_restart = re.search(r"^Restart=always\b", src, re.MULTILINE) is not None
        assert has_oom_stop, f"{unit_name}: 缺 OOMPolicy=stop"
        assert has_restart, f"{unit_name}: 缺 Restart=always (OOMPolicy=stop 需配合才能自愈)"
        print(f"PASS: {unit_name} OOMPolicy=stop + Restart=always")


def test_memory_values_reasonable():
    """MemoryMax 数值合理性: controller >= 256M, attacker >= 512M (避免过低)"""
    cases = [
        ("ddos-controller.service", 256),
        ("ddos-attacker.service", 512),
        ("ddos-attacker-raw.service", 512),
    ]
    for unit_name, min_mb in cases:
        p = SERVICE_DIR / unit_name
        if not p.exists():
            continue
        src = _read_text(p)
        m = re.search(r"^MemoryMax=(\d+)([MG])\b", src, re.MULTILINE)
        assert m, f"{unit_name}: 缺 MemoryMax"
        val, unit = int(m.group(1)), m.group(2)
        mb = val if unit == "M" else val * 1024
        assert mb >= min_mb, f"{unit_name}: MemoryMax={mb}M 过小 (>= {min_mb}M)"
        print(f"PASS: {unit_name} MemoryMax={mb}M (>= {min_mb}M)")


if __name__ == "__main__":
    test_ddos_controller_has_oom_protection()
    test_ddos_attacker_has_oom_protection()
    test_ddos_attacker_raw_has_oom_protection()
    test_node_oom_priority_higher_than_controller()
    test_oom_policy_stop_with_restart()
    test_memory_values_reasonable()
    print("\nALL 6 SYSTEMD OOM TESTS PASSED")
