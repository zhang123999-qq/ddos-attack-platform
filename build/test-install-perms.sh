#!/bin/bash
# v1.3.4 安装器加固 E2E 验证
# 此脚本必须以 root 权限执行 (sudo) 因为检查文件 600/640/750
# WSL 推荐: echo root | sudo -S bash /tmp/install-perms.sh
set +e
LOG=/tmp/install-perms-test.log
exec > >(tee -a $LOG) 2>&1

# 确保我们是 root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: 此脚本必须以 root 执行"
    echo "用法: echo root | sudo -S bash $0"
    exit 1
fi

PASS=0; FAIL=0

echo "=== v1.3.4 安装器加固 E2E ==="
echo root | sudo -S -v 2>&1 | head -1

echo "--- 0. 清理旧安装 ---"
systemctl stop ddos-controller ddos-attacker 2>/dev/null
systemctl disable ddos-controller ddos-attacker 2>/dev/null
rm -rf /opt/ddos-attack-platform /etc/ddos-controller /etc/ddos-attacker
rm -f /etc/systemd/system/ddos-controller.service /etc/systemd/system/ddos-attacker.service
rm -f /usr/local/bin/ddos-controller /usr/local/bin/ddos-node
userdel ddos 2>/dev/null
systemctl daemon-reload

echo "--- 1. ddos 用户应不存在 ---"
id ddos 2>/dev/null && echo "  pre: ddos user exists (unexpected)" || echo "  pre: ddos user not yet created (good)"

echo "--- 2. 运行 controller-install.sh ---"
cd /tmp
curl -sLfo /tmp/ci.sh https://raw.githubusercontent.com/zhang123999-qq/ddos-attack-platform/master/deploy/controller-install.sh
chmod +x /tmp/ci.sh
# 提示用户输入; 但用 heredoc 形式不依赖交互
cat > /tmp/ci-input.txt <<'INPUT'
8443
regression-v134-secret-32chars-abcdef

INPUT
bash /tmp/ci.sh < /tmp/ci-input.txt 2>&1 | tail -20

echo "--- 3. ddos 用户应已创建 ---"
if id ddos 2>/dev/null; then echo "  PASS: ddos user exists"; PASS=$((PASS+1)); else echo "  FAIL: ddos user missing"; FAIL=$((FAIL+1)); fi

echo "--- 4. /etc/ddos-controller/config.env 应为 600 ddos:ddos ---"
if [ -f /etc/ddos-controller/config.env ]; then
    PERMS=$(stat -c "%a %U:%G" /etc/ddos-controller/config.env)
    echo "  实际: $PERMS"
    if [ "$PERMS" = "600 ddos:ddos" ]; then echo "  PASS"; PASS=$((PASS+1)); else echo "  FAIL"; FAIL=$((FAIL+1)); fi
else
    echo "  FAIL: 文件不存在"
    FAIL=$((FAIL+1))
fi

echo "--- 5. /opt/.../controller 应为 750 ddos:ddos ---"
PERMS=$(stat -c "%a %U:%G" /opt/ddos-attack-platform/controller)
echo "  实际: $PERMS"
if [ "$PERMS" = "750 ddos:ddos" ]; then echo "  PASS"; PASS=$((PASS+1)); else echo "  FAIL"; FAIL=$((FAIL+1)); fi

echo "--- 6. /etc/systemd/system/ddos-controller.service 应为 640 ---"
PERMS=$(stat -c "%a" /etc/systemd/system/ddos-controller.service 2>/dev/null)
echo "  实际: $PERMS"
if [ "$PERMS" = "640" ]; then echo "  PASS"; PASS=$((PASS+1)); else echo "  FAIL"; FAIL=$((FAIL+1)); fi

echo "--- 7. service unit 应含 User=ddos Group=ddos ---"
# service unit 640 root 只读 metadata, 内容需用 sudo
if sudo -n cat /etc/systemd/system/ddos-controller.service 2>/dev/null | grep -q '^User=ddos$' && \
   sudo -n cat /etc/systemd/system/ddos-controller.service 2>/dev/null | grep -q '^Group=ddos$'; then
    echo "  PASS: User=ddos Group=ddos"
    PASS=$((PASS+1))
else
    echo "  FAIL: 缺 User/Group ddos"
    FAIL=$((FAIL+1))
fi

