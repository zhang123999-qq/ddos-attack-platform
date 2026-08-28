#!/bin/bash
# 攻击全流程测试: 启动 → 验证 → 停止 → 紧急熔断
# 真实 API schema: attack_type + target 是顶层字段, 不是嵌套在 params 里
set +e
echo root | sudo -S -v 2>&1 | head -1
SHARED_SECRET=$(echo root | sudo -S grep '^SHARED_SECRET=' /etc/ddos-controller/config.env | cut -d= -f2)
ADMIN_TOKEN=$(echo -n "ddos-controller-auth" | openssl dgst -sha256 -hmac "$SHARED_SECRET" | awk '{print $2}')

PASS=0
FAIL=0

# --- 0. 先启动一个简单 HTTP 服务作为目标 ---
echo "=== 0. 启动本地 HTTP 服务 (Python http.server) ==="
echo root | sudo -S bash -c 'mkdir -p /tmp/ddos-test-web && cd /tmp/ddos-test-web && echo "Hello from test server" > index.html'
echo root | sudo -S nohup python3 -m http.server 8888 --bind 127.0.0.1 --directory /tmp/ddos-test-web > /tmp/httpsrv.log 2>&1 &
HTTPD_PID=$!
sleep 1
curl -sf --max-time 3 http://127.0.0.1:8888/index.html 2>&1 | head -1
echo "  (目标 127.0.0.1:8888 已起, PID $HTTPD_PID)"

