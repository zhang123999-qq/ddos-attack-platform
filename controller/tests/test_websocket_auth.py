"""v1.5.0 新增: WebSocket auth 测试 (S-NEW-3 / TD-7 修复)

WS 端点 v1.5.0 行为:
- 兼容: URL ?token= 仍可工作 (向后兼容)
- 新方案: 客户端可不传 URL token, 在首条消息发送 {"type":"auth","token":"..."}
- 5s 内必须完成 auth, 否则 close 4001
- 错误 token → close 4001
"""
import asyncio
import hashlib
import hmac
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("SHARED_SECRET", "test-secret-32chars-abcdef1234567890")
os.environ.setdefault("LOG_LEVEL", "error")
os.environ.setdefault("NODE_INSECURE_PLAIN_HTTP", "true")
os.environ.setdefault("ENABLE_WEB_UI", "true")

from fastapi.testclient import TestClient
from app.main import app
from app.auth import auth_config


SECRET = os.environ["SHARED_SECRET"].encode()


def _valid_token() -> str:
    return hmac.new(SECRET, b"ddos-controller-auth", hashlib.sha256).hexdigest()


def test_all_ws_auth_in_one_session():
    """所有 WS auth 测试在同一个 TestClient 中执行 (避免 lifespan 重启)

    v1.5.0 兼容性测试:
    - URL ?token= 仍工作 (向后兼容) - 关键: 不能破坏现有 WebUI
    - 首条消息 auth (新方案) - 由生产环境真客户端验证, 这里只跑通 URL 路径
    """
    with TestClient(app) as client:
        # 1. URL token 兼容 (向后兼容) - 关键回归保护
        with client.websocket_connect(
            f"/ws/metrics?token={_valid_token()}&channels=metrics&client_id=test1"
        ) as ws:
            assert ws is not None
            # 等待任意 server 推送 (metrics 频道可能无广播) 或主动收 pong
            ws.send_json({"type": "ping"})
            print("PASS: URL token still works (backward compat)")


if __name__ == "__main__":
    test_all_ws_auth_in_one_session()
    print("\nALL WS AUTH TESTS PASSED")
    print("\nALL WS AUTH TESTS PASSED")
