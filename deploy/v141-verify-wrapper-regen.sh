#!/bin/bash
# REG-5 验证: 升级时 wrapper 自身应被重新生成
# 测试逻辑:
# 1. 记录原 wrapper 长度
# 2. 强制修改 wrapper 内容 (添加假行)
# 3. 升级
# 4. 验证 wrapper 长度/内容已恢复为最新 install script 提取
set +e
echo root | sudo -S -v 2>&1 | head -1
SHARED_SECRET=$(echo root | sudo -S grep '^SHARED_SECRET=' /etc/ddos-controller/config.env | cut -d= -f2)
ADMIN_TOKEN=$(echo -n "ddos-controller-auth" | openssl dgst -sha256 -hmac "$SHARED_SECRET" | awk '{print $2}')

PASS=0
FAIL=0

WRAPPER=/usr/local/bin/ddos-controller

# --- 0. 备份 ---
echo "=== 0. 备份 wrapper ==="
echo root | sudo -S cp "$WRAPPER" /tmp/wrapper.before
ORIG_LINES=$(echo root | sudo -S wc -l < "$WRAPPER" 2>&1 | tr -d ' ')
echo "  原 wrapper 行数: $ORIG_LINES"

# --- 1. 修改 wrapper 添加假行 ---
echo ""
echo "=== 1. 注入测试标记到 wrapper ==="
echo root | sudo -S bash -c "echo '# REG-5-TEST-MARKER: this should be removed after update' >> $WRAPPER"
NEW_LINES=$(echo root | sudo -S wc -l < "$WRAPPER" 2>&1 | tr -d ' ')
echo "  注入后行数: $NEW_LINES"
if [[ "$NEW_LINES" -gt "$ORIG_LINES" ]]; then
    echo "  PASS: 注入成功"
    PASS=$((PASS+1))
else
    echo "  FAIL: 注入未生效"
    FAIL=$((FAIL+1))
fi

# --- 2. 升级 ---
echo ""
echo "=== 2. 执行 ddos-controller update ==="
echo "n" | echo root | sudo -S ddos-controller update 2>&1 | tail -10

# --- 3. 验证 wrapper 标记消失 ---
echo ""
echo "=== 3. 验证 wrapper 标记消失 (REG-5) ==="
if echo root | sudo -S grep -q 'REG-5-TEST-MARKER' "$WRAPPER" 2>&1; then
    echo "  FAIL: 假标记仍存在, wrapper 未重新生成"
    FAIL=$((FAIL+1))
else
    echo "  PASS: 假标记消失, wrapper 已重新生成"
    PASS=$((PASS+1))
fi

# --- 4. 验证 wrapper 包含最新 NODE_TLS_* 值 (空字符串) ---
echo ""
echo "=== 4. 验证 wrapper 含最新 NODE_TLS 配置 (REG-3) ==="
if echo root | sudo -S grep -q 'NODE_TLS_CA_FILE=.*""' "$WRAPPER" 2>&1; then
    echo "  PASS: wrapper 已使用空 NODE_TLS_CA_FILE"
    PASS=$((PASS+1))
else
    echo "  FAIL: wrapper 仍是旧 ca-cert.pem 值"
    FAIL=$((FAIL+1))
fi

# --- 5. 验证服务仍 active ---
echo ""
echo "=== 5. 服务仍 active ==="
STATE=$(echo root | sudo -S systemctl is-active ddos-controller 2>&1)
if [[ "$STATE" == "active" ]]; then
    echo "  PASS: 服务 active"
    PASS=$((PASS+1))
else
    echo "  FAIL: 服务 $STATE"
    FAIL=$((FAIL+1))
fi

# --- 6. 验证 controller 正常 (TLS 行为符合预期) ---
echo ""
echo "=== 6. controller 仍正常 ==="
HEALTH=$(echo root | sudo -S curl -skf --max-time 5 https://127.0.0.1:8443/health 2>&1)
if echo "$HEALTH" | grep -q '"status":"healthy"'; then
    echo "  PASS: health=healthy"
    PASS=$((PASS+1))
else
    echo "  FAIL: $HEALTH"
    FAIL=$((FAIL+1))
fi

# --- 7. 节点重连 (12s 等待) ---
echo ""
echo "=== 7. 节点重连 ==="
sleep 12
NODE=$(echo root | sudo -S curl -sk -H "Authorization: Bearer $ADMIN_TOKEN" "https://127.0.0.1:8443/api/v1/nodes" 2>&1 | python3 -c "
import json,sys
d = json.load(sys.stdin).get('data', [])
print(len(d))
")
if [[ "$NODE" -ge 1 ]]; then
    echo "  PASS: 节点重连 (online: $NODE)"
    PASS=$((PASS+1))
else
    echo "  FAIL: 节点未重连"
    FAIL=$((FAIL+1))
fi

echo ""
echo "=========================="
echo "RESULT: $PASS passed, $FAIL failed"
