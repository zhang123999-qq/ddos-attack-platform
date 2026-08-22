#!/usr/bin/env bash
# =============================================================================
# unified-deploy.sh — 统一部署编排器 (Docker / 二进制 混合部署)
#
# 用法:
#   ./unified-deploy.sh generate-configs    生成各节点 .env
#   ./unified-deploy.sh distribute-certs     分发证书+配置到各节点
#   ./unified-deploy.sh deploy-controller    部署 Controller (自动选择 docker/binary)
#   ./unified-deploy.sh deploy-attacker ID  部署指定 Attacker 节点
#   ./unified-deploy.sh deploy-all           一键部署全部
#   ./unified-deploy.sh status               查看集群状态
#   ./unified-deploy.sh stop                 停止所有
#   ./unified-deploy.sh logs SERVICE         查看日志
#
# 核心: 同一套 config.yaml, 每个节点独立选择 docker 或 binary
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG_FILE="${SCRIPT_DIR}/config.yaml"
GENERATED_DIR="${ROOT_DIR}/generated"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[0;34m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "\n${BLUE}━━━ $* ━━━${NC}"; }

usage() {
    cat <<EOF
${BOLD}DDoS Attack Platform — Unified Deploy${NC}

${CYAN}Usage:${NC}
  $0 generate-configs      从 config.yaml 生成所有节点 .env
  $0 generate-certs         生成 mTLS 证书
  $0 distribute            分发证书 + 配置 + 二进制到各节点
  $0 deploy-controller     部署 Controller (自动 docker/binary)
  $0 deploy-attacker ID    部署指定 Attacker 节点
  $0 deploy-all            一键全流程部署
  $0 status                查看集群状态
  $0 stop                  停止所有服务
  $0 logs SERVICE          查看日志 (controller / attacker-http-01 / ...)

${CYAN}Quick Start:${NC}
  $0 generate-configs && $0 generate-certs && $0 distribute && $0 deploy-all
EOF
    exit 0
}

# ========== YAML 解析辅助 ==========
parse_yaml() {
    python3 -c "
import yaml, json, sys
with open('$CONFIG_FILE') as f:
    cfg = yaml.safe_load(f)
# 输出 shell 变量
g = cfg['global']
secret = g['shared_secret']
if secret == 'auto':
    import secrets
    secret = secrets.token_hex(32)
print(f'SHARED_SECRET={secret}')
print(f'ALLOWED_TARGET_CIDRS={",".join(g[\"allowed_target_cidrs\"])}')
print(f'GLOBAL_MAX_RPS={g[\"global_max_rps\"]}')
print(f'GLOBAL_MAX_PPS={g[\"global_max_pps\"]}')
print(f'GLOBAL_MAX_CONCURRENT={g[\"global_max_concurrent\"]}')

ctrl = cfg['controller']
print(f'CTRL_HOST={ctrl[\"host\"]}')
print(f'CTRL_PORT={ctrl[\"port\"]}')
print(f'CTRL_DEPLOY={ctrl[\"deploy_method\"]}')
print(f'CTRL_SSH_USER={ctrl.get(\"ssh\",{}).get(\"user\",\"root\")}')
print(f'CTRL_SSH_PORT={ctrl.get(\"ssh\",{}).get(\"port\",22)}')
print(f'CTRL_INSTALL_DIR={ctrl.get(\"install_dir\",\"/opt/ddos-attack-platform/controller\")}')
print(f'CONTROLLER_URL=https://{ctrl[\"host\"]}:{ctrl[\"port\"]}')

print(f'ATTACKER_COUNT={len(cfg.get(\"attackers\",[]))}')
for i, atk in enumerate(cfg.get('attackers', [])):
    print(f'ATK_{i}_ID={atk[\"node_id\"]}')
    print(f'ATK_{i}_HOST={atk[\"host\"]}')
    print(f'ATK_{i}_DEPLOY={atk[\"deploy_method\"]}')
    print(f'ATK_{i}_TYPE={atk[\"type\"]}')
    print(f'ATK_{i}_SSH_USER={atk.get(\"ssh\",{}).get(\"user\",\"root\")}')
    print(f'ATK_{i}_SSH_PORT={atk.get(\"ssh\",{}).get(\"port\",22)}')
    print(f'ATK_{i}_INSTALL_DIR={atk.get(\"install_dir\",\"/opt/ddos-attack-platform/attacker\")}')
" 2>/dev/null
}

# ========== SSH 前缀生成 ==========
ssh_prefix() {
    local host="$1" user="$2" port="$3"
    if [[ "$host" == "localhost" || "$host" == "127.0.0.1" ]]; then
        echo ""
    else
        echo "ssh -o StrictHostKeyChecking=no -p $port $user@$host"
    fi
}

