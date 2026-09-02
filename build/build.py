#!/usr/bin/env python3
"""
DDoS Attack Platform - 二进制构建与打包脚本

输出:
  dist/controller/  — Controller 单文件可执行 + 配置文件 + 场景
  dist/attacker/   — Attacker 单文件可执行 + 配置文件

用法:
  python build.py all              # 构建所有
  python build.py controller       # 仅构建 Controller
  python build.py attacker         # 仅构建 Attacker
  python build.py package          # 打包为 .tar.gz 发布
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = ROOT / "build"
DIST_DIR = ROOT / "dist"

# 平台后缀
PLATFORM = sys.platform  # darwin / linux / win32
EXT = ".exe" if PLATFORM == "win32" else ""


def banner(msg: str):
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}")


def run(cmd: str, cwd: Path = None):
    print(f"  → {cmd}")
    subprocess.run(cmd, shell=True, check=True, cwd=cwd)


def clean_dist():
    """清理旧的构建产物"""
    shutil.rmtree(DIST_DIR / "controller", ignore_errors=True)
    shutil.rmtree(DIST_DIR / "attacker", ignore_errors=True)


def build_controller():
    """构建 Controller 二进制"""
    banner("Building Controller")
    shutil.rmtree(DIST_DIR / "controller", ignore_errors=True)
    (DIST_DIR / "controller").mkdir(parents=True, exist_ok=True)

    # PyInstaller
    run(f"pyinstaller --distpath={DIST_DIR / 'controller'} "
        f"--workpath={BUILD_DIR / '_controller_build'} "
        f"--specpath={BUILD_DIR} "
        f"controller.spec",
        cwd=BUILD_DIR)

    # 复制配置文件
    src_cfg = ROOT / "controller" / "config.env.example"
    shutil.copy(src_cfg, DIST_DIR / "controller" / "config.env")

    # 创建默认 certs 目录
    (DIST_DIR / "controller" / "certs").mkdir(parents=True, exist_ok=True)

    # 复制场景
    scenarios_dst = DIST_DIR / "controller" / "scenarios"
    shutil.copytree(ROOT / "scenarios", scenarios_dst, dirs_exist_ok=True)

    # 部署文档
    for doc in ["SAFETY_RULES.md", "TEACHING_GUIDE.md", "API_REFERENCE.md"]:
        src = ROOT / "docs" / doc
        if src.exists():
            shutil.copy(src, DIST_DIR / "controller")

    # 启动说明
    (DIST_DIR / "controller" / "start.sh").write_text("""#!/bin/bash
# Controller 启动脚本
export SHARED_SECRET="${SHARED_SECRET:-$(openssl rand -hex 32)}"
export ALLOWED_TARGET_CIDRS="${ALLOWED_TARGET_CIDRS:-10.100.0.0/16,192.168.0.0/16}"
export GLOBAL_MAX_RPS="${GLOBAL_MAX_RPS:-50000}"
export GLOBAL_MAX_PPS="${GLOBAL_MAX_PPS:-100000}"
export CONTROLLER_PORT=8443
export AUDIT_LOG_PATH="./audit.jsonl"

echo "Starting DDoS Controller..."
echo "  Port:        $CONTROLLER_PORT"
echo "  Allowed CIDRs: $ALLOWED_TARGET_CIDRS"
echo "  API Docs:    https://localhost:$CONTROLLER_PORT/docs"
echo ""

# 检查证书
if [ ! -f "./certs/controller-cert.pem" ]; then
    echo "[WARN] TLS certificates not found in ./certs/"
    echo "       Run generate_certs.sh first from deploy/ directory"
    echo "       Starting without TLS..."
    export TLS_CERT_FILE=""
    export TLS_KEY_FILE=""
    export TLS_CA_FILE=""
fi

./ddos-controller
""")
    (DIST_DIR / "controller" / "start.sh").chmod(0o755)

    # Windows 启动批处理
    (DIST_DIR / "controller" / "start.bat").write_text("""@echo off
