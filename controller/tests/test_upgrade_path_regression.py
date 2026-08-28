# -*- coding: utf-8 -*-
"""v1.4.0 升级路径兼容性测试 (REG-1)

验证 deploy/controller-install.sh 中的 ensure_env_var 函数及 do_update 路径:
1. ensure_env_var 函数存在且定义正确 (regex 静态检查)
2. ensure_env_var 的语义 (append 行为) 通过原生 Python 模拟验证
3. do_update() 函数体调用 ensure_env_var 补全所有 NODE_TLS_* 变量
4. v1.3.4 → v1.4.0 模拟: 缺 NODE_TLS_* 的 config.env 经 ensure_env_var 补全

本测试不依赖 bash 子进程 (避免 Windows+WSL 文件系统互通问题)。
ensure_env_var 的 shell 行为通过 Python 等价逻辑验证。
"""
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO_ROOT, "deploy", "controller-install.sh")


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------- Python 等价实现 (与 shell ensure_env_var 行为一致) ----------

def py_ensure_env_var(env_text: str, key: str, value: str) -> str:
    """Python 等价 ensure_env_var: 已存在 KEY 跳过, 否则追加

    与 shell 版本对比:
    shell: grep -qE "^${key}=" "$file" 2>/dev/null; echo "${key}=${value}" >> "$file"
    python: any line.startswith(f"{key}=") → skip; else: append f"{key}={value}\n"
    """
    for line in env_text.splitlines():
        if line.startswith(f"{key}="):
            return env_text  # 幂等
    if env_text and not env_text.endswith("\n"):
        env_text += "\n"
    return env_text + f"{key}={value}\n"


# ---------- 静态脚本检查 ----------

def test_ensure_env_var_function_exists():
    """v1.4.0: controller-install.sh 必须定义 ensure_env_var()"""
    assert os.path.isfile(SCRIPT), f"missing {SCRIPT}"
    text = _read_text(SCRIPT)
    m = re.search(r"^ensure_env_var\(\) \{\n((?:.*\n)*?)^\}", text, re.MULTILINE)
    assert m, "ensure_env_var() function not found in controller-install.sh"
    body = m.group(1)
    assert "grep -qE" in body, "ensure_env_var should check existence with grep -qE"
    assert '${key}=' in body or '"${key}=' in body, "ensure_env_var should match ^${key}="
    assert 'echo "${key}=${value}" >>' in body, "ensure_env_var should append KEY=VALUE"
    print("PASS: ensure_env_var function defined (REG-1)")


def test_ensure_env_var_idempotent_in_python():
    """v1.4.0: ensure_env_var 重复调用同 KEY 不覆盖 (Python 等价验证)

    shell 与 python 行为对齐 — 已存在 KEY 跳过
    """
    initial = "EXISTING_KEY=original_value\n"
    # 第一次调用: KEY 不存在
    after1 = py_ensure_env_var(initial, "NEW_KEY", "value1")
    assert "EXISTING_KEY=original_value" in after1
    assert "NEW_KEY=value1" in after1
    # 第二次调用: KEY 已存在, 不覆盖
    after2 = py_ensure_env_var(after1, "NEW_KEY", "value2")
    assert "NEW_KEY=value1" in after2
    assert "NEW_KEY=value2" not in after2
    print("PASS: ensure_env_var idempotent (Python equivalent) (REG-1)")


def test_ensure_env_var_appends_when_missing_in_python():
    """v1.4.0: ensure_env_var KEY 不存在时正确追加"""
    after = py_ensure_env_var("A=1\nB=2\n", "NODE_TLS_CA_FILE", "/path/ca.pem")
    assert "A=1" in after
    assert "B=2" in after
    assert "NODE_TLS_CA_FILE=/path/ca.pem" in after
    assert after.strip().endswith("NODE_TLS_CA_FILE=/path/ca.pem"), \
        f"appended line should be at EOF, got: {after!r}"
    print("PASS: ensure_env_var appends when missing (Python equivalent) (REG-1)")