# ========== 本地执行或远程执行 ==========
run_cmd() {
    local host="$1" user="$2" port="$3" cmd="$4"
    if [[ "$host" == "localhost" || "$host" == "127.0.0.1" ]]; then
        bash -c "$cmd"
    else
        ssh -o StrictHostKeyChecking=no -p "$port" "$user@$host" "$cmd"
    fi
}

# ========== 部署单个节点 (自动 docker/binary) ==========
deploy_node() {
    local role="$1" host="$2" deploy_method="$3" install_dir="$4" ssh_user="$5" ssh_port="$6"
    local extra="${7:-}"  # node_id or container_name

    log_step "Deploying $role ($deploy_method) → $host"

    case "$deploy_method" in
        docker)
            log_info "  Method: Docker Compose"
            local compose_file
            if [[ "$role" == "controller" ]]; then
                compose_file="deploy/docker-compose.controller.yml"
            elif [[ "$extra" == *"raw"* ]]; then
                compose_file="deploy/docker-compose.attacker-raw.yml"
            else
                compose_file="deploy/docker-compose.attacker.yml"
            fi

            # 确保 env 文件存在
            local env_file="${GENERATED_DIR}/${extra}.env"
            [[ ! -f "$env_file" ]] && env_file="${GENERATED_DIR}/controller.env"

            run_cmd "$host" "$ssh_user" "$ssh_port" "
                cd ${ROOT_DIR}
                if [ -f '${env_file}' ]; then set -a; source '${env_file}'; set +a; fi
                export SHARED_SECRET=\"${SHARED_SECRET:-changeme}\"
                export CONTROLLER_URL=\"${CONTROLLER_URL}\"
                docker compose -f ${compose_file} up -d --remove-orphans
            " || log_error "Docker deploy failed for $role"
            ;;

        binary)
            log_info "  Method: Binary + systemd"
            local service_name
            if [[ "$role" == "controller" ]]; then
                service_name="ddos-controller"
            elif [[ "$extra" == *"raw"* ]]; then
                service_name="ddos-attacker-raw"
            else
                service_name="ddos-attacker"
            fi

            run_cmd "$host" "$ssh_user" "$ssh_port" "
                cd ${install_dir}
                if [ -f './install-service.sh' ]; then
                    bash install-service.sh ${role}
                else
                    # 手动安装
                    cp -f ${ROOT_DIR}/deploy/systemd/${service_name}.service /etc/systemd/system/
                    systemctl daemon-reload
                    systemctl enable ${service_name}
                    systemctl restart ${service_name}
                fi
            " || log_error "Binary deploy failed for $role"
            ;;
        *)
            log_error "Unknown deploy_method: $deploy_method"
            ;;
    esac

    log_info "  ✓ $role deployed"
}

# ========== 命令处理 ==========
CMD="${1:-}"; shift || true

