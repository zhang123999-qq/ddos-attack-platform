#!/bin/bash
# =============================================================================
# DDoS Attack Platform — 控制器一键安装
#
#   bash <(curl -Ls https://raw.githubusercontent.com/zhang123999-qq/ddos-attack-platform/master/deploy/controller-install.sh)
#
# 流程: 依赖 → 交互配置(端口/密钥/白名单) → 自签证书(SAN=本机IP) → 下载二进制
#       → systemd 启动 → 打印 WebUI 地址与添加节点指引
# 管理: ddos-controller            # 查看状态 (无参数)
#       ddos-controller {start|stop|restart|logs|update|uninstall}
# =============================================================================
set -euo pipefail

VERSION="1.3.0"
INSTALL_DIR="/opt/ddos-attack-platform/controller"
ETC_DIR="/etc/ddos-controller"
CERT_DIR="$INSTALL_DIR/certs"
SERVICE_NAME="ddos-controller"
BIN_NAME="ddos-controller"
CTL_PATH="/usr/local/bin/ddos-controller"
GITHUB_REPO="zhang123999-qq/ddos-attack-platform"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "${BLUE}[STEP]${NC} $*"; }

DO_UNINSTALL=0
[[ "${1:-}" == "uninstall" ]] && DO_UNINSTALL=1

# ---------- 卸载 ----------
if [[ $DO_UNINSTALL == 1 ]]; then
    log_step "Uninstalling controller"
    systemctl disable --now "$SERVICE_NAME" 2>/dev/null || true
    rm -f /etc/systemd/system/${SERVICE_NAME}.service
    systemctl daemon-reload 2>/dev/null || true
    rm -rf "$INSTALL_DIR" "$ETC_DIR" "$CTL_PATH"
    log_info "Controller removed."
    exit 0
fi

[[ $EUID -ne 0 ]] && { log_error "Must run as root"; exit 1; }
command -v systemctl >/dev/null || { log_error "systemd required"; exit 1; }

ARCH=$(uname -m)

# v1.4.0 (TD-1 升级兼容): 幂等写入 env 变量, 已存在则跳过
# 用法: ensure_env_var KEY VALUE FILE
ensure_env_var() {
    local key="$1"
    local value="$2"
    local file="$3"
    if grep -qE "^${key}=" "$file" 2>/dev/null; then
        return 0  # 已有, 跳过
    fi
    echo "${key}=${value}" >> "$file"
}
case "$ARCH" in
    x86_64) ARCH_TAG="x86_64" ;;
    aarch64|arm64) ARCH_TAG="arm64" ;;
    *) log_error "Unsupported arch: $ARCH"; exit 1 ;;
esac

log_step "Installing dependencies"
if command -v apt-get >/dev/null; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq && apt-get install -y -qq curl openssl ca-certificates tar python3 >/dev/null
elif command -v yum >/dev/null; then
    yum install -y -q curl openssl ca-certificates tar python3 >/dev/null
elif command -v dnf >/dev/null; then
    dnf install -y -q curl openssl ca-certificates tar python3 >/dev/null
fi

# ---------- 检测主 IP ----------
PRIMARY_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' | head -1)
[[ -z "$PRIMARY_IP" ]] && PRIMARY_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[[ -z "$PRIMARY_IP" ]] && PRIMARY_IP="127.0.0.1"

# ---------- 交互式配置 ----------
echo -e "${BLUE}==============================================${NC}"
echo -e "${BLUE}  DDoS Attack Platform — Controller Setup     ${NC}"
echo -e "${BLUE}  仅限授权内网红队教学演练使用                  ${NC}"
echo -e "${BLUE}==============================================${NC}"
read -rp "WebUI/API 监听端口 [8443]: " INPUT_PORT
CONTROLLER_PORT="${INPUT_PORT:-8443}"
# v1.3.0 方案A: 目标不限 — 不再询问白名单网段
read -rp "SHARED_SECRET [留空自动生成 64 位hex]: " INPUT_SECRET
if [[ -z "$INPUT_SECRET" ]]; then
    SHARED_SECRET=$(openssl rand -hex 32)
    log_info "generated random secret"
