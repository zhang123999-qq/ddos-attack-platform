#!/bin/bash
# 升级路径测试: v1.4.1-hotfix2 升级 v1.4.1-hotfix2 应幂等
set +e
echo root | sudo -S -v 2>&1 | head -1
SHARED_SECRET=$(echo root | sudo -S grep '^SHARED_SECRET=' /etc/ddos-controller/config.env | cut -d= -f2)
ADMIN_TOKEN=$(echo -n "ddos-controller-auth" | openssl dgst -sha256 -hmac "$SHARED_SECRET" | awk '{print $2}')

PASS=0
FAIL=0

# --- 0. 备份 config.env 当前状态 ---
echo "=== 0. 备份 config.env ==="
echo root | sudo -S cp /etc/ddos-controller/config.env /tmp/config.env.before
echo "  备份完成"

# --- 1. 执行 update (跳过重新生成证书) ---
echo ""
echo "=== 1. 执行 ddos-controller update ==="
# update 应该跳过 cert 重新生成, 保留 config.env
echo "n" | echo root | sudo -S ddos-controller update 2>&1 | tail -20

# --- 2. 验证服务还在运行 (config.env 保留) ---
echo ""
echo "=== 2. 验证服务 active ==="
STATE=$(echo root | sudo -S systemctl is-active ddos-controller 2>&1)
echo "  service: $STATE"
if [[ "$STATE" == "active" ]]; then
    echo "  PASS: 服务保持 active"
    PASS=$((PASS+1))
else
    echo "  FAIL: 服务未运行"
    FAIL=$((FAIL+1))
fi

# --- 3. config.env 幂等性: SHARED_SECRET 应保留 ---
echo ""
echo "=== 3. config.env 幂等性 (SHARED_SECRET 保留) ==="
SECRET_AFTER=$(echo root | sudo -S grep '^SHARED_SECRET=' /etc/ddos-controller/config.env | cut -d= -f2)
SECRET_BEFORE=$(grep '^SHARED_SECRET=' /tmp/config.env.before | cut -d= -f2)
if [[ "$SECRET_AFTER" == "$SECRET_BEFORE" ]]; then
    echo "  PASS: SHARED_SECRET 保留 ($SECRET_AFTER)"
    PASS=$((PASS+1))
else
    echo "  FAIL: SHARED_SECRET 变化"
    echo "  before: $SECRET_BEFORE"
    echo "  after:  $SECRET_AFTER"
    FAIL=$((FAIL+1))
fi

# --- 4. NODE_TLS_* 配置幂等性 ---
echo ""
echo "=== 4. NODE_TLS_* 幂等性 ==="
TLS_COUNT=$(echo root | sudo -S grep -c '^NODE_TLS_' /etc/ddos-controller/config.env 2>&1)
echo "  NODE_TLS_* lines: $TLS_COUNT"
if [[ "$TLS_COUNT" -le 1 ]]; then
    echo "  PASS: 升级未引入新 TLS 配置"
    PASS=$((PASS+1))
else
    echo "  FAIL: 升级引入了新 TLS 配置 ($TLS_COUNT 条)"
    FAIL=$((FAIL+1))
fi

# --- 5. update 后服务仍可用 ---
echo ""
echo "=== 5. update 后 /health 仍可用 ==="
HEALTH=$(echo root | sudo -S curl -skf --max-time 5 https://127.0.0.1:8443/health 2>&1)
echo "  body: $HEALTH"
if echo "$HEALTH" | grep -q '"status":"healthy"'; then
    echo "  PASS: 升级后服务仍 healthy"
    PASS=$((PASS+1))
else
    echo "  FAIL: 升级后 health 失败"
    FAIL=$((FAIL+1))
fi

# --- 6. 节点重连 ---
echo ""
echo "=== 6. 节点重连 (12s 等待) ==="
sleep 12
NODE=$(echo root | sudo -S curl -sk -H "Authorization: Bearer $ADMIN_TOKEN" "https://127.0.0.1:8443/api/v1/nodes" 2>&1 | python3 -c "
import json,sys
d = json.load(sys.stdin).get('data', [])
print(len(d))
")
echo "  nodes online: $NODE"
if [[ "$NODE" -ge 1 ]]; then
    echo "  PASS: 节点重连成功"
    PASS=$((PASS+1))
else
    echo "  FAIL: 节点未重连"
    FAIL=$((FAIL+1))
fi

echo ""
echo "=========================="
echo "RESULT: $PASS passed, $FAIL failed"
