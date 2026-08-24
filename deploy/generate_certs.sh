#!/bin/bash
# mTLS 证书生成脚本
# 运行位置: controller/ 目录或项目根目录
# 生成: CA、Controller 证书、N 个节点证书

set -euo pipefail

# 配置
CA_DIR="certs"
CA_KEY="${CA_DIR}/ca-key.pem"
CA_CERT="${CA_DIR}/ca-cert.pem"
CONTROLLER_KEY="${CA_DIR}/controller-key.pem"
CONTROLLER_CERT="${CA_DIR}/controller-cert.pem"
CONTROLLER_CSR="${CA_DIR}/controller-csr.pem"
CONTROLLER_CONF="${CA_DIR}/controller.conf"

NODES_DIR="${CA_DIR}/nodes"

# 默认配置 (可通过环境变量覆盖)
CONTROLLER_IP="${CONTROLLER_IP:-10.100.1.10}"
CONTROLLER_HOSTNAME="${CONTROLLER_HOSTNAME:-ddos-controller}"
NODE_IPS="${NODE_IPS:-10.100.1.20 10.100.1.21}"
NODE_HOSTNAMES="${NODE_HOSTNAMES:-attacker-http-01 attacker-raw-01}"
# 有效期分层 (对齐 README 轮换红线: CA 2年、节点证书 1年)
DAYS_VALID_CA="${DAYS_VALID_CA:-730}"
DAYS_VALID_NODE="${DAYS_VALID_NODE:-365}"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# 创建目录
mkdir -p "${CA_DIR}" "${NODES_DIR}"

# 生成 CA
if [[ ! -f "${CA_KEY}" ]]; then
    log_info "Generating CA private key..."
    openssl genrsa -out "${CA_KEY}" 4096
    chmod 600 "${CA_KEY}"
fi

if [[ ! -f "${CA_CERT}" ]]; then
    log_info "Generating CA certificate (valid ${DAYS_VALID_CA} days)..."
    openssl req -x509 -new -nodes -key "${CA_KEY}" -sha256 -days "${DAYS_VALID_CA}" \
        -out "${CA_CERT}" \
        -subj "/CN=DDoS Lab CA/O=Internal Security Team/OU=Red Team"
fi

# 生成 Controller 证书配置
cat > "${CONTROLLER_CONF}" <<EOF
[req]
distinguished_name = req_distinguished_name
req_extensions = v3_req
prompt = no

[req_distinguished_name]
CN = ${CONTROLLER_HOSTNAME}
O = Internal Security Team
OU = Red Team Controller

[v3_req]
keyUsage = keyEncipherment, dataEncipherment
extendedKeyUsage = serverAuth, clientAuth
subjectAltName = @alt_names

[alt_names]
IP.1 = ${CONTROLLER_IP}
IP.2 = 127.0.0.1
DNS.1 = ${CONTROLLER_HOSTNAME}
DNS.2 = localhost
EOF

# 生成 Controller 证书
if [[ ! -f "${CONTROLLER_KEY}" ]]; then
    log_info "Generating Controller private key..."
    openssl genrsa -out "${CONTROLLER_KEY}" 2048
    chmod 600 "${CONTROLLER_KEY}"
fi

if [[ ! -f "${CONTROLLER_CERT}" ]]; then
    log_info "Generating Controller certificate (valid ${DAYS_VALID_NODE} days)..."
    openssl req -new -key "${CONTROLLER_KEY}" -out "${CONTROLLER_CSR}" -config "${CONTROLLER_CONF}"
    openssl x509 -req -in "${CONTROLLER_CSR}" -CA "${CA_CERT}" -CAkey "${CA_KEY}" \
        -CAcreateserial -out "${CONTROLLER_CERT}" -days "${DAYS_VALID_NODE}" -sha256 \
        -extensions v3_req -extfile "${CONTROLLER_CONF}"
fi