echo Starting DDoS Controller...
echo   Port: 8443
echo   API Docs: https://localhost:8443/docs
echo.
set SHARED_SECRET=%SHARED_SECRET%
if "%SHARED_SECRET%"=="" set SHARED_SECRET=changeme32charslongsecret
set ALLOWED_TARGET_CIDRS=10.100.0.0/16,192.168.0.0/16
set GLOBAL_MAX_RPS=50000
set GLOBAL_MAX_PPS=100000
set CONTROLLER_PORT=8443
set CONTROLLER_HOST=0.0.0.0
set ENABLE_WEB_UI=true
ddos-controller.exe
""")

    print(f"\n✓ Controller built: {DIST_DIR / 'controller' / f'ddos-controller{EXT}'}")


def build_attacker():
    """构建 Attacker 二进制"""
    banner("Building Attacker")
    shutil.rmtree(DIST_DIR / "attacker", ignore_errors=True)
    (DIST_DIR / "attacker").mkdir(parents=True, exist_ok=True)

    run(f"pyinstaller --distpath={DIST_DIR / 'attacker'} "
        f"--workpath={BUILD_DIR / '_attacker_build'} "
        f"--specpath={BUILD_DIR} "
        f"attacker.spec",
        cwd=BUILD_DIR)

    src_cfg = ROOT / "attacker" / "config.env.example"
    shutil.copy(src_cfg, DIST_DIR / "attacker" / "config.env")

    (DIST_DIR / "attacker" / "certs").mkdir(parents=True, exist_ok=True)

    (DIST_DIR / "attacker" / "start.sh").write_text("""#!/bin/bash
# Attacker Node 启动脚本
export NODE_ID="${NODE_ID:-attacker-http-01}"
export NODE_TYPE="${NODE_TYPE:-http}"
export CONTROLLER_URL="${CONTROLLER_URL:-https://10.100.1.10:8443}"
export ATTACK_TYPES="${ATTACK_TYPES:-http_flood,slowloris}"
export MAX_RPS="${MAX_RPS:-10000}"
export MAX_CONCURRENT="${MAX_CONCURRENT:-5000}"
export ALLOWED_TARGET_CIDRS="${ALLOWED_TARGET_CIDRS:-10.100.0.0/16,192.168.0.0/16}"

echo "Starting DDoS Attacker Node..."
echo "  Node ID:      $NODE_ID"
echo "  Type:         $NODE_TYPE"
echo "  Controller:   $CONTROLLER_URL"
echo "  Attacks:      $ATTACK_TYPES"
echo ""

./ddos-attacker
""")
    (DIST_DIR / "attacker" / "start.sh").chmod(0o755)

    (DIST_DIR / "attacker" / "start.bat").write_text("""@echo off
echo Starting DDoS Attacker Node...
set NODE_ID=attacker-http-01
set NODE_TYPE=http
set CONTROLLER_URL=https://10.100.1.10:8443
set ATTACK_TYPES=http_flood,slowloris
set MAX_RPS=10000
set MAX_CONCURRENT=5000
ddos-attacker.exe
""")

    print(f"\n✓ Attacker built: {DIST_DIR / 'attacker' / f'ddos-attacker{EXT}'}")


def create_package():
    """打包为 tar.gz / zip 发布"""
    banner("Creating Release Package")

    package_dir = BUILD_DIR / "_package"
    shutil.rmtree(package_dir, ignore_errors=True)
    package_dir.mkdir(parents=True, exist_ok=True)

    # Controller
    shutil.copytree(DIST_DIR / "controller", package_dir / "controller", dirs_exist_ok=True)
    # Attacker
    shutil.copytree(DIST_DIR / "attacker", package_dir / "attacker", dirs_exist_ok=True)
    # README + 文档
    shutil.copy(ROOT / "README.md", package_dir)
    shutil.copy(ROOT / "docs" / "SAFETY_RULES.md", package_dir)
    shutil.copy(ROOT / "docs" / "TEACHING_GUIDE.md", package_dir)

    archive_name = f"ddos-attack-platform-{PLATFORM}"

    if PLATFORM == "win32":
        shutil.make_archive(str(DIST_DIR / archive_name), 'zip', package_dir)
        print(f"\n✓ Package: {DIST_DIR / archive_name}.zip")
    else:
        shutil.make_archive(str(DIST_DIR / archive_name), 'gztar', package_dir)
        print(f"\n✓ Package: {DIST_DIR / archive_name}.tar.gz")


def install_pyinstaller():
    """安装 PyInstaller"""
    run(f"{sys.executable} -m pip install pyinstaller --quiet")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"

    os.chdir(ROOT)
    install_pyinstaller()

    try:
        if cmd in ("all", "controller"):
            build_controller()
        if cmd in ("all", "attacker"):
            build_attacker()
        if cmd == "package":
            create_package()

        banner("Build Complete")
        print(f"  Controller:  {DIST_DIR / 'controller'}")
        print(f"  Attacker:    {DIST_DIR / 'attacker'}")
        print("\n  Deploy:")
        print(f"    cd {DIST_DIR / 'controller'} && ./start.sh")
        print(f"    cd {DIST_DIR / 'attacker'} && ./start.sh")

    except subprocess.CalledProcessError as e:
        print(f"\n✗ Build failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Error: {e}")
        sys.exit(1)