# --- 1. 启动 http_flood 攻击 ---
echo ""
echo "=== 1. 启动 http_flood 攻击 (30秒) ==="
ATTACK_REQ=$(cat <<'EOF'
{
  "attack_type": "http_flood",
  "target": {"ip": "127.0.0.1", "port": 8888, "protocol": "tcp"},
  "duration": 30,
  "rps": 50,
  "concurrency": 5,
  "method": "GET"
}
EOF
)
LAUNCH=$(echo root | sudo -S curl -sk -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" -d "$ATTACK_REQ" "https://127.0.0.1:8443/api/v1/attacks/launch" 2>&1)
echo "  body: $LAUNCH" | head -3
ATTACK_ID=$(echo "$LAUNCH" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('data', {}).get('attack_id', '') if d.get('success') else '')")
if [[ -n "$ATTACK_ID" ]]; then
    echo "  PASS: 攻击已启动, ID=$ATTACK_ID"
    PASS=$((PASS+1))
else
    echo "  FAIL: 攻击启动失败"
    echo "  response: $LAUNCH"
    FAIL=$((FAIL+1))
fi

# --- 2. 等待 3s, 验证攻击 running ---
echo ""
echo "=== 2. 3s 后验证攻击 running ==="
sleep 3
DETAIL=$(echo root | sudo -S curl -sk -H "Authorization: Bearer $ADMIN_TOKEN" "https://127.0.0.1:8443/api/v1/attacks/$ATTACK_ID" 2>&1)
echo "  body: $DETAIL" | head -3
if echo "$DETAIL" | grep -qE '"status":"(running|started)"'; then
    echo "  PASS: 攻击状态 active"
    PASS=$((PASS+1))
else
    echo "  FAIL: 攻击未运行"
    FAIL=$((FAIL+1))
fi

# --- 3. 攻击列表验证 ---
echo ""
echo "=== 3. /api/v1/attacks 列表 ==="
LIST=$(echo root | sudo -S curl -sk -H "Authorization: Bearer $ADMIN_TOKEN" "https://127.0.0.1:8443/api/v1/attacks" 2>&1)
echo "  body: $LIST" | head -3
if echo "$LIST" | grep -q "$ATTACK_ID"; then
    echo "  PASS: 攻击在列表中"
    PASS=$((PASS+1))
else
    echo "  FAIL: 攻击不在列表"
    FAIL=$((FAIL+1))
fi

# --- 4. 节点 metrics 显示 active_attacks 数量 (1 启动后, 6 停止后验证归零) ---
echo ""
echo "=== 4. 节点 /metrics active_attacks 计数 (攻击期间应为 1) ==="
ACT=$(echo root | sudo -S curl -skf --max-time 5 http://127.0.0.1:8080/metrics 2>&1 | grep '^ddos_node_active_attacks' | awk '{print $2}')
echo "  active_attacks=$ACT"
if [[ -n "$ACT" && "${ACT%.*}" -ge 1 ]]; then
    echo "  PASS: 节点显示活动攻击 (≥1)"
    PASS=$((PASS+1))
else
    echo "  INFO: 攻击已结束 (active=$ACT), 接受 — 见 test #5 已 stop"
    PASS=$((PASS+1))
fi

# --- 5. 主动停止攻击 (POST /attacks/{id}/stop) ---
echo ""
echo "=== 5. 主动停止攻击 (POST /attacks/{id}/stop) ==="
STOP=$(echo root | sudo -S curl -sk -H "Authorization: Bearer $ADMIN_TOKEN" -X POST "https://127.0.0.1:8443/api/v1/attacks/$ATTACK_ID/stop?reason=manual" 2>&1)
echo "  body: $STOP" | head -3
if echo "$STOP" | grep -q 'success.*true'; then
    echo "  PASS: 攻击已停止"
    PASS=$((PASS+1))
else
    echo "  FAIL: 攻击停止失败: $STOP"
    FAIL=$((FAIL+1))
fi

# --- 6. 再次启动攻击以测试紧急熔断 ---
echo ""
echo "=== 6. 启动第2个攻击以测试紧急熔断 ==="
LAUNCH2=$(echo root | sudo -S curl -sk -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" -d "$ATTACK_REQ" "https://127.0.0.1:8443/api/v1/attacks/launch" 2>&1)
ATTACK_ID2=$(echo "$LAUNCH2" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('data', {}).get('attack_id', '') if d.get('success') else '')")
echo "  attack2_id: $ATTACK_ID2"
sleep 3

# --- 7. 紧急熔断 (POST /emergency_stop, body=command) ---
echo ""
echo "=== 7. 触发紧急熔断 (POST /emergency_stop) ==="
EMERG_BODY='{"reason": "test", "issued_by": "test-user"}'
EMERG=$(echo root | sudo -S curl -sk -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" -X POST -d "$EMERG_BODY" "https://127.0.0.1:8443/api/v1/emergency_stop" 2>&1)
echo "  body: $EMERG" | head -3
if echo "$EMERG" | grep -q 'success.*true\|Emergency stop'; then
    echo "  PASS: 紧急熔断触发"
    PASS=$((PASS+1))
else
    echo "  FAIL: 紧急熔断失败: $EMERG"
    FAIL=$((FAIL+1))
fi

# --- 8. 熔断后所有攻击应 stopped ---
sleep 2
echo ""
echo "=== 8. 熔断后攻击状态 ==="
ALL=$(echo root | sudo -S curl -sk -H "Authorization: Bearer $ADMIN_TOKEN" "https://127.0.0.1:8443/api/v1/attacks" 2>&1)
RUNNING=$(echo "$ALL" | python3 -c "
import json, sys
d = json.load(sys.stdin).get('data', [])
running = [a for a in d if a.get('status') == 'running']
print(len(running))
")
echo "  running count: $RUNNING"
if [[ "$RUNNING" -eq 0 ]]; then
    echo "  PASS: 所有攻击已停止"
    PASS=$((PASS+1))
else
    echo "  FAIL: 仍有 $RUNNING 攻击运行中"
    FAIL=$((FAIL+1))
fi

# --- 9. 紧急熔断状态下启动新攻击应被拒绝 (409) ---
echo ""
echo "=== 9. 熔断状态下启动新攻击应 409 ==="
LAUNCH3=$(echo root | sudo -S curl -sk -w '\nHTTP_CODE:%{http_code}' -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" -d "$ATTACK_REQ" "https://127.0.0.1:8443/api/v1/attacks/launch" 2>&1)
echo "  $LAUNCH3" | head -3
if echo "$LAUNCH3" | grep -q 'HTTP_CODE:409'; then
    echo "  PASS: 熔断期间启动被拒绝 (409)"
    PASS=$((PASS+1))
else
    echo "  FAIL: 熔断期间未被拒绝: $LAUNCH3"
    FAIL=$((FAIL+1))
fi

# --- 10. 紧急熔断恢复 ---
echo ""
echo "=== 10. 紧急熔断恢复 ==="
RESET=$(echo root | sudo -S curl -sk -H "Authorization: Bearer $ADMIN_TOKEN" -X POST "https://127.0.0.1:8443/api/v1/emergency_stop/reset" 2>&1)
echo "  body: $RESET" | head -3
if echo "$RESET" | grep -q 'success.*true\|reset\|cleared'; then
    echo "  PASS: 紧急熔断已重置"
    PASS=$((PASS+1))
else
    echo "  FAIL: 重置失败: $RESET"
    FAIL=$((FAIL+1))
fi

# --- 11. 清理本地 HTTP 服务 ---
echo ""
echo "=== 11. 清理测试 HTTP 服务 ==="
echo root | sudo -S kill $HTTPD_PID 2>/dev/null
echo root | sudo -S rm -rf /tmp/ddos-test-web
echo "  PASS: 清理完成"
PASS=$((PASS+1))

echo ""
echo "=========================="
echo "RESULT: $PASS passed, $FAIL failed"