echo "--- 8. controller 进程应运行在 ddos 用户 ---"
sleep 5
PID=$(ps -ef | grep '/ddos-controller' | grep -v grep | awk '{print $1}' | head -1)
echo "  实际 owner: $PID"
if [ "$PID" = "ddos" ]; then echo "  PASS"; PASS=$((PASS+1)); else echo "  FAIL"; FAIL=$((FAIL+1)); fi

echo "--- 9. /health 应正常 ---"
if curl -skf --max-time 5 https://127.0.0.1:8443/health > /tmp/v134-h.json 2>/dev/null; then
    echo "  PASS: /health"
    PASS=$((PASS+1))
    cat /tmp/v134-h.json
    echo
else
    echo "  FAIL: /health"
    FAIL=$((FAIL+1))
fi

echo "--- 10. enroll-command 应返回 URL ---"
SECRET="regression-v134-secret-32chars-abcdef"
ADMIN="Authorization: Bearer $(echo -n "ddos-controller-auth" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $2}')"
ENROLL=$(curl -sk "https://127.0.0.1:8443/api/v1/nodes/enroll-command?type=http&node_id=attacker-http-v134" -H "$ADMIN")
SCRIPT_URL=$(echo "$ENROLL" | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['data']['command'])" 2>/dev/null)
if [ -n "$SCRIPT_URL" ]; then
    echo "  PASS: enroll-command"
    PASS=$((PASS+1))
else
    echo "  FAIL: enroll"
    FAIL=$((FAIL+1))
fi

echo "--- 11. 运行 node-install.sh ---"
# node-install.sh 要求 root; 写入文件, sudo 执行
echo "$SCRIPT_URL" > /tmp/v134-node-cmd.sh
chmod +x /tmp/v134-node-cmd.sh
bash /tmp/v134-node-cmd.sh 2>&1 | tail -8

echo "--- 12. attacker config.env 应为 600 ddos:ddos ---"
if [ -f /etc/ddos-attacker/config.env ]; then
    PERMS=$(stat -c "%a %U:%G" /etc/ddos-attacker/config.env)
    echo "  实际: $PERMS"
    if [ "$PERMS" = "600 ddos:ddos" ]; then echo "  PASS"; PASS=$((PASS+1)); else echo "  FAIL"; FAIL=$((FAIL+1)); fi
else
    echo "  FAIL: 文件不存在"
    FAIL=$((FAIL+1))
fi

echo "--- 13. attacker service unit 应为 640 ---"
if [ -f /etc/systemd/system/ddos-attacker.service ]; then
    PERMS=$(stat -c "%a" /etc/systemd/system/ddos-attacker.service)
    echo "  实际: $PERMS"
    if [ "$PERMS" = "640" ]; then echo "  PASS"; PASS=$((PASS+1)); else echo "  FAIL"; FAIL=$((FAIL+1)); fi
else
    echo "  FAIL: 文件不存在"
    FAIL=$((FAIL+1))
fi

echo "--- 14. attacker 进程应运行在 ddos 用户 (http 类型) ---"
sleep 3
PID=$(ps -ef | grep '/ddos-attacker' | grep -v grep | awk '{print $1}' | head -1)
echo "  实际 owner: $PID"
if [ "$PID" = "ddos" ]; then echo "  PASS"; PASS=$((PASS+1)); else echo "  FAIL"; FAIL=$((FAIL+1)); fi

echo "--- 15. 节点应成功注册并心跳 ---"
sleep 3
NODES=$(curl -sk "https://127.0.0.1:8443/api/v1/nodes" -H "$ADMIN" | python3 -c "import json,sys;d=json.load(sys.stdin)['data'];print(sum(1 for n in d if n['status']=='online'))" 2>/dev/null)
echo "  online=$NODES"
if [ "$NODES" -ge 1 ] 2>/dev/null; then
    echo "  PASS"
    PASS=$((PASS+1))
else
    echo "  FAIL"
    FAIL=$((FAIL+1))
fi

echo "--- 16. 隔离: nobody 用户不能读 config.env ---"
OTHER=$(runuser -u nobody -- cat /etc/ddos-controller/config.env 2>/dev/null)
if [ -z "$OTHER" ]; then
    echo "  PASS: config.env 不可被其他用户读取"
    PASS=$((PASS+1))
else
    echo "  FAIL: 泄漏: $OTHER"
    FAIL=$((FAIL+1))
fi

echo ""
echo "==============================="
echo "TOTAL: $PASS passed, $FAIL failed"
echo "==============================="
exit $FAIL