else
    SHARED_SECRET="$INPUT_SECRET"
fi
if [[ ${#SHARED_SECRET} -lt 32 ]]; then
    log_error "SHARED_SECRET must be >= 32 chars"; exit 1
fi

mkdir -p "$INSTALL_DIR" "$ETC_DIR" "$CERT_DIR"

# ---------- 自签证书 (SAN = 本机IP/localhost, 节点可真实校验) ----------
if [[ ! -f "$CERT_DIR/controller-cert.pem" ]]; then
    log_step "Generating self-signed certificate (SAN: $PRIMARY_IP)"
    openssl req -x509 -newkey rsa:2048 -sha256 -days 730 -nodes \
        -keyout "$CERT_DIR/controller-key.pem" \
        -out "$CERT_DIR/controller-cert.pem" \
        -subj "/CN=ddos-controller" \
        -addext "subjectAltName=DNS:localhost,DNS:$(hostname),IP:127.0.0.1,IP:$PRIMARY_IP" \
        >/dev/null 2>&1
    cp "$CERT_DIR/controller-cert.pem" "$CERT_DIR/ca-cert.pem"   # 自签场景 CA=自身
    chmod 600 "$CERT_DIR/controller-key.pem"
    log_info "certificate ready"
else
    log_info "existing certificate found, reusing"
fi

# ---------- 二进制下载 ----------
log_step "Downloading controller binary (arch=$ARCH_TAG)"
TARBALL="ddos-controller-linux-${ARCH_TAG}.tar.gz"
DL_OK=0
for URL in \
    "https://github.com/${GITHUB_REPO}/releases/latest/download/$TARBALL" \
    "https://github.com/${GITHUB_REPO}/releases/download/v1.2.0/$TARBALL"; do
    log_info "try: $URL"
    if curl -Lfs --max-time 300 -o "/tmp/$TARBALL" "$URL"; then DL_OK=1; break; fi
done
if [[ $DL_OK != 1 ]]; then
    log_warn "Binary download failed — falling back to Docker deployment"
    if command -v docker >/dev/null; then
        mkdir -p "$(dirname "$INSTALL_DIR")/artifacts"
        cat > "$INSTALL_DIR/docker-compose.yml" <<YML
services:
  controller:
    image: ghcr.io/zhang123999-qq/ddos-attack-platform/controller:main
    container_name: ddos-controller
    restart: unless-stopped
    ports:
      - "${CONTROLLER_PORT}:8443"
    environment:
      - SHARED_SECRET=${SHARED_SECRET}
      - REQUIRE_SHARED_SECRET=true
      - AUDIT_FILE_ENABLED=false
      - CONTROLLER_PORT=8443
    volumes:
      - ./certs:/certs
      - ../artifacts:/app/artifacts
      - ./node-install.sh:/app/deploy/node-install.sh:ro
YML
        # 提供安装脚本给容器分发
        SCRIPT_SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
        NODE_SCRIPT_URL="https://raw.githubusercontent.com/${GITHUB_REPO}/master/deploy/node-install.sh"
        curl -Lfs --max-time 60 -o "$INSTALL_DIR/node-install.sh" "$NODE_SCRIPT_URL" \
            || log_warn "node-install.sh fetch failed; /install.sh 分发不可用"
        cd "$INSTALL_DIR" && docker compose up -d
        DL_OK=1; DOCKER_MODE=1
    else
        log_error "No binary source reachable and docker missing. Install docker or retry."
        exit 1
    fi
fi

if [[ -z "${DOCKER_MODE:-}" ]]; then
    tar -xzf "/tmp/$TARBALL" -C "$INSTALL_DIR"
    chmod +x "$INSTALL_DIR/$BIN_NAME" 2>/dev/null || true
    rm -f "/tmp/$TARBALL"
fi

# ---------- 修复 F2: 创建专用服务用户, 避免 systemd User=ddos 回退 root ----------
# 与 install-service.sh 行为一致: 不存在则创建无登录权限的 ddos 系统用户
SERVICE_USER="ddos"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    log_info "creating service user: $SERVICE_USER"
    useradd -r -s /usr/sbin/nologin -m -d /nonexistent "$SERVICE_USER" 2>/dev/null || true
fi

# 修复 F2+F3: 修正安装目录和配置的所有者与权限
# GHA tarball 会保留 build UID (1001), 这里 chown 到 ddos 用户
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR" "$ETC_DIR" 2>/dev/null || true
# 密钥文件必须是 600 (其他用户不可读); 安装目录 750 (其他用户不可遍历)
chmod 750 "$INSTALL_DIR" "$ETC_DIR" 2>/dev/null || true
chmod 600 "$ETC_DIR/config.env" 2>/dev/null || true

# ---------- 分发物: 节点安装脚本 (BUG-5: 二进制包不含源码树, /install.sh 端点需要它) ----------
# 控制器按 INSTALL_SCRIPT_PATH env → 仓库 deploy/ → /app/deploy/ → 二进制同目录 顺序查找
if [[ -z "${DOCKER_MODE:-}" ]]; then
    NODE_SCRIPT_DST="$INSTALL_DIR/node-install.sh"
    if curl -Lfs --max-time 60 --retry 2 -o "$NODE_SCRIPT_DST" \
        "https://raw.githubusercontent.com/${GITHUB_REPO}/master/deploy/node-install.sh"; then
        chmod +x "$NODE_SCRIPT_DST"
        echo "[OK] node-install.sh staged — WebUI /install.sh 端点可用"
    else
        echo "[WARN] node-install.sh fetch failed — WebUI 生成的节点命令将回退 GitHub 直连 (可忽略)"
    fi
fi

# ---------- 写配置 ----------
cat > "$ETC_DIR/config.env" <<ENV
CONTROLLER_HOST=0.0.0.0
CONTROLLER_PORT=8443
SHARED_SECRET=${SHARED_SECRET}
REQUIRE_SHARED_SECRET=true
ENABLE_WEB_UI=true
AUDIT_FILE_ENABLED=false
TLS_CERT_FILE=$CERT_DIR/controller-cert.pem
TLS_KEY_FILE=$CERT_DIR/controller-key.pem
TLS_CA_FILE=$CERT_DIR/ca-cert.pem
LOG_LEVEL=info
# v1.4.0 (TD-1 修复): Controller→Node 强制 HTTPS, CA 复用 Controller CA
# v1.4.1-hotfix (REG-3 配套): Node 端 NODE_USE_TLS=false (REG-2, 因 enroll 不签发证书),
# Controller 需允许明文 HTTP, 配合通信
# v1.5.0 将引入 enroll 阶段证书签发, 届时恢复 NODE_PLAIN_HTTP_BANNED=true
NODE_TLS_CA_FILE=$CERT_DIR/ca-cert.pem
NODE_TLS_CERT_FILE=$CERT_DIR/controller-cert.pem
NODE_TLS_KEY_FILE=$CERT_DIR/controller-key.pem
NODE_INSECURE_PLAIN_HTTP=true
NODE_PLAIN_HTTP_BANNED=false
ENV
chmod 600 "$ETC_DIR/config.env"
# 修复 F3: 配置文件 owner 也必须是 ddos (cat > 是以 root 写, 需显式 chown)
chown "$SERVICE_USER:$SERVICE_USER" "$ETC_DIR/config.env" 2>/dev/null || true

# ---------- systemd ----------
if [[ -z "${DOCKER_MODE:-}" ]]; then
cat > /etc/systemd/system/${SERVICE_NAME}.service <<UNIT
[Unit]
Description=DDoS Attack Platform - Controller
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/$BIN_NAME
ExecStop=/bin/kill -SIGTERM \$MAINPID
Restart=always
RestartSec=10
EnvironmentFile=-$ETC_DIR/config.env
Environment=TLS_CERT_FILE=$CERT_DIR/controller-cert.pem
Environment=TLS_KEY_FILE=$CERT_DIR/controller-key.pem
Environment=TLS_CA_FILE=$CERT_DIR/ca-cert.pem
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=$INSTALL_DIR $ETC_DIR
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
StandardOutput=journal
StandardError=journal
SyslogIdentifier=$SERVICE_NAME
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
UNIT

cat > "$CTL_PATH" <<'CTL'
#!/bin/bash
# DDoS Attack Platform — 控制器快捷管理指令
# 简化: 无参数 = 智能状态; update = 升级到最新 Release
INSTALL_DIR="/opt/ddos-attack-platform/controller"
ETC_DIR="/etc/ddos-controller"
SERVICE_NAME="ddos-controller"
GITHUB_REPO="zhang123999-qq/ddos-attack-platform"

# BUG-1: 变更类操作需要 root — root 直行; sudo 免密缓存命中即代执行;
# 否则明确提示正确命令 (原实现直接 systemctl 报 Access denied)
SUDO=""
[[ $EUID -ne 0 ]] && SUDO="sudo"
require_root() {
    if [[ $EUID -ne 0 ]] && ! sudo -n true 2>/dev/null; then
        echo "[AUTH] 此操作需要 root 权限, 请执行: sudo ddos-controller ${1:-<op>}" >&2
        exit 1
    fi
}

get_port() {
    local port
    port=$(grep -E '^CONTROLLER_PORT=' "$ETC_DIR/config.env" 2>/dev/null | cut -d= -f2 | tr -d '\r\n ')
    echo "${port:-8443}"
}

do_update() {
    echo "[UPDATE] Checking latest release..."
    local arch tag tarball tmp latest current
    case "$(uname -m)" in
        x86_64) tag="x86_64" ;;
        aarch64|arm64) tag="arm64" ;;
        *) echo "[ERROR] Unsupported arch: $(uname -m)"; return 1 ;;
    esac
    tarball="ddos-controller-linux-${tag}.tar.gz"
    tmp=$(mktemp -d)
    if ! curl -Lfs --max-time 900 --retry 3 --retry-delay 5 --connect-timeout 15 -o "$tmp/$tarball" \
        "https://github.com/${GITHUB_REPO}/releases/latest/download/$tarball"; then
        echo "[ERROR] Download failed — check network / github reachability"
        rm -rf "$tmp"; return 1
    fi
    # 提取版本号对比 (health 接口 version 字段), 相同则跳过
    systemctl start "$SERVICE_NAME" 2>/dev/null || true
    current=$(curl -sk --max-time 3 "https://127.0.0.1:$(get_port)/api/v1/controller-info" 2>/dev/null \
        | grep -oP '"version":\s*"\K[^"]+' || true)
    if [[ -n "$current" ]] && bash "$0" status >/dev/null 2>&1; then :; fi

    systemctl stop "$SERVICE_NAME"
    if ! tar -xzf "$tmp/$tarball" -C "$INSTALL_DIR"; then
        echo "[ERROR] Extract failed — restarting old version"
        systemctl start "$SERVICE_NAME"; rm -rf "$tmp"; return 1
    fi
    chmod +x "$INSTALL_DIR/ddos-controller"
    # 修复 F2+F3: 升级路径同样修正 owner 和 config.env 权限
    # GHA tarball 中文件可能属 build UID (如 1001:1001), 需 chown 回 ddos
    if id -u ddos >/dev/null 2>&1; then
        chown -R ddos:ddos "$INSTALL_DIR" "$ETC_DIR" 2>/dev/null || true
    fi
    chmod 750 "$INSTALL_DIR" "$ETC_DIR" 2>/dev/null || true
    chmod 600 "$ETC_DIR/config.env" 2>/dev/null || true

    # v1.4.0 (TD-1 升级路径): 旧部署 config.env 缺 NODE_TLS_* 变量, 直接 do_update
    # 升级 v1.4.0 二进制后, NodeCommander 默认 fail-closed 启动崩溃。
    # 补写缺失的 NODE_TLS_* 变量, 不覆盖已有值 (幂等)
    if [[ -f "$ETC_DIR/config.env" ]]; then
        local cert_dir="$INSTALL_DIR/certs"
        [[ -d "$cert_dir" ]] || cert_dir="/opt/ddos-attack-platform/controller/certs"
        ensure_env_var "NODE_TLS_CA_FILE" "$cert_dir/ca-cert.pem" "$ETC_DIR/config.env"
        ensure_env_var "NODE_TLS_CERT_FILE" "$cert_dir/controller-cert.pem" "$ETC_DIR/config.env"
        ensure_env_var "NODE_TLS_KEY_FILE" "$cert_dir/controller-key.pem" "$ETC_DIR/config.env"
        # v1.4.1-hotfix (REG-3): Node 端 NODE_USE_TLS=false (REG-2 设计修订),
        # 配套 Controller 需允许明文 HTTP, 否则 fail-closed 会拒绝联系 Node
        ensure_env_var "NODE_INSECURE_PLAIN_HTTP" "true" "$ETC_DIR/config.env"
        ensure_env_var "NODE_PLAIN_HTTP_BANNED" "false" "$ETC_DIR/config.env"
        # 重新锁定权限 (ensure_env_var append 后权限可能错)
        chown ddos:ddos "$ETC_DIR/config.env" 2>/dev/null || true
        chmod 600 "$ETC_DIR/config.env" 2>/dev/null || true
        echo "[UPDATE] NODE_TLS_* 配置已对齐 (TD-1 升级兼容 + REG-3 允许明文)"
    fi

    # 节点安装脚本 (install.sh 端点) — 同步刷新到最新 master
    if curl -Lfs --max-time 60 --retry 2 -o "$INSTALL_DIR/node-install.sh" \
        "https://raw.githubusercontent.com/${GITHUB_REPO}/master/deploy/node-install.sh" 2>/dev/null; then
        chmod +x "$INSTALL_DIR/node-install.sh"
        chown ddos:ddos "$INSTALL_DIR/node-install.sh" 2>/dev/null || true
    fi
    rm -rf "$tmp"
    systemctl start "$SERVICE_NAME"

    local ok=0
    for i in $(seq 1 20); do
        curl -skf --max-time 2 "https://127.0.0.1:$(get_port)/health" >/dev/null 2>&1 && { ok=1; break; }
        sleep 1
    done
    if [[ $ok == 1 ]]; then
        local newver
        newver=$(curl -sk --max-time 3 "https://127.0.0.1:$(get_port)/api/v1/controller-info" 2>/dev/null \
            | grep -oP '"version":\s*"\K[^"]+' || echo '?')
        echo "[OK] Updated. Running version: ${newver:-unknown}"
    else
        echo "[WARN] Service restarted but health not confirmed yet — check: ddos-controller logs"
    fi
}

