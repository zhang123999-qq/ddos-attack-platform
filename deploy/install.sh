#!/bin/bash
# 一键部署脚本
# 用法: ./install.sh --role controller|attacker-http|attacker-raw [--host IP] [--user USER]

set -euo pipefail

# 默认配置
ROLE=""
TARGET_HOST=""
SSH_USER="${SSH_USER:-root}"
PROJECT_DIR="${PROJECT_DIR:-/opt/ddos-attack-platform}"
COMPOSE_FILE="docker-compose.yml"
ENV_FILE="config.env"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step() { echo -e "${BLUE}[STEP]${NC} $*"; }

usage() {
    cat <<EOF
Usage: $0 --role ROLE [--host HOST] [--user USER]

Roles:
  controller      Deploy controller node
  attacker-http   Deploy HTTP/Slowloris attacker node
  attacker-raw    Deploy SYN/UDP attacker node (requires privileged)

Options:
  --role ROLE       Required: controller | attacker-http | attacker-raw
  --host HOST       Target host IP (default: localhost)
  --user USER       SSH user (default: root)
  --project-dir DIR Remote project directory (default: /opt/ddos-attack-platform)
  -h, --help        Show this help

Examples:
  $0 --role controller
  $0 --role attacker-http --host 10.100.1.20
  $0 --role attacker-raw --host 10.100.1.21 --user admin
EOF
}

# 解析参数
while [[ $# -gt 0 ]]; do
    case $1 in
        --role)
            ROLE="$2"
            shift 2
            ;;
        --host)
            TARGET_HOST="$2"
            shift 2
            ;;
        --user)
            SSH_USER="$2"
            shift 2
            ;;
        --project-dir)
            PROJECT_DIR="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

if [[ -z "$ROLE" ]]; then
    log_error "Role is required (--role controller|attacker-http|attacker-raw)"
    usage
    exit 1
fi

# 设置目标主机
if [[ -z "$TARGET_HOST" || "$TARGET_HOST" == "localhost" || "$TARGET_HOST" == "127.0.0.1" ]]; then
    TARGET_HOST="localhost"
    SSH_CMD=""
    SCP_CMD="cp -r"
else
    SSH_CMD="ssh -o StrictHostKeyChecking=accept-new ${SSH_USER}@${TARGET_HOST}"
    SCP_CMD="scp -o StrictHostKeyChecking=accept-new -r"
fi

# 根据角色设置路径和文件
case $ROLE in
    controller)
        SOURCE_DIR="$(dirname "$0")/../controller"
        COMPOSE_FILE="docker-compose.yml"
        ;;
    attacker-http)
        SOURCE_DIR="$(dirname "$0")/../attacker"
        COMPOSE_FILE="docker-compose.yml"
        ;;
    attacker-raw)
        SOURCE_DIR="$(dirname "$0")/../attacker"
        COMPOSE_FILE="docker-compose.raw.yml"
        ;;
    *)
        log_error "Invalid role: $ROLE"
        exit 1
        ;;
esac

# 检查源目录
if [[ ! -d "$SOURCE_DIR" ]]; then
    log_error "Source directory not found: $SOURCE_DIR"
    exit 1
fi

# 检查证书
if [[ ! -d "$SOURCE_DIR/certs" ]]; then
    log_error "Certificates not found in $SOURCE_DIR/certs/"
    log_error "Run generate_certs.sh first"
    exit 1
fi

# 检查配置文件
if [[ ! -f "$SOURCE_DIR/config.env" ]]; then
    log_warn "config.env not found, copying from example"
    cp "$SOURCE_DIR/config.env.example" "$SOURCE_DIR/config.env"
    log_warn "Please edit $SOURCE_DIR/config.env before deploying"
    exit 1
fi

log_step "Deploying $ROLE to $TARGET_HOST:$PROJECT_DIR"

# 创建远程目录
if [[ "$TARGET_HOST" != "localhost" ]]; then
    $SSH_CMD "mkdir -p $PROJECT_DIR"
fi

# 同步文件
log_info "Syncing files..."
rsync -avz --delete \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.pytest_cache' \
    -e "ssh -o StrictHostKeyChecking=accept-new" \
    "$SOURCE_DIR/" "${SSH_USER}@${TARGET_HOST}:${PROJECT_DIR}/"

# 远程执行部署
if [[ "$TARGET_HOST" == "localhost" ]]; then
    DEPLOY_CMD="cd $PROJECT_DIR && docker-compose -f $COMPOSE_FILE up -d --build"
else
    DEPLOY_CMD="cd $PROJECT_DIR && docker-compose -f $COMPOSE_FILE up -d --build"
fi

log_info "Starting containers..."
if [[ "$TARGET_HOST" == "localhost" ]]; then
    eval "$DEPLOY_CMD"
else
    $SSH_CMD "$DEPLOY_CMD"
fi

# 等待健康检查
log_info "Waiting for health check..."
sleep 5

# 检查状态
if [[ "$TARGET_HOST" == "localhost" ]]; then
    docker-compose -f "$PROJECT_DIR/$COMPOSE_FILE" ps
else
    $SSH_CMD "cd $PROJECT_DIR && docker-compose -f $COMPOSE_FILE ps"
fi

log_info "Deployment complete!"
echo ""
echo "=== Access Info ==="
case $ROLE in
    controller)
        echo "Controller API:  https://${TARGET_HOST}:8443"
        echo "Web UI:          https://${TARGET_HOST}:8443"
        echo "WebSocket:       wss://${TARGET_HOST}:8443/ws/metrics?token=<TOKEN>"
        echo "Health:          https://${TARGET_HOST}:8443/health"
        ;;
    attacker-http|attacker-raw)
        echo "Attacker API:    http://${TARGET_HOST}:8080"
        echo "Health:          http://${TARGET_HOST}:8080/health"
        echo "Metrics:         http://${TARGET_HOST}:8080/metrics"
        ;;
esac