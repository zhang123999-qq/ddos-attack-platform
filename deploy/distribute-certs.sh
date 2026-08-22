#!/usr/bin/env bash
# =============================================================================
# distribute-certs.sh — 自动分发证书和配置到所有节点
#
# 功能:
#   1. 从 config.yaml 读取所有节点列表
#   2. 通过 scp 分发 CA + 节点专属证书到各自 certs/ 目录
#   3. 同时分发生成的 .env 配置文件
#
# 前置: generate-configs.sh 已运行, generate_certs.sh 已运行
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG_FILE="${SCRIPT_DIR}/config.yaml"
GENERATED_DIR="${ROOT_DIR}/generated"
CERTS_DIR="${ROOT_DIR}/certs"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[0;34m'; NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "${BLUE}[STEP]${NC} $*"; }

if [[ ! -f "$CONFIG_FILE" ]]; then
    log_error "$CONFIG_FILE not found"
    exit 1
fi

if [[ ! -f "$CERTS_DIR/ca-cert.pem" ]]; then
    log_error "CA certificate not found: $CERTS_DIR/ca-cert.pem"
    log_error "Run 'make certs' or './deploy/generate_certs.sh' first"
    exit 1
fi

if [[ ! -d "$GENERATED_DIR" ]] || ! ls "$GENERATED_DIR"/*.env &>/dev/null; then
    log_error "No generated configs found in $GENERATED_DIR/"
    log_error "Run './deploy/generate-configs.sh' first"
    exit 1
fi

log_step "=== Distributing certificates and configs ==="

# ========== 解析集群拓扑 ==========
eval "$(python3 -c "
import yaml, json

with open('$CONFIG_FILE') as f:
    cfg = yaml.safe_load(f)

entries = []

# Controller
ctrl = cfg['controller']
entries.append({
    'role': 'controller',
    'host': ctrl['host'],
    'ssh_user': ctrl.get('ssh', {}).get('user', 'root'),
    'ssh_port': ctrl.get('ssh', {}).get('port', 22),
    'deploy_method': ctrl['deploy_method'],
    'install_dir': ctrl.get('install_dir', '/opt/ddos-attack-platform/controller'),
})

# Attackers
for atk in cfg.get('attackers', []):
    entries.append({
        'role': 'attacker',
        'node_id': atk['node_id'],
        'host': atk['host'],
        'ssh_user': atk.get('ssh', {}).get('user', 'root'),
        'ssh_port': atk.get('ssh', {}).get('port', 22),
        'deploy_method': atk['deploy_method'],
        'install_dir': atk.get('install_dir', '/opt/ddos-attack-platform/attacker'),
    })

print(f'ENTRIES_COUNT={len(entries)}')
for i, e in enumerate(entries):
    for k, v in e.items():
        print(f'ENTRY_{i}_{k.upper()}={v}')
" 2>&1)"

# ========== 分发到每个节点 ==========
SUCCESS=0
FAILED=0

for ((i=0; i<ENTRIES_COUNT; i++)); do
    eval "ROLE=\${ENTRY_${i}_ROLE}"
    eval "HOST=\${ENTRY_${i}_HOST}"
    eval "SSH_USER=\${ENTRY_${i}_SSH_USER}"
    eval "SSH_PORT=\${ENTRY_${i}_SSH_PORT}"
    eval "DEPLOY_METHOD=\${ENTRY_${i}_DEPLOY_METHOD}"
    eval "INSTALL_DIR=\${ENTRY_${i}_INSTALL_DIR}"
    eval "NODE_ID=\${ENTRY_${i}_NODE_ID:-controller}"

    SSH_TARGET="${SSH_USER}@${HOST}"
    SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=10 -p ${SSH_PORT}"

    log_step "Distributing to $NODE_ID ($ROLE) at $HOST (deploy=$DEPLOY_METHOD)"

    # 测试 SSH 连通性
    if ! ssh $SSH_OPTS "$SSH_TARGET" "echo ok" &>/dev/null; then
        if [[ "$HOST" == "localhost" || "$HOST" == "127.0.0.1" ]]; then
            log_info "Local deployment — skipping SSH"
            IS_LOCAL=true
        else
            log_error "SSH connection failed: $SSH_TARGET — skipping"
            FAILED=$((FAILED+1))
            continue
        fi
    else
        IS_LOCAL=false
    fi

    # 创建远程目录
    REMOTE_CERTS="${INSTALL_DIR}/certs"
    if $IS_LOCAL; then
        mkdir -p "$REMOTE_CERTS"
    else
        ssh $SSH_OPTS "$SSH_TARGET" "mkdir -p $REMOTE_CERTS $INSTALL_DIR"
    fi

    # ---- 分发证书 ----
    if [[ "$ROLE" == "controller" ]]; then
        CERT_FILES=(
            "$CERTS_DIR/ca-cert.pem:$REMOTE_CERTS/ca-cert.pem"
            "$CERTS_DIR/controller-cert.pem:$REMOTE_CERTS/controller-cert.pem"
            "$CERTS_DIR/controller-key.pem:$REMOTE_CERTS/controller-key.pem"
        )
        ENV_FILE="$GENERATED_DIR/controller.env"
        REMOTE_ENV="${INSTALL_DIR}/config.env"
    else
        # 找到节点专属证书目录
        NODE_CERT_DIR="$CERTS_DIR/nodes/$NODE_ID"
        if [[ ! -d "$NODE_CERT_DIR" ]]; then
            log_warn "  Node cert dir not found: $NODE_CERT_DIR — trying certs/fallback"
            NODE_CERT_DIR="$CERTS_DIR"
        fi
        CERT_FILES=(
            "$CERTS_DIR/ca-cert.pem:$REMOTE_CERTS/ca-cert.pem"
            "$NODE_CERT_DIR/node-cert.pem:$REMOTE_CERTS/node-cert.pem"
            "$NODE_CERT_DIR/node-key.pem:$REMOTE_CERTS/node-key.pem"
        )
        ENV_FILE="$GENERATED_DIR/${NODE_ID}.env"
        REMOTE_ENV="${INSTALL_DIR}/config.env"

        if [[ ! -f "$ENV_FILE" ]]; then
            # 回退到通用 attacker env
            ENV_FILE="$GENERATED_DIR/attacker.env"
            log_warn "  Using fallback env: $ENV_FILE"
        fi
    fi

    # 发送证书
    for cf in "${CERT_FILES[@]}"; do
        SRC="${cf%%:*}"
        DST="${cf##*:}"
        if [[ ! -f "$SRC" ]]; then
            log_warn "  Certificate missing: $SRC — skipping"
            continue
        fi
        if $IS_LOCAL; then
            cp "$SRC" "$DST"
        else
            scp $SSH_OPTS "$SRC" "${SSH_TARGET}:${DST}" >/dev/null
        fi
        log_info "  ✓ $(basename $SRC)"
    done

    # 发送配置文件
    if [[ -f "$ENV_FILE" ]]; then
        if $IS_LOCAL; then
            cp "$ENV_FILE" "$REMOTE_ENV"
        else
            scp $SSH_OPTS "$ENV_FILE" "${SSH_TARGET}:${REMOTE_ENV}" >/dev/null
        fi
        log_info "  ✓ config.env"
    else
        log_warn "  No env file found for $NODE_ID"
    fi

    # 场景文件 (Controller only)
    if [[ "$ROLE" == "controller" ]]; then
        REMOTE_SCENARIOS="${INSTALL_DIR}/scenarios"
        if $IS_LOCAL; then
            mkdir -p "$REMOTE_SCENARIOS"
            cp -r "$ROOT_DIR/scenarios/"* "$REMOTE_SCENARIOS/"
        else
            ssh $SSH_OPTS "$SSH_TARGET" "mkdir -p $REMOTE_SCENARIOS"
            scp $SSH_OPTS -r "$ROOT_DIR/scenarios/"* "${SSH_TARGET}:${REMOTE_SCENARIOS}/" >/dev/null
        fi
        log_info "  ✓ scenarios/"
    fi

    # 二进制包 (如果是 binary 部署)
    if [[ "$DEPLOY_METHOD" == "binary" ]]; then
        if [[ "$ROLE" == "controller" ]]; then
            BINARY_ARCHIVE="$ROOT_DIR/dist/ddos-controller-linux-x86_64.tar.gz"
        else
            BINARY_ARCHIVE="$ROOT_DIR/dist/ddos-attacker-linux-x86_64.tar.gz"
        fi
        if [[ -f "$BINARY_ARCHIVE" ]]; then
            if $IS_LOCAL; then
                tar -xzf "$BINARY_ARCHIVE" -C "$INSTALL_DIR/"
            else
                scp $SSH_OPTS "$BINARY_ARCHIVE" "${SSH_TARGET}:/tmp/ddos-binary.tar.gz" >/dev/null
                ssh $SSH_OPTS "$SSH_TARGET" "tar -xzf /tmp/ddos-binary.tar.gz -C $INSTALL_DIR/ && rm /tmp/ddos-binary.tar.gz"
            fi
            log_info "  ✓ binary archive"
        else
            log_warn "  Binary archive not found: $BINARY_ARCHIVE"
            log_warn "  Run 'make binary' to build"
        fi
    fi

    # 设置权限
    if $IS_LOCAL; then
        chmod 600 "${REMOTE_CERTS}"/*-key.pem 2>/dev/null || true
        chmod 644 "${REMOTE_CERTS}"/*-cert.pem "${REMOTE_CERTS}"/ca-cert.pem 2>/dev/null || true
    else
        ssh $SSH_OPTS "$SSH_TARGET" "chmod 600 ${REMOTE_CERTS}/*-key.pem 2>/dev/null; chmod 644 ${REMOTE_CERTS}/*-cert.pem ${REMOTE_CERTS}/ca-cert.pem 2>/dev/null; true"
    fi

    SUCCESS=$((SUCCESS+1))
    echo ""
done

echo "========================================="
echo -e "  ${GREEN}Success: $SUCCESS${NC}  |  ${RED}Failed: $FAILED${NC}"
echo "========================================="
echo ""
echo -e "${YELLOW}Next:${NC} ./deploy/unified-deploy.sh deploy-all"