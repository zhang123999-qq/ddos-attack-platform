"""API 冒烟测试 — 真实启动 FastAPI app (含 lifespan) 验证端到端行为

在 controller/ 目录下运行: python tests/test_api_smoke.py
覆盖: /health、节点注册身份一致性 (P1-3)、请求边界校验、场景列表
"""
import sys
import hmac
import hashlib
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

SECRET = os.getenv("SHARED_SECRET", "").encode() or b"insecure-default-change-me-32chars"


def node_token(node_id: str) -> str:
    return hmac.new(SECRET, node_id.encode(), hashlib.sha256).hexdigest()


def admin_token() -> str:
    return hmac.new(SECRET, b"ddos-controller-auth", hashlib.sha256).hexdigest()


def main():
    with TestClient(app) as client:
        # 1. 健康检查
        r = client.get("/health")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "healthy"
        print("HEALTH OK")

        # 2. P1-3: 身份不匹配的注册必须 403
        headers = {"X-Node-ID": "node-x", "X-Node-Token": node_token("node-x")}
        bad_body = {
            "node_id": "spoofed-node", "node_type": "http",
            "ip": "127.0.0.1", "hostname": "evil", "cpu_cores": 1, "memory_gb": 1,
        }
        r = client.post("/api/v1/nodes/register", json=bad_body, headers=headers)
        assert r.status_code == 403, f"expected 403 for spoofed node_id, got {r.status_code}"
        print("REGISTER SPOOF BLOCKED OK (403)")

        # 3. 身份一致的注册成功
        ok_body = dict(bad_body, node_id="node-x")
        r = client.post("/api/v1/nodes/register", json=ok_body, headers=headers)
        assert r.status_code == 200, r.text
        print("REGISTER MATCHED OK")

        # 4. 越界参数 → 422 (原实现穿透到 AttackParams 构造炸 500)
        admin = {"Authorization": f"Bearer {admin_token()}"}
        attack_body = {
            "attack_type": "http_flood",
            "target": {"ip": "127.0.0.1", "port": 80},
            "duration": 60, "rps": 99999999, "concurrency": 100,
        }
        r = client.post("/api/v1/attacks/launch", json=attack_body, headers=admin)
        assert r.status_code == 422, f"expected 422 for out-of-bounds rps, got {r.status_code}"
        print("LAUNCH BOUNDS VALIDATION OK (422)")

        # 5. 场景列表非空 (P0-3 回归)
        r = client.get("/api/v1/scenarios", headers=admin)
        assert r.status_code == 200
        data = r.json()["data"]
        assert len(data) >= 6, f"expected >=6 scenarios, got {len(data)}"
        print(f"SCENARIOS LISTED OK ({len(data)})")


if __name__ == "__main__":
    main()
    print("ALL API SMOKE TESTS PASSED")
else:
    # pytest 入口: 复用脚本式 main() 作为单个收集单元
    def test_api_smoke_end_to_end():
        main()
