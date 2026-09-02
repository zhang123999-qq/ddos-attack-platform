"""v1.5.0 新增: 节点自助 enroll 路由 (签发 mTLS 客户端证书)

- POST /api/v1/nodes/enroll - 节点接入, 返回 PEM 内联证书
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.auth import auth_config
from app.deps import (
    NODE_ID_RE, audit_event, public_base_url, sleep_jitter_on_auth_fail
)
from app.cert_authority import cert_authority

router = APIRouter(prefix="/api/v1/nodes", tags=["enroll"])


class EnrollRequest(BaseModel):
    node_id: str
    enroll_token: str
    node_ip: Optional[str] = None


@router.post("/enroll", include_in_schema=False)
async def enroll_node(req: EnrollRequest, request: Request):
    """节点自助接入 (无认证端点, 由无状态 enroll token 把关)

    校验 HMAC(secret,'ddos-enroll:'+node_id+':'+小时桶) → 返回运行所需配置 + mTLS 证书。
    v1.5.0: Controller 内置 CA 直接签发 node 客户端证书, 节点安装后强制 HTTPS+mTLS。
    """
    if not NODE_ID_RE.match(req.node_id):
        raise HTTPException(status_code=400, detail="Invalid node_id format")

    # v1.5.0 (A.3): enroll 端点也限流 (防爆破, 5 RPM — 略高于 admin)
    from app.admin_rate_limit import admin_rate_limiter
    await admin_rate_limiter.check_or_raise("nodes.enroll")

    if not auth_config.verify_enroll_token(req.node_id, req.enroll_token.strip()):
        await sleep_jitter_on_auth_fail(1.0)  # 拖慢爆破
        await audit_event("node_enroll_failed", req.node_id, {
            "source_ip": request.client.host if request.client else "unknown"
        })
        raise HTTPException(status_code=403, detail="Invalid or expired enroll token")

    secret = os.getenv("SHARED_SECRET") or auth_config.shared_secret.decode()
    cidrs = [c.strip() for c in os.getenv("ALLOWED_TARGET_CIDRS", "127.0.0.0/8").split(",") if c.strip()]
    allow_any = os.getenv("ALLOW_ANY_TARGET", "false").lower() == "true"

    try:
        cert_pem, key_pem = cert_authority.issue_node_cert(
            node_id=req.node_id, node_ip=req.node_ip, validity_days=None,
        )
    except Exception as e:
        await audit_event("node_enroll_failed", req.node_id, {
            "reason": "cert_issue_failed", "error": str(e),
            "source_ip": request.client.host if request.client else "unknown"
        })
        raise HTTPException(status_code=500, detail="Cert issuance failed")

    await audit_event("node_enroll_success", req.node_id, {
        "source_ip": request.client.host if request.client else "unknown",
        "node_ip": req.node_ip, "cert_serial": "issued",
    })
    return {
        "node_id": req.node_id,
        "shared_secret": secret,
        "allowed_target_cidrs": ",".join(cidrs),
        "allow_any_target": allow_any,
        "node_cert_pem": cert_pem.decode("utf-8"),
        "node_key_pem": key_pem.decode("utf-8"),
        "ca_cert_url": f"{public_base_url(request)}/artifacts/ca-cert.pem",
        "tls_fingerprint": auth_config.get_tls_fingerprint(),
        "node_use_tls": True,
        "node_tls_ca_file": "/certs/ca-cert.pem",
        "node_tls_cert_file": "/certs/node-cert.pem",
        "node_tls_key_file": "/certs/node-key.pem",
        "enrolled_at": "auto",
    }


def register(app) -> None:
    app.include_router(router)