show_status() {
    local state port pid
    state=$(systemctl is-active "$SERVICE_NAME" 2>/dev/null)
    port=$(get_port)
    case "$state" in
        active)
            pid=$(systemctl show "$SERVICE_NAME" -p MainPID --value)
            local ver nodes
            ver=$(curl -sk --max-time 3 "https://127.0.0.1:${port}/api/v1/controller-info" 2>/dev/null \
                | grep -oP '"version":\s*"\K[^"]+' || echo '?')
            nodes=$(curl -sk --max-time 3 "https://127.0.0.1:${port}/api/v1/nodes" 2>/dev/null \
                | grep -o '"status": *"online"' | wc -l | tr -d ' ')
            echo "controller : RUNNING (pid $pid, v${ver})"
            echo "webui      : https://127.0.0.1:${port}   (LAN: https://$(hostname -I 2>/dev/null | awk '{print $1}'):${port})"
            echo "nodes      : ${nodes} online"
            ;;
        *)
            echo "controller : STOPPED (${state})"
            echo "start it   : sudo ddos-controller restart"
            ;;
    esac
}

case "${1:-}" in
    ""|status)  show_status ;;                       # 简化: 无参数直接看状态
    s)          show_status ;;
    start)      require_root "$1";  $SUDO systemctl start "$SERVICE_NAME";  show_status ;;
    stop)       require_root "$1";  $SUDO systemctl stop "$SERVICE_NAME";   echo "controller stopped" ;;
    r|restart)  require_root "$1";  $SUDO systemctl restart "$SERVICE_NAME"; sleep 2; show_status ;;
    logs)       journalctl -u "$SERVICE_NAME" -f --no-pager -n 100 ;;
    l)          journalctl -u "$SERVICE_NAME" -n 50 --no-pager ;;   # 最近日志 (非跟随)
    update|u)   require_root "$1";  $SUDO bash "$0" --do-update-internal ;;
    uninstall)  require_root "$1"
                if [[ $EUID -ne 0 ]]; then
                    $SUDO systemctl disable --now "$SERVICE_NAME" 2>/dev/null
                    $SUDO rm -f /etc/systemd/system/${SERVICE_NAME}.service; $SUDO systemctl daemon-reload
                    $SUDO rm -rf "$INSTALL_DIR" "$ETC_DIR" "/usr/local/bin/$(basename "$0")"
                else
                    systemctl disable --now "$SERVICE_NAME" 2>/dev/null
                    rm -f /etc/systemd/system/${SERVICE_NAME}.service; systemctl daemon-reload
                    rm -rf "$INSTALL_DIR" "$ETC_DIR" "/usr/local/bin/$(basename "$0")"
                fi
                echo "uninstalled" ;;
    --do-update-internal) do_update ;;
    *) echo "Usage: ddos-controller [status|start|stop|restart|logs|update|uninstall]"
       echo "  (无参数=status, s=status, r=restart, l=最近日志, u=update)" ;;
