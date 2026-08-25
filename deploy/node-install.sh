#!/bin/bash
# =============================================================================
# DDoS Attack Platform — 攻击节点一键安装器 (拉取式自助安装)
#
# 由控制器 WebUI「添加节点」生成完整命令, 在目标 Linux 服务器以 root 粘贴执行:
#   bash <(curl -Lsk https://<CONTROLLER>:8443/install.sh) \
#       -e https://<CONTROLLER>:8443 -t <enroll_token> --id attacker-http-01 --type http
#
# 流程: 依赖检查 → 拉取 CA+指纹钉扎 → enroll 换取配置 → 双通道下载二进制
#       → 写 config.env → systemd 启动 → 健康自检 (自动注册上线)
#
# 管理: ddos-node            # 查看状态 (无参数)
#       ddos-node {start|stop|restart|logs|update|uninstall}
# =============================================================================
set -euo pipefail

VERSION="1.1.0"
INSTALL_DIR="/opt/ddos-attack-platform/attacker"
ETC_DIR="/etc/ddos-attacker"
SERVICE_NAME="ddos-attacker"
BIN_NAME="ddos-attacker"
CTL_PATH="/usr/local/bin/ddos-node"
GITHUB_REPO="zhang123999-qq/ddos-attack-platform"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "${BLUE}[STEP]${NC} $*"; }

# ---------- 默认参数 ----------
ENDPOINT=""            # -e 控制器地址 (可被脚本内嵌 __CONTROLLER_URL__ 预填)
ENROLL_TOKEN=""        # -t enroll token (必填)
NODE_ID=""             # --id 节点ID (必填)
NODE_TYPE="http"       # --type http|raw
FINGERPRINT=""         # --fingerprint 控制器证书 SHA-256 指纹 (去冒号小写)
RELEASE_VERSION=""     # --version 指定发布版本 (默认 latest)
DO_UNINSTALL=0

usage() {
    cat <<EOF
Usage: $(basename "$0") -t ENROLL_TOKEN --id NODE_ID [options]

Required:
  -t TOKEN        Enroll token from controller WebUI (bound to --id, ~1h valid)
  --id NODE_ID    Unique node id, e.g. attacker-http-02

Options:
  -e URL          Controller base URL (auto-filled when served by controller)
  --type TYPE     http | raw  (raw needs CAP_NET_RAW for SYN/UDP)  default: http
  --fingerprint FP Expected controller cert sha256 fingerprint (hex, no colons)
  --version VER   Pin release version, e.g. v1.2.0  (default: latest)
  uninstall       Remove service and files completely
  -h              Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case $1 in
        -e) ENDPOINT="$2"; shift 2 ;;
        -t) ENROLL_TOKEN="$2"; shift 2 ;;
        --id) NODE_ID="$2"; shift 2 ;;
        --type) NODE_TYPE="$2"; shift 2 ;;
        --fingerprint) FINGERPRINT="$2"; shift 2 ;;
        --version) RELEASE_VERSION="$2"; shift 2 ;;
        uninstall) DO_UNINSTALL=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) log_error "Unknown option: $1"; usage; exit 1 ;;
    esac
done

# ---------- 卸载 ----------
if [[ "$DO_UNINSTALL" == "1" ]]; then
    log_step "Uninstalling $SERVICE_NAME"
    systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
    rm -f /etc/systemd/system/${SERVICE_NAME}.service
    systemctl daemon-reload 2>/dev/null || true
    rm -rf "$INSTALL_DIR" "$ETC_DIR" "$CTL_PATH"
    log_info "Removed. Reboot not required."
    exit 0
fi

if [[ -z "$ENROLL_TOKEN" || -z "$NODE_ID" ]]; then
    log_error "-t and --id are required"
    usage
    exit 1
fi
[[ "$NODE_TYPE" != "http" && "$NODE_TYPE" != "raw" ]] && { log_error "--type must be http|raw"; exit 1; }

# ---------- 环境预检 ----------
[[ $EUID -ne 0 ]] && { log_error "Must run as root"; exit 1; }
command -v systemctl >/dev/null || { log_error "systemd required"; exit 1; }

ARCH=$(uname -m)
case "$ARCH" in
    x86_64)  ARCH_TAG="x86_64" ;;
    aarch64|arm64) ARCH_TAG="arm64" ;;
    *) log_error "Unsupported arch: $ARCH (only x86_64/arm64)"; exit 1 ;;
esac

log_step "Installing dependencies"
if command -v apt-get >/dev/null; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq && apt-get install -y -qq curl openssl ca-certificates tar python3 >/dev/null
elif command -v yum >/dev/null; then
    yum install -y -q curl openssl ca-certificates tar python3 >/dev/null
elif command -v dnf >/dev/null; then
    dnf install -y -q curl openssl ca-certificates tar python3 >/dev/null
else
    log_warn "Unknown package manager; ensure curl/openssl/tar/python3 exist"
fi
for c in curl openssl tar python3; do command -v $c >/dev/null || { log_error "missing: $c"; exit 1; }; done

mkdir -p "$ETC_DIR"

