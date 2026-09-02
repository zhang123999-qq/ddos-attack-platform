"""Pytest 全局配置: 设置测试环境变量, 让 controller/ 和 attacker/ 下的测试都可用

关键设计: 本 conftest 仅设置环境变量, 不处理 sys.path (避免同进程内两个项目
的 'app' 包命名冲突)。controller 和 attacker 各自的 conftest.py (controller/conftest.py,
attacker/conftest.py) 单独负责 sys.path 切换。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 测试环境变量默认值 (仅在未设置时填充)
os.environ.setdefault("SHARED_SECRET", "test-secret-32chars-abcdef1234567890")
os.environ.setdefault("ADMIN_RATE_LIMIT_RPM", "0")  # 关闭避免 429 干扰测试
os.environ.setdefault("ALLOW_ANY_TARGET", "true")
os.environ.setdefault(
    "ALLOWED_TARGET_CIDRS",
    "127.0.0.1/32,10.100.0.0/16,192.168.0.0/16,172.16.0.0/12",
)
os.environ.setdefault("NODE_INSECURE_PLAIN_HTTP", "true")
os.environ.setdefault("NODE_PLAIN_HTTP_BANNED", "false")
os.environ.setdefault("LOG_LEVEL", "warning")
os.environ.setdefault("AUDIT_FILE_ENABLED", "false")
os.environ.setdefault("ENABLE_WEB_UI", "false")
os.environ.setdefault("STATE_STORE_PATH", str(Path(tempfile.gettempdir()) / "ddos_state_test.db"))
os.environ.setdefault("CA_STORAGE_DIR", str(Path(tempfile.gettempdir()) / "ddos_ca_test"))
os.environ.setdefault("TLS_CA_FILE", str(Path(tempfile.gettempdir()) / "ca-cert-test.pem"))
os.environ.setdefault("TLS_CERT_FILE", str(Path(tempfile.gettempdir()) / "controller-cert-test.pem"))
os.environ.setdefault("TLS_KEY_FILE", str(Path(tempfile.gettempdir()) / "controller-key-test.pem"))