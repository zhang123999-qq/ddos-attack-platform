#!/bin/bash
# 验证节点自动注册 + 心跳
set +e
echo root | sudo -S -v 2>&1 | head -1
SHARED_SECRET=$(echo root | sudo -S grep '^SHARED_SECRET=' /etc/ddos-controller/config.env | cut -d= -f2)
ADMIN_TOKEN=$(echo -n "ddos-controller-auth" | openssl dgst -sha256 -hmac "$SHARED_SECRET" | awk '{print $2}')

PASS=0
FAIL=0

# --- 1. 节点列表 ---
echo "=== 1. /api/v1/nodes 节点列表 ==="
NODES=$(echo root | sudo -S curl -sk -H "Authorization: Bearer $ADMIN_TOKEN" "https://127.0.0.1:8443/api/v1/nodes" 2>&1)
echo "  body: $NODES" | head -3
if echo "$NODES" | grep -q 'attacker-http-v141fix'; then
    echo "  PASS: 节点已注册"
    PASS=$((PASS+1))
else
    echo "  FAIL: 节点未注册"
    FAIL=$((FAIL+1))
fi

# --- 2. 节点详情 ---
echo ""
echo "=== 2. /api/v1/nodes/attacker-http-v141fix 详情 ==="
DETAIL=$(echo root | sudo -S curl -sk -H "Authorization: Bearer $ADMIN_TOKEN" "https://127.0.0.1:8443/api/v1/nodes/attacker-http-v141fix" 2>&1)
echo "  body: $DETAIL" | head -5
if echo "$DETAIL" | grep -q '"status":"online"'; then
    echo "  PASS: 节点 status=online"
    PASS=$((PASS+1))
else
    echo "  FAIL: 节点 status 非 online"
    FAIL=$((FAIL+1))
fi

# --- 3. 节点详情中的 heartbeat 字段 (last_heartbeat 已显示在 nodes 列表) ---
echo ""
echo "=== 3. 节点 last_heartbeat 字段 ==="
HB_FIELD=$(echo root | sudo -S curl -sk -H "Authorization: Bearer $ADMIN_TOKEN" "https://127.0.0.1:8443/api/v1/nodes/attacker-http-v141fix" 2>&1)
HB1=$(echo "$HB_FIELD" | python3 -c "import json,sys; d=json.load(sys.stdin).get('data', {}); print(d.get('last_heartbeat', '?'))")
echo "  last_heartbeat: $HB1"
if [[ -n "$HB1" && "$HB1" != "None" && "$HB1" != "?" ]]; then
    echo "  PASS: last_heartbeat 存在"
    PASS=$((PASS+1))
else
    echo "  FAIL: last_heartbeat 缺失"
    FAIL=$((FAIL+1))
fi

# --- 4. 等待 12s 观察心跳变化 (心跳间隔默认 10s) ---
echo ""
echo "=== 4. 等待 12s 观察心跳变化 ==="
sleep 12
HB2=$(echo root | sudo -S curl -sk -H "Authorization: Bearer $ADMIN_TOKEN" "https://127.0.0.1:8443/api/v1/nodes/attacker-http-v141fix" 2>&1 | python3 -c "import json,sys; d=json.load(sys.stdin).get('data', {}); print(d.get('last_heartbeat', '?'))")
echo "  HB1: $HB1"
echo "  HB2: $HB2"
if [[ "$HB1" != "$HB2" && -n "$HB2" ]]; then
    echo "  PASS: 心跳持续上报"
    PASS=$((PASS+1))
else
    echo "  FAIL: 心跳未变化"
    FAIL=$((FAIL+1))
fi

# --- 5. 节点健康 (attacker 自身) ---
echo ""
echo "=== 5. 节点健康 (attacker /health) ==="
NH=$(echo root | sudo -S curl -skf --max-time 5 http://127.0.0.1:8080/health 2>&1)
echo "  body: $NH"
if echo "$NH" | grep -q '"status":"healthy"'; then
    echo "  PASS: 节点 health=healthy (node_id 已包含在 body 中)"
    PASS=$((PASS+1))
else
    echo "  FAIL: 节点 health 异常"
    FAIL=$((FAIL+1))
fi

# --- 6. metrics 端点 (Prometheus) ---
echo ""
echo "=== 6. 节点 /metrics (Prometheus) ==="
MT=$(echo root | sudo -S curl -skf --max-time 5 http://127.0.0.1:8080/metrics 2>&1 | head -5)
echo "  $MT"
if echo "$MT" | grep -q 'ddos\|node_'; then
    echo "  PASS: Prometheus 指标可用"
    PASS=$((PASS+1))
else
    echo "  FAIL: metrics 不可用"
    FAIL=$((FAIL+1))
fi

echo ""
echo "=========================="
echo "RESULT: $PASS passed, $FAIL failed"