# ---------- 控制器元信息 + CA 引导 ----------
[[ -z "$ENDPOINT" ]] && { log_error "-e endpoint required"; exit 1; }
ENDPOINT="${ENDPOINT%/}"
HOSTPORT=$(python3 -c "from urllib.parse import urlparse;u=urlparse('$ENDPOINT');print(u.netloc)")
TMP_CA="$ETC_DIR/ca-cert.pem"

log_step "Fetching controller info & CA certificate"
INFO_JSON=$(curl -Lsk --max-time 15 "$ENDPOINT/api/v1/controller-info")
DECLARED_FP=$(echo "$INFO_JSON" | python3 -c "import sys,json;print(json.load(sys.stdin).get('tls_fingerprint',''))")

curl -Lsk --max-time 15 -o "$TMP_CA" "$ENDPOINT/artifacts/ca-cert.pem"
[[ -s "$TMP_CA" ]] || { log_error "Failed to download CA cert"; exit 1; }

# 指纹交叉校验: 实际握手证书 vs 控制器自报指纹 (防降级/错配)
SERVED_FP=$(echo | timeout 10 openssl s_client -connect "$HOSTPORT" -servername "$(cut -d: -f1 <<<"$HOSTPORT")" 2>/dev/null \
    | openssl x509 -fingerprint -sha256 -noout 2>/dev/null | cut -d= -f2 | tr -d ':' | tr 'A-F' 'a-f' || true)
DECLARED_NORM=$(echo "$DECLARED_FP" | tr -d ':' | tr 'A-F' 'a-f')
if [[ -n "$DECLARED_NORM" && -n "$SERVED_FP" ]]; then
    if [[ "$DECLARED_NORM" != "$SERVED_FP" ]]; then
        log_error "TLS fingerprint MISMATCH! served=$SERVED_FP declared=$DECLARED_NORM"
        log_error "Possible MITM or stale deployment. Aborting."
        exit 1
    fi
    log_info "TLS fingerprint verified"
else
    log_warn "Fingerprint check unavailable; continuing with CA pinning only"
fi

# 命令行显式指纹 > 自报指纹 双保险
if [[ -n "$FINGERPRINT" ]]; then
    FN=$(echo "$FINGERPRINT" | tr -d ':' | tr 'A-F' 'a-f')
    [[ -n "$SERVED_FP" && "$FN" != "$SERVED_FP" ]] && {
        log_error "Explicit --fingerprint mismatch! aborting."; exit 1; }
fi

# ---------- Enroll: 用一次性 token 换取运行配置 ----------
log_step "Enrolling node '$NODE_ID'"
ENROLL_CODE=0
ENROLL_JSON=$(curl -Ls --cacert "$TMP_CA" --max-time 20 \
    -H "Content-Type: application/json" \
    -d "{\"node_id\":\"$NODE_ID\",\"enroll_token\":\"$ENROLL_TOKEN\"}" \
    "$ENDPOINT/api/v1/nodes/enroll") || ENROLL_CODE=$?
if [[ $ENROLL_CODE -ne 0 ]]; then
    log_error "Enroll request failed (network/TLS), curl exit=$ENROLL_CODE"
    exit 1
fi