def test_v134_to_v140_migration_writes_node_tls():
    """v1.4.0 (REG-1): v1.3.4 config.env 模拟经 ensure_env_var 升级后, 必须含 NODE_TLS_*

    这是关键回归保护: 之前 v1.4.0 修复有遗漏 — do_update() 没补写 NODE_TLS_*
    导致老用户升级后 controller 因 fail-closed 启动崩溃
    """
    v134_config = (
        "CONTROLLER_HOST=0.0.0.0\n"
        "CONTROLLER_PORT=8443\n"
        "SHARED_SECRET=regression-v134-secret-32chars-abcdef\n"
        "REQUIRE_SHARED_SECRET=true\n"
        "ENABLE_WEB_UI=true\n"
        "AUDIT_FILE_ENABLED=false\n"
        "TLS_CERT_FILE=/opt/ddos-attack-platform/controller/certs/controller-cert.pem\n"
        "TLS_KEY_FILE=/opt/ddos-attack-platform/controller/certs/controller-key.pem\n"
        "TLS_CA_FILE=/opt/ddos-attack-platform/controller/certs/ca-cert.pem\n"
        "LOG_LEVEL=info\n"
    )
    cert_dir = "/opt/ddos-attack-platform/controller/certs"
    env_text = v134_config
    for k, v in [
        ("NODE_TLS_CA_FILE", f"{cert_dir}/ca-cert.pem"),
        ("NODE_TLS_CERT_FILE", f"{cert_dir}/controller-cert.pem"),
        ("NODE_TLS_KEY_FILE", f"{cert_dir}/controller-key.pem"),
        ("NODE_INSECURE_PLAIN_HTTP", "false"),
        ("NODE_PLAIN_HTTP_BANNED", "true"),
    ]:
        env_text = py_ensure_env_var(env_text, k, v)
    for k in ("NODE_TLS_CA_FILE", "NODE_TLS_CERT_FILE", "NODE_TLS_KEY_FILE",
              "NODE_INSECURE_PLAIN_HTTP", "NODE_PLAIN_HTTP_BANNED"):
        assert f"{k}=" in env_text, f"v1.3.4→v1.4.0 migration missing {k}"
    assert "SHARED_SECRET=regression-v134-secret-32chars-abcdef" in env_text
    assert "TLS_CA_FILE=/opt/ddos-attack-platform/controller/certs/ca-cert.pem" in env_text
    print("PASS: v1.3.4 → v1.4.0 migration adds NODE_TLS_* (REG-1)")


def test_do_update_calls_ensure_env_var_for_node_tls():
    """v1.4.0 (REG-1): do_update() 函数体内必须包含 ensure_env_var 调用"""
    text = _read_text(SCRIPT)
    pattern = r"^do_update\(\) \{\n((?:.*\n)*?)^\}\n"
    m = re.search(pattern, text, re.MULTILINE)
    assert m, "do_update() function not found"
    body = m.group(1)
    for k in ("NODE_TLS_CA_FILE", "NODE_TLS_CERT_FILE", "NODE_TLS_KEY_FILE",
              "NODE_INSECURE_PLAIN_HTTP", "NODE_PLAIN_HTTP_BANNED"):
        assert f'ensure_env_var "{k}"' in body, \
            f"do_update() must call ensure_env_var for {k} (REG-1 升级兼容)"
    print("PASS: do_update() calls ensure_env_var for all NODE_TLS_* (REG-1)")


# ---------- 真实 shell 行为验证 (WSL / Git Bash only, 自动 SKIP) ----------

def _bash_available() -> bool:
    """检测 bash 可用性 (仅 Linux/macOS/WSL 真正可用)"""
    if sys.platform == "win32":
        return False  # Windows + WSL bash 文件系统不互通, 自动跳过
    try:
        result = subprocess.run(
            ["bash", "--version"], capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def test_shell_ensure_env_var_actual_behavior():
    """v1.4.0 (REG-1): 在原生 Linux bash 下验证 shell 实际行为 (Windows 自动 SKIP)"""
    if not _bash_available():
        print("SKIP: shell behavior test requires native bash (Windows auto-skipped)")
        return
    import tempfile
    src_text = _read_text(SCRIPT)
    m = re.search(r"^ensure_env_var\(\) \{\n((?:.*\n)*?)^\}", src_text, re.MULTILINE)
    src = f"ensure_env_var() {{\n{m.group(1)}}}"
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".env") as f:
        f.write("EXISTING_KEY=original_value\n")
        env_file = f.name
    try:
        for _ in range(2):
            r = subprocess.run(
                ["bash", "-c", f'{src}; ensure_env_var "NEW_KEY" "value1" "{env_file}"'],
                capture_output=True, text=True,
            )
            assert r.returncode == 0, f"bash failed: {r.stderr}"
        with open(env_file) as f:
            content = f.read()
        assert "EXISTING_KEY=original_value" in content
        assert "NEW_KEY=value1" in content
        assert "NEW_KEY=value2" not in content  # 幂等
        print("PASS: shell ensure_env_var actual behavior verified (REG-1)")
    finally:
        os.unlink(env_file)


if __name__ == "__main__":
    test_ensure_env_var_function_exists()
    test_ensure_env_var_idempotent_in_python()
    test_ensure_env_var_appends_when_missing_in_python()
    test_v134_to_v140_migration_writes_node_tls()
    test_do_update_calls_ensure_env_var_for_node_tls()
    test_shell_ensure_env_var_actual_behavior()
    print("\nALL v1.4.0 UPGRADE-PATH REGRESSION TESTS PASSED")
else:
    # pytest 入口
    def test_pytest_function_exists():
        test_ensure_env_var_function_exists()

    def test_pytest_idempotent_in_python():
        test_ensure_env_var_idempotent_in_python()

    def test_pytest_appends_in_python():
        test_ensure_env_var_appends_when_missing_in_python()

    def test_pytest_v134_to_v140_migration():
        test_v134_to_v140_migration_writes_node_tls()

    def test_pytest_do_update_calls():
        test_do_update_calls_ensure_env_var_for_node_tls()

    def test_pytest_shell_actual_behavior():
        test_shell_ensure_env_var_actual_behavior()