# 生成节点证书
IFS=' ' read -r -a NODE_IP_ARRAY <<< "${NODE_IPS}"
IFS=' ' read -r -a NODE_HOST_ARRAY <<< "${NODE_HOSTNAMES}"

if [[ ${#NODE_IP_ARRAY[@]} -ne ${#NODE_HOST_ARRAY[@]} ]]; then
    log_error "NODE_IPS and NODE_HOSTNAMES count mismatch!"
    exit 1
fi

for i in "${!NODE_IP_ARRAY[@]}"; do
    NODE_IP="${NODE_IP_ARRAY[i]}"
    NODE_HOST="${NODE_HOST_ARRAY[i]}"
    NODE_DIR="${NODES_DIR}/${NODE_HOST}"
    
    mkdir -p "${NODE_DIR}"
    
    NODE_KEY="${NODE_DIR}/node-key.pem"
    NODE_CERT="${NODE_DIR}/node-cert.pem"
    NODE_CSR="${NODE_DIR}/node-csr.pem"
    NODE_CONF="${NODE_DIR}/node.conf"
    
    cat > "${NODE_CONF}" <<EOF
[req]
distinguished_name = req_distinguished_name
req_extensions = v3_req
prompt = no

[req_distinguished_name]
CN = ${NODE_HOST}
O = Internal Security Team
OU = Red Team Attacker

[v3_req]
keyUsage = keyEncipherment, dataEncipherment
extendedKeyUsage = clientAuth
subjectAltName = @alt_names

[alt_names]
IP.1 = ${NODE_IP}
DNS.1 = ${NODE_HOST}
EOF
    
    if [[ ! -f "${NODE_KEY}" ]]; then
        log_info "Generating node key for ${NODE_HOST}..."
        openssl genrsa -out "${NODE_KEY}" 2048
        chmod 600 "${NODE_KEY}"
    fi
    
    if [[ ! -f "${NODE_CERT}" ]]; then
        log_info "Generating node cert for ${NODE_HOST} (valid ${DAYS_VALID_NODE} days)..."
        openssl req -new -key "${NODE_KEY}" -out "${NODE_CSR}" -config "${NODE_CONF}"
        openssl x509 -req -in "${NODE_CSR}" -CA "${CA_CERT}" -CAkey "${CA_KEY}" \
            -CAcreateserial -out "${NODE_CERT}" -days "${DAYS_VALID_NODE}" -sha256 \
            -extensions v3_req -extfile "${NODE_CONF}"
    fi
    
    # 复制 CA 证书到节点目录
    cp "${CA_CERT}" "${NODE_DIR}/ca-cert.pem"
done

# 设置权限
chmod 644 "${CA_CERT}" "${CONTROLLER_CERT}"
find "${NODES_DIR}" -name "*.pem" -exec chmod 644 {} \;
find "${NODES_DIR}" -name "*-key.pem" -exec chmod 600 {} \;

log_info "Certificate generation complete!"
echo ""
echo "=== Controller certs (copy to controller/certs/) ==="
echo "  CA:        ${CA_CERT}"
echo "  Cert:      ${CONTROLLER_CERT}"
echo "  Key:       ${CONTROLLER_KEY}"
echo ""
echo "=== Node certs (copy to each attacker node) ==="
for i in "${!NODE_HOST_ARRAY[@]}"; do
    NODE_HOST="${NODE_HOST_ARRAY[i]}"
    NODE_DIR="${NODES_DIR}/${NODE_HOST}"
    echo "  Node: ${NODE_HOST}"
    echo "    CA:     ${NODE_DIR}/ca-cert.pem"
    echo "    Cert:   ${NODE_DIR}/node-cert.pem"
    echo "    Key:    ${NODE_DIR}/node-key.pem"
done
echo ""
echo "=== Next steps ==="
echo "1. Copy controller certs to controller/certs/"
echo "2. Copy each node's certs to attacker/certs/ on respective machines"
echo "3. Generate shared secret: openssl rand -hex 32"
echo "4. Configure config.env files"
echo "5. Deploy with docker-compose"