case "$CMD" in
    help|--help|-h)
        usage
        ;;

    generate-configs)
        log_step "Generating per-node configs from config.yaml"
        bash "${SCRIPT_DIR}/generate-configs.sh"
        ;;

    generate-certs)
        log_step "Generating mTLS certificates"
        bash "${SCRIPT_DIR}/generate_certs.sh"
        ;;

    distribute)
        log_step "Distributing certs + configs to all nodes"
        bash "${SCRIPT_DIR}/distribute-certs.sh"
        ;;

    deploy-controller)
        eval "$(parse_yaml)"
        deploy_node "controller" "$CTRL_HOST" "$CTRL_DEPLOY" \
            "$CTRL_INSTALL_DIR" "$CTRL_SSH_USER" "$CTRL_SSH_PORT" "controller"
        ;;

    deploy-attacker)
        NODE_ID="${1:-}"
        if [[ -z "$NODE_ID" ]]; then
            log_error "Usage: $0 deploy-attacker <node_id>"
            exit 1
        fi
        eval "$(parse_yaml)"
        FOUND=false
        for ((i=0; i<ATTACKER_COUNT; i++)); do
            eval "ID=\${ATK_${i}_ID}"
            if [[ "$ID" == "$NODE_ID" ]]; then
                eval "HOST=\${ATK_${i}_HOST}"
                eval "DEPLOY=\${ATK_${i}_DEPLOY}"
                eval "SSH_USER=\${ATK_${i}_SSH_USER}"
                eval "SSH_PORT=\${ATK_${i}_SSH_PORT}"
                eval "INSTALL_DIR=\${ATK_${i}_INSTALL_DIR}"
                eval "TYPE=\${ATK_${i}_TYPE}"
                deploy_node "attacker-${TYPE}" "$HOST" "$DEPLOY" \
                    "$INSTALL_DIR" "$SSH_USER" "$SSH_PORT" "$NODE_ID"
                FOUND=true
                break
            fi
        done
        if ! $FOUND; then
            log_error "Node not found: $NODE_ID"
            exit 1
        fi
        ;;

    deploy-all)
        log_step "=== Full Cluster Deployment ==="
        eval "$(parse_yaml)"

        echo ""
        echo "  Controller:  $CTRL_HOST  ($CTRL_DEPLOY)"
        for ((i=0; i<ATTACKER_COUNT; i++)); do
            eval "echo \"  Attacker:    \${ATK_${i}_ID} → \${ATK_${i}_HOST} (\${ATK_${i}_DEPLOY})\""
        done
        echo ""
        read -p "Proceed with deployment? [y/N] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then exit 0; fi

        deploy_node "controller" "$CTRL_HOST" "$CTRL_DEPLOY" \
            "$CTRL_INSTALL_DIR" "$CTRL_SSH_USER" "$CTRL_SSH_PORT" "controller"

        for ((i=0; i<ATTACKER_COUNT; i++)); do
            eval "ID=\${ATK_${i}_ID}"
            eval "HOST=\${ATK_${i}_HOST}"
            eval "DEPLOY=\${ATK_${i}_DEPLOY}"
            eval "SSH_USER=\${ATK_${i}_SSH_USER}"
            eval "SSH_PORT=\${ATK_${i}_SSH_PORT}"
            eval "INSTALL_DIR=\${ATK_${i}_INSTALL_DIR}"
            eval "TYPE=\${ATK_${i}_TYPE}"
            deploy_node "attacker-${TYPE}" "$HOST" "$DEPLOY" \
                "$INSTALL_DIR" "$SSH_USER" "$SSH_PORT" "$ID"
        done

        log_step "=== Deployment Complete ==="
        echo "  Controller API:  https://${CTRL_HOST}:${CTRL_PORT}/docs"
        echo "  Web UI:          https://${CTRL_HOST}:${CTRL_PORT}"
        ;;

    status)
        eval "$(parse_yaml)"
        echo -e "${BOLD}Cluster Status${NC}"
        echo "────────────────────────────────────────"

        # Controller
        if run_cmd "$CTRL_HOST" "$CTRL_SSH_USER" "$CTRL_SSH_PORT" \
            "curl -sk https://localhost:${CTRL_PORT}/ready 2>/dev/null || echo 'UNREACHABLE'"; then
            echo -e "  Controller:  ${GREEN}● healthy${NC}  (${CTRL_HOST}:${CTRL_PORT})"
        else
            echo -e "  Controller:  ${RED}✗ unreachable${NC}"
        fi

        # Attackers
        for ((i=0; i<ATTACKER_COUNT; i++)); do
            eval "ID=\${ATK_${i}_ID}"
            eval "HOST=\${ATK_${i}_HOST}"
            if run_cmd "$HOST" "root" "22" \
                "curl -s http://localhost:8080/health 2>/dev/null || echo 'UNREACHABLE'"; then
                echo -e "  ${ID}:  ${GREEN}● healthy${NC}  (${HOST}:8080)"
            else
                echo -e "  ${ID}:  ${RED}✗ unreachable${NC}"
            fi
        done
        ;;

    stop)
        eval "$(parse_yaml)"
        log_step "Stopping all services"
        run_cmd "$CTRL_HOST" "$CTRL_SSH_USER" "$CTRL_SSH_PORT" \
            "docker compose -f ${ROOT_DIR}/deploy/docker-compose.controller.yml down 2>/dev/null; systemctl stop ddos-controller 2>/dev/null; true"
        for ((i=0; i<ATTACKER_COUNT; i++)); do
            eval "ID=\${ATK_${i}_ID}"
            eval "HOST=\${ATK_${i}_HOST}"
            run_cmd "$HOST" "root" "22" \
                "docker compose -f ${ROOT_DIR}/deploy/docker-compose.attacker*.yml down 2>/dev/null; systemctl stop ddos-attacker ddos-attacker-raw 2>/dev/null; true"
        done
        log_info "All services stopped"
        ;;

    logs)
        SERVICE="${1:-controller}"
        eval "$(parse_yaml)"
        if [[ "$SERVICE" == "controller" ]]; then
            run_cmd "$CTRL_HOST" "$CTRL_SSH_USER" "$CTRL_SSH_PORT" \
                "docker logs -f ddos-controller 2>/dev/null || journalctl -u ddos-controller -f"
        else
            for ((i=0; i<ATTACKER_COUNT; i++)); do
                eval "ID=\${ATK_${i}_ID}"
                if [[ "$ID" == "$SERVICE" ]]; then
                    eval "HOST=\${ATK_${i}_HOST}"
                    run_cmd "$HOST" "root" "22" \
                        "docker logs -f ddos-${ID} 2>/dev/null || journalctl -u ddos-attacker -f"
                    exit 0
                fi
            done
            log_error "Service not found: $SERVICE"
        fi
        ;;

    *)
        echo -e "${RED}Unknown command: $CMD${NC}"
        usage
        ;;
esac