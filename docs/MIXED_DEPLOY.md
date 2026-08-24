# DDoS Attack Platform — 混合部署指南

> Controller 和 Attacker 节点可以**任意组合** Docker 或二进制部署方式

---

## ⚡ 零 SSH 一键安装（新增，最简路径）

适合单台控制器 + 逐台添加攻击节点的场景，无需预声明拓扑、无需管理机 SSH 权限：

```bash
# 1. 控制器: 一条命令 (交互式配置, 自动 systemd)
bash <(curl -Ls https://raw.githubusercontent.com/zhang123999-qq/ddos-attack-platform/master/deploy/controller-install.sh)

# 2. 攻击节点: WebUI「节点管理」→「➕ 添加节点」→ 复制命令 → 攻击机粘贴执行
#    节点自动下载二进制并注册上线; 管理命令 ddos-node {status|logs|uninstall}
```

> 下文的 unified-deploy.sh 流程适用于**批量预声明拓扑**的集群场景。

---

## 🎯 核心设计

```
┌──────────────────────────────────────────────────────────────────┐
│                    统一通信层 (HTTP REST + Token)                  │
│                                                                  │
│  Controller ←→ Attacker 只依赖 3 个东西:                           │
│    1. CONTROLLER_URL (https://IP:8443)                           │
│    2. SHARED_SECRET (相同的 32 字节密钥)                           │
│    3. CA 证书 (同一个 CA 签发)                                     │
│                                                                  │
│  部署方式 (Docker/Binary)、OS、网络 —— 全部无关                     │
└──────────────────────────────────────────────────────────────────┘
```

---

## 🚀 5 分钟快速开始

```bash
git clone <repo> && cd ddos-attack-platform

# 1. 编辑集群拓扑
vim deploy/config.yaml
# 修改 hosts / deploy_method 为你实际环境

# 2. 一键全流程
./deploy/unified-deploy.sh generate-configs   # 从 config.yaml 生成所有 .env
./deploy/unified-deploy.sh generate-certs      # 生成 mTLS 证书
./deploy/unified-deploy.sh distribute          # 分发到所有节点
./deploy/unified-deploy.sh deploy-all          # 部署全部
```

---

## 📋 部署组合矩阵

### 场景 A: 全 Docker (开发/测试)

```yaml
controller:
  deploy_method: docker   # ← Docker

attackers:
  - node_id: "attacker-http-01"
    deploy_method: docker # ← Docker
  - node_id: "attacker-raw-01"
    deploy_method: docker # ← Docker
```

```bash
# 一条命令
docker compose up -d
```

### 场景 B: 全二进制 (生产/裸机)

```yaml
controller:
  deploy_method: binary   # ← 二进制 systemd

attackers:
  - node_id: "attacker-http-01"
    deploy_method: binary # ← 二进制 systemd
```

```bash
# 需要先构建二进制
make binary-package
./deploy/unified-deploy.sh deploy-all
```

### 场景 C: 混合 (最常见)

```yaml
controller:
  deploy_method: docker   # ← Docker (方便管理)

attackers:
  - node_id: "attacker-http-01"
    deploy_method: binary # ← 二进制 (高性能)
  - node_id: "attacker-http-02"
    deploy_method: docker # ← Docker (快速扩容)
  - node_id: "attacker-raw-01"
    deploy_method: binary # ← 二进制 (需要 root)
```

```bash
make binary-package                   # 构建二进制
./deploy/unified-deploy.sh distribute # 分发到各节点
./deploy/unified-deploy.sh deploy-controller    # Controller → Docker
./deploy/unified-deploy.sh deploy-attacker attacker-http-01  # → 二进制
./deploy/unified-deploy.sh deploy-attacker attacker-http-02  # → Docker
./deploy/unified-deploy.sh deploy-attacker attacker-raw-01   # → 二进制
```

---

## 🔧 config.yaml 完整参考