SHARED_SECRET=$(echo "$ENROLL_JSON" | python3 -c "
import sys,json
try: print(json.load(sys.stdin)['shared_secret'])
except Exception: print('')" 2>/dev/null)
ALLOWED_CIDRS=$(echo "$ENROLL_JSON" | python3 -c "
import sys,json
try: print(json.load(sys.stdin).get('allowed_target_cidrs','127.0.0.0/8'))
except Exception: print('127.0.0.0/8')" 2>/dev/null)

if [[ -z "$SHARED_SECRET" ]]; then
    log_error "Enroll rejected. Raw response:"
    echo "$ENROLL_JSON" | head -5
    exit 1
fi
log_info "Enroll OK (secret acquired over verified TLS)"

# ---------- 二进制下载: 控制器制品优先, GitHub Releases 回退 ----------
log_step "Downloading binary (arch=$ARCH_TAG)"
TARBALL="ddos-attacker-linux-${ARCH_TAG}.tar.gz"
DL_OK=0
# 下载必须与 enroll 同源信任: 自签部署下 ENDPOINT 证书由控制器 CA 签发,
# 不带 --cacert 将证书验证失败 (GitHub 回退源不受影响, curl 对 https 自动忽略 --cacert 的额外约束)
for URL in "$ENDPOINT/artifacts/$TARBALL" \
           "https://github.com/${GITHUB_REPO}/releases/latest/download/$TARBALL" \
           ${RELEASE_VERSION:+"https://github.com/${GITHUB_REPO}/releases/download/${RELEASE_VERSION}/$TARBALL"}; do
    log_info "try: $URL"
    if curl -Lfs --cacert "$TMP_CA" --max-time 300 -o "/tmp/$TARBALL" "$URL"; then DL_OK=1; break; fi
done
[[ $DL_OK == 1 ]] || { log_error "All download sources failed"; exit 1; }

mkdir -p "$INSTALL_DIR"
tar -xzf "/tmp/$TARBALL" -C "$INSTALL_DIR"
chmod +x "$INSTALL_DIR/$BIN_NAME" 2>/dev/null || true
rm -f "/tmp/$TARBALL"

# ---------- 写配置 ----------
cat > "$ETC_DIR/config.env" <<ENV
NODE_ID=${NODE_ID}
NODE_TYPE=${NODE_TYPE}
SHARED_SECRET=${SHARED_SECRET}
CONTROLLER_URL=${ENDPOINT}
CONTROLLER_CA_CERT=${TMP_CA}
ALLOWED_TARGET_CIDRS=${ALLOWED_CIDRS}
ATTACK_TYPES=$( [[ "$NODE_TYPE" == "raw" ]] && echo "syn_flood,udp_flood,udp_reflection" || echo "http_flood,slowloris" )
LOG_LEVEL=info
ENV
chmod 600 "$ETC_DIR/config.env"

# ---------- systemd 单元 (复用仓库加固基线) ----------
if [[ "$NODE_TYPE" == "raw" ]]; then
    CAP_LINES="AmbientCapabilities=CAP_NET_RAW CAP_NET_ADMIN
CapabilityBoundingSet=CAP_NET_RAW CAP_NET_ADMIN
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_PACKET"
else
    CAP_LINES="RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX"
fi

cat > /etc/systemd/system/${SERVICE_NAME}.service <<UNIT
[Unit]
Description=DDoS Attack Platform - Attacker Node ($NODE_TYPE)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/$BIN_NAME
ExecStop=/bin/kill -SIGTERM \$MAINPID
Restart=always
RestartSec=10
EnvironmentFile=-$ETC_DIR/config.env
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=$INSTALL_DIR
$CAP_LINES
StandardOutput=journal
StandardError=journal
SyslogIdentifier=$SERVICE_NAME
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
UNIT

# ---------- 管理命令 ----------
cat > "$CTL_PATH" <<CTL
#!/bin/bash
# DDoS Attack Platform — 攻击节点快捷管理指令
SERVICE="ddos-attacker"
ETC="/etc/ddos-attacker"

show_status() {
    local state pid nid
    state=\$(systemctl is-active "\$SERVICE" 2>/dev/null)
    case "\$state" in
        active)
            pid=\$(systemctl show "\$SERVICE" -p MainPID --value)
            nid=\$(grep -E '^NODE_ID=' "$ETC/config.env" 2>/dev/null | cut -d= -f2 | tr -d '\r\n ')
            echo "node : RUNNING (pid \$pid, id=\${nid:-?})"
            echo "health: \$(curl -sf --max-time 2 http://127.0.0.1:\${NODE_PORT:-8080}/health >/dev/null 2>&1 && echo OK || echo FAIL)"
            ;;
        *) echo "node : STOPPED (\$state)   start: sudo ddos-node restart" ;;
    esac
}

case "\${1:-}" in
    ""|status)  show_status ;;
    s)          show_status ;;
    start)      systemctl start "\$SERVICE";  show_status ;;
    stop)       systemctl stop "\$SERVICE";   echo "node stopped" ;;
    r|restart)  systemctl restart "\$SERVICE"; sleep 2; show_status ;;
    logs)       journalctl -u "\$SERVICE" -f --no-pager -n 100 ;;
    l)          journalctl -u "\$SERVICE" -n 50 --no-pager ;;
    enable)     systemctl enable "\$SERVICE" ;;
    disable)    systemctl disable "\$SERVICE" ;;
    update)     echo "[UPDATE] Re-running installer with current enrollment config..."
                echo "[INFO] Node updates ship via controller enroll; re-run the WebUI-generated install command to upgrade." ;;
    uninstall)  systemctl disable --now "\$SERVICE" 2>/dev/null; rm -f /etc/systemd/system/\${SERVICE}.service; systemctl daemon-reload; rm -rf /opt/ddos-attack-platform/attacker "$ETC" "/usr/local/bin/\$(basename "\$0")"; echo "uninstalled" ;;
    *) echo "Usage: ddos-node [status|start|stop|restart|logs|update|uninstall]"
       echo "  (无参数=status, s=status, r=restart, l=最近日志)" ;;
esac
CTL
chmod +x "$CTL_PATH"

# ---------- 启动与健康自检 ----------
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

log_step "Waiting for node health & registration"
HEALTHY=0
for i in $(seq 1 30); do
    if curl -sf --max-time 2 http://127.0.0.1:8080/health >/dev/null 2>&1; then HEALTHY=1; break; fi
    sleep 1
done

echo ""
if [[ $HEALTHY == 1 ]]; then
    log_info "=============================================="
    log_info " Node '$NODE_ID' installed and healthy!"
    log_info " It has auto-registered to the controller."
    log_info " Check the WebUI nodes table to confirm."
    log_info " Manage: ddos-node            # 查看状态"
    log_info "         ddos-node {logs|restart|uninstall}"
    log_info "=============================================="
else
    log_error "Service started but health check failed after 30s. Recent logs:"
    journalctl -u "$SERVICE_NAME" -n 20 --no-pager || true
    exit 1
fi