esac
CTL
chmod +x "$CTL_PATH"
# 修复 F4: 限制 service unit 文件权限为 640 (其他用户不可读 systemd 环境变量)
chmod 640 "/etc/systemd/system/${SERVICE_NAME}.service" 2>/dev/null || true
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"
fi

# ---------- 健康自检 + 完成输出 ----------
log_step "Waiting for controller health"
HEALTHY=0
for i in $(seq 1 30); do
    if curl -skf --max-time 2 "https://127.0.0.1:${CONTROLLER_PORT}/health" >/dev/null 2>&1; then HEALTHY=1; break; fi
    sleep 1
done

echo ""
if [[ $HEALTHY == 1 ]]; then
    FINGERPRINT=$(openssl x509 -in "$CERT_DIR/controller-cert.pem" -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2 | tr -d ':' | tr 'A-F' 'a-f')
    log_info "=============================================="
    log_info " Controller is UP!"
    log_info " WebUI:   https://${PRIMARY_IP}:${CONTROLLER_PORT}"
    log_info " Token:   hmac_sha256(SHARED_SECRET,'ddos-controller-auth')"
    log_info "          密钥保存在 $ETC_DIR/config.env"
    log_info ""
    log_info " 下一步: 浏览器打开 WebUI → 「节点管理」→「➕ 添加节点」"
    log_info "         复制生成的命令到攻击机执行即可自动上线"
    if [[ -n "$FINGERPRINT" ]]; then
    log_info " TLS指纹: $FINGERPRINT"
    fi
    log_info " 防火墙放行: ufw allow ${CONTROLLER_PORT}/tcp   # 或对应云安全组"
    log_info " 管理: ddos-controller            # 查看状态"
    log_info "       ddos-controller {logs|restart|update|uninstall}"
    log_info "=============================================="
else
    log_error "Health check failed after 30s:"
    [[ -z "${DOCKER_MODE:-}" ]] && journalctl -u "$SERVICE_NAME" -n 20 --no-pager || docker logs ddos-controller --tail 20
    exit 1
fi