```yaml
global:
  shared_secret: "auto"          # auto = 自动生成 32 字节随机密钥
  allowed_target_cidrs:          # 允许攻击的网段
    - "10.100.0.0/16"
  global_max_rps: 50000          # 全局限流

controller:
  host: "10.100.1.10"            # Controller 服务器 IP
  port: 8443
  deploy_method: docker          # docker | binary
  # docker 模式
  docker:
    image: "ghcr.io/your-org/ddos-attack-platform/controller:latest"
  # binary 模式
  install_dir: "/opt/ddos-attack-platform/controller"
  binary:
    archive: "dist/ddos-controller-linux-x86_64.tar.gz"
    service_name: "ddos-controller"
  ssh:
    user: "root"
    port: 22

attackers:
  - node_id: "attacker-http-01"  # 唯一标识
    host: "10.100.1.20"          # 节点 IP
    deploy_method: binary        # docker | binary  ← 每个节点独立选择
    type: http                   # http | raw
    attacks:                     # 支持的攻击类型
      - "http_flood"
      - "slowloris"
    max_rps: 10000
    max_concurrent: 5000
    labels:                      # 用于场景节点选择
      role: "http-attacker"
    # binary 模式
    install_dir: "/opt/ddos-attack-platform/attacker"
    binary:
      archive: "dist/ddos-attacker-linux-x86_64.tar.gz"
    # docker 模式
    docker:
      image: "ghcr.io/your-org/ddos-attack-platform/attacker-http:latest"
    ssh:
      user: "root"
      port: 22
```

---

## 📡 网络要求

| 通信方向 | 协议 | 端口 | 前提 |
|----------|------|------|------|
| Attacker → Controller | HTTPS (mTLS) | 8443 | Controller IP 对 Attacker 可达 |
| Controller → Attacker | HTTP | 8080 | Attacker IP 对 Controller 可达 |
| 管理端 → Controller | HTTPS | 8443 | 浏览器/API 可达 Controller |

**关键**: Controller 的 `host` 必须是所有 Attacker 节点都能访问到的 IP。

---

## 🔐 证书管理

```bash
# 在 deploy/config.yaml 中描述了所有节点后:

# 1. 生成 CA + 所有节点证书
./deploy/generate_certs.sh

# 2. 分发到各节点 (自动识别 Docker/Binary)
./deploy/distribute-certs.sh
```

`distribute-certs.sh` 自动处理：
- **Docker 节点**: 证书放到宿主机的 `certs/` 目录，容器通过 volume mount
- **二进制节点**: 证书通过 scp 发送到 `/opt/ddos-attack-platform/*/certs/`
- **本地节点**: 直接 cp

---

## 📊 集群状态检查

```bash
./deploy/unified-deploy.sh status

# 输出:
#   Controller:  ● healthy  (10.100.1.10:8443)
#   attacker-http-01:  ● healthy  (10.100.1.20:8080)
#   attacker-http-02:  ● healthy  (10.100.1.21:8080)
#   attacker-raw-01:   ● healthy  (10.100.1.30:8080)
```

---

## 🛑 停止集群

```bash
./deploy/unified-deploy.sh stop
# 同时处理 Docker 和 systemd 服务
```

---

## 🔄 扩容：新增 Attacker 节点

```bash
# 1. 编辑 config.yaml，添加:
attackers:
  - node_id: "attacker-http-03"
    host: "10.100.1.22"
    deploy_method: binary   # ← 新节点用二进制
    ...

# 2. 重新生成配置
./deploy/unified-deploy.sh generate-configs

# 3. 分发到新节点
./deploy/unified-deploy.sh distribute

# 4. 部署新节点
./deploy/unified-deploy.sh deploy-attacker attacker-http-03
```

---

## 📦 推荐拓扑

### 小型教学环境 (3 台机器)

| 机器 | 角色 | 部署方式 |
|------|------|----------|
| 10.100.1.10 | Controller | Docker |
| 10.100.1.20 | Attacker-HTTP | 二进制 |
| 10.100.1.30 | Attacker-RAW | 二进制 (root) |

### 中型演练环境 (5 台机器)

| 机器 | 角色 | 部署方式 |
|------|------|----------|
| 10.100.1.10 | Controller | Docker |
| 10.100.1.20 | Attacker-HTTP-01 | Docker |
| 10.100.1.21 | Attacker-HTTP-02 | 二进制 |
| 10.100.1.30 | Attacker-RAW-01 | 二进制 |
| 10.100.1.31 | Attacker-RAW-02 | Docker (privileged) |

---

## ⚙️ systemd 服务管理 (二进制节点)

```bash
# 查看状态
systemctl status ddos-controller
systemctl status ddos-attacker

# 查看日志
journalctl -u ddos-controller -f
journalctl -u ddos-attacker -f

# 重启
systemctl restart ddos-controller
```

---

## 📝 相关文件

| 文件 | 用途 |
|------|------|
| `deploy/config.yaml` | 集群拓扑唯一真相源 |
| `deploy/unified-deploy.sh` | 统一部署编排器 |
| `deploy/generate-configs.sh` | 从 config.yaml 生成 .env |
| `deploy/distribute-certs.sh` | 证书+配置自动分发 |
| `deploy/systemd/*.service` | systemd 单元文件 |
| `deploy/install-service.sh` | 二进制节点安装脚本 |