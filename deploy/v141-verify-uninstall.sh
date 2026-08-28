#!/bin/bash
# 完整卸载验证 — 确保 uninstall 路径可用
set +e
echo root | sudo -S -v 2>&1 | head -1

PASS=0
FAIL=0

# --- 1. 卸载前快照 ---
echo "=== 1. 卸载前快照 ==="
echo "  进程: $(echo root | sudo -S ps -ef 2>&1 | grep -cE 'ddos-controller|ddos-attacker' | head -1)"
echo "  config.env 文件数: $(echo root | sudo -S ls /etc/ddos-controller/config.env /etc/ddos-attacker/config.env 2>&1 | wc -l)"
echo "  install dir: $(echo root | sudo -S ls -d /opt/ddos-attack-platform 2>&1)"

# --- 2. 卸载 controller ---
echo ""
echo "=== 2. ddos-controller uninstall ==="
echo root | sudo -S ddos-controller uninstall 2>&1
echo "  $?"

# --- 3. 卸载 attacker ---
echo ""
echo "=== 3. ddos-node uninstall ==="
echo root | sudo -S ddos-node uninstall 2>&1
echo "  $?"

# --- 4. 清理 ddos 用户 ---
echo ""
echo "=== 4. 清理 ddos 用户 ==="
echo root | sudo -S userdel -r ddos 2>&1
echo "  $?"

# --- 5. 验证完全清理 ---
echo ""
echo "=== 5. 验证完全清理 ==="
echo "--- 5a. /opt/ddos-attack-platform 残留 ---"
echo root | sudo -S ls -la /opt/ddos-attack-platform 2>&1
echo "--- 5b. /etc/ddos-* 残留 ---"
echo root | sudo -S ls -la /etc/ddos-controller /etc/ddos-attacker 2>&1
echo "--- 5c. systemd 残留 ---"
echo root | sudo -S ls /etc/systemd/system/ddos-*.service 2>&1
echo "--- 5d. ddos 用户 ---"
echo root | sudo -S id ddos 2>&1
echo "--- 5e. /usr/local/bin 快捷指令 ---"
echo root | sudo -S ls /usr/local/bin/ddos-* 2>&1
echo "--- 5f. 进程残留 ---"
echo root | sudo -S ps -ef 2>&1 | grep -E 'ddos|app.main' | grep -v grep

# --- 6. 端口 8443 / 8080 释放 ---
echo ""
echo "=== 6. 端口释放 ==="
echo "  8443: $(echo root | sudo -S ss -tln 2>&1 | grep -c ':8443') listener"
echo "  8080: $(echo root | sudo -S ss -tln 2>&1 | grep -c ':8080') listener"
if [[ $(echo root | sudo -S ss -tln 2>&1 | grep -c ':8443') -eq 0 && $(echo root | sudo -S ss -tln 2>&1 | grep -c ':8080') -eq 0 ]]; then
    echo "  PASS: 端口全部释放"
    PASS=$((PASS+1))
else
    echo "  FAIL: 端口仍占用"
    FAIL=$((FAIL+1))
fi

echo ""
echo "=========================="
echo "卸载完成, 验证项: $PASS passed, $FAIL failed"
