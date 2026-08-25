# -*- coding: utf-8 -*-
"""v1.3.4 安装器加固测试 — 验证 controller-install.sh / node-install.sh 包含 F2/F3/F4 修复

不实际执行安装 (需 root), 仅静态检查脚本内容是否含必要步骤:
  F2: useradd ddos
  F3: chown ddos:ddos + chmod 600 config.env
  F4: chmod 640 systemd unit
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS = {
    "controller-install.sh": ROOT / "deploy" / "controller-install.sh",
    "node-install.sh": ROOT / "deploy" / "node-install.sh",
}


def _read_text(path: Path) -> str:
    """Read text file forcing utf-8 (Windows defaults to gbk)"""
    return path.read_bytes().decode("utf-8", errors="replace")


def assert_contains(path: Path, pattern: str, re_flags: int = re.MULTILINE, *, why: str = ""):
    src = _read_text(path)
    if not re.search(pattern, src, re_flags):
        raise AssertionError(f"{path.name}: missing pattern /{pattern}/ — {why}")


def assert_not_contains(path: Path, pattern: str, why: str = ""):
    src = _read_text(path)
    if re.search(pattern, src, re.MULTILINE):
        raise AssertionError(f"{path.name}: forbidden pattern found /{pattern}/ — {why}")


def test_controller_install_creates_ddos_user():
    """F2: 控制器安装器必须创建 ddos 用户"""
    p = SCRIPTS["controller-install.sh"]
    # 必须存在 useradd 且 SERVICE_USER=ddos
    assert_contains(p, r"useradd\b.*ddos", re.DOTALL, why="应使用 useradd 创建 ddos 系统用户")
    assert_contains(p, r'SERVICE_USER="ddos"', why="SERVICE_USER 变量必须设为 ddos")
    # id 命令检测用户存在性 (幂等)
    assert_contains(p, r'if\s+!\s+id\s+-u\s+"\$SERVICE_USER"', why="安装前需检测 ddos 是否存在 (幂等)")
    print("PASS: controller-install.sh creates ddos user (F2)")


def test_controller_install_chowns_install_dir():
    """F2+F3: 控制器安装器必须 chown 安装目录到 ddos"""
    p = SCRIPTS["controller-install.sh"]
    assert_contains(p, r'chown\s+-R\s+"\$SERVICE_USER:\$SERVICE_USER"\s+"\$INSTALL_DIR"\s+"\$ETC_DIR"',
                    why="必须 chown -R ddos:ddos $INSTALL_DIR $ETC_DIR")
    print("PASS: controller-install.sh chowns install dir (F2+F3)")


def test_controller_install_chmod_config_env():
    """F3: config.env 必须是 600 ddos:ddos"""
    p = SCRIPTS["controller-install.sh"]
    assert_contains(p, r'chmod\s+600\s+"\$ETC_DIR/config\.env"',
                    why="config.env (含 SHARED_SECRET) 必须 chmod 600")
    # 必须 chown ddos:ddos config.env (cat > 写入后是 root:root, 需显式 chown)
    assert_contains(p, r'chown\s+"\$SERVICE_USER:\$SERVICE_USER"\s+"\$ETC_DIR/config\.env"',
                    why="config.env 需 chown ddos:ddos")
    print("PASS: controller-install.sh chmod 600 config.env (F3)")


def test_controller_install_chmod_install_dir():
    """F3: 安装目录 750 (其他用户不可遍历)"""
    p = SCRIPTS["controller-install.sh"]
    assert_contains(p, r'chmod\s+750\s+"\$INSTALL_DIR"\s+"\$ETC_DIR"',
                    why="安装目录应 chmod 750 防其他用户遍历")
    print("PASS: controller-install.sh chmod 750 install dir (F3)")


def test_controller_install_chmod_service_unit():
    """F4: systemd unit 必须 chmod 640"""
    p = SCRIPTS["controller-install.sh"]
    assert_contains(p, r'chmod\s+640\s+"/etc/systemd/system/\$\{SERVICE_NAME\}\.service"',
                    why="systemd unit 文件应 chmod 640 限其他用户读 env vars")
    print("PASS: controller-install.sh chmod 640 service unit (F4)")


def test_controller_unit_uses_ddos_user():
    """F2: systemd unit 必须用 User=ddos Group=ddos"""
    p = SCRIPTS["controller-install.sh"]
    unit_block_match = re.search(r'\[Service\](.*?)\[Install\]', _read_text(p), re.DOTALL)
    assert unit_block_match, "找不到 [Service] 段"
    unit = unit_block_match.group(1)
    assert re.search(r"^User=\$SERVICE_USER\s*$", unit, re.MULTILINE), "unit 缺 User=$SERVICE_USER"
    assert re.search(r"^Group=\$SERVICE_USER\s*$", unit, re.MULTILINE), "unit 缺 Group=$SERVICE_USER"
    print("PASS: controller systemd unit uses ddos user (F2)")


def test_controller_update_reapplies_perms():
    """F2+F3: 升级路径 (do_update) 也必须重新 chown + chmod"""
    p = SCRIPTS["controller-install.sh"]
    update_block_match = re.search(r'do_update\(\)\s*\{(.*?)^\}', _read_text(p), re.DOTALL | re.MULTILINE)
    assert update_block_match, "找不到 do_update 函数"
    body = update_block_match.group(1)
    assert re.search(r"chown\s+(-R\s+)?ddos:ddos\s+\"\$INSTALL_DIR\"", body), \
        "do_update 缺 chown ddos:ddos"
    assert re.search(r'chmod\s+600\s+"\$ETC_DIR/config\.env"', body), \
        "do_update 缺 chmod 600 config.env"
    print("PASS: controller-install.sh update re-applies perms (F2+F3)")


def test_node_install_creates_ddos_user():
    """F2: 节点安装器必须创建 ddos 用户"""
    p = SCRIPTS["node-install.sh"]
    assert_contains(p, r"useradd\b.*ddos", re.DOTALL, why="应使用 useradd 创建 ddos 系统用户")
    assert_contains(p, r'SERVICE_USER="ddos"', why="SERVICE_USER 变量必须设为 ddos")
    assert_contains(p, r'if\s+!\s+id\s+-u\s+"\$SERVICE_USER"', why="安装前需检测 ddos 是否存在")
    print("PASS: node-install.sh creates ddos user (F2)")


def test_node_install_chowns_install_dir():
    """F2+F3: 节点安装器必须 chown 安装目录"""
    p = SCRIPTS["node-install.sh"]
    assert_contains(p, r'chown\s+-R\s+"\$SERVICE_USER:\$SERVICE_USER"\s+"\$INSTALL_DIR"\s+"\$ETC_DIR"',
                    why="必须 chown -R ddos:ddos $INSTALL_DIR $ETC_DIR")
    print("PASS: node-install.sh chowns install dir (F2+F3)")


def test_node_install_chmod_config_env():
    """F3: 节点 config.env 必须是 600 ddos:ddos"""
    p = SCRIPTS["node-install.sh"]
    assert_contains(p, r'chmod\s+600\s+"\$ETC_DIR/config\.env"',
                    why="节点 config.env (含 SHARED_SECRET) 必须 chmod 600")
    assert_contains(p, r'chown\s+"\$SERVICE_USER:\$SERVICE_USER"\s+"\$ETC_DIR/config\.env"',
                    why="节点 config.env 需 chown ddos:ddos")
    print("PASS: node-install.sh chmod 600 config.env (F3)")


def test_node_install_chmod_service_unit():
    """F4: 节点 systemd unit 必须 chmod 640"""
    p = SCRIPTS["node-install.sh"]
    assert_contains(p, r'chmod\s+640\s+"/etc/systemd/system/\$\{SERVICE_NAME\}\.service"',
                    why="节点 systemd unit 应 chmod 640")
    print("PASS: node-install.sh chmod 640 service unit (F4)")


def test_node_unit_uses_ddos_user_for_http():
    """F2: http 类型 attacker 节点用 ddos 用户;raw 类型仍 root (需 CAP_NET_RAW)"""
    p = SCRIPTS["node-install.sh"]
    unit_block_match = re.search(r'\[Service\](.*?)\[Install\]', _read_text(p), re.DOTALL)
    assert unit_block_match
    unit = unit_block_match.group(1)
    user_lines = re.findall(r"^User=[^\n]*$", unit, re.MULTILINE)
    assert any("SERVICE_USER" in u and "raw" in u for u in user_lines), \
        f"应包含条件 User (raw=root, http=ddos), 实际: {user_lines}"
    print("PASS: node unit uses ddos for http, root for raw (F2)")


def main():
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} INSTALLER HARDENING TESTS PASSED")


if __name__ == "__main__":
    main()
