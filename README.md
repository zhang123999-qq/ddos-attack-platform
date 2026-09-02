# DDoS Attack Platform v1.5.0 — 内网红方攻击演练平台

[![Version](https://img.shields.io/badge/version-1.5.0-blue.svg)]()
[![License](https://img.shields.io/badge/license-Internal%20Only-red.svg)]()
[![Python](https://img.shields.io/badge/python-3.11%2B-green.svg)]()
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows-lightgrey.svg)]()
[![Tests](https://img.shields.io/badge/tests-97%2F97%20pass-brightgreen.svg)]()
[![E2E](https://img.shields.io/badge/E2E-4%20suites-blue.svg)]()
[![Security](https://img.shields.io/badge/security-hardened-success.svg)]()

> ⚠️ **仅供授权内网网络安全团队教学/演练使用**  
> 📋 **部署前必须阅读并签署 [安全守则](docs/SAFETY_RULES.md) 与授权确认书**  
> 🚫 **严禁用于任何非授权测试、生产环境攻击、公共网络攻击**
> 🐛 **发现安全漏洞**: 见 [SECURITY.md](docs/SECURITY.md) 披露流程

> **v1.5.0 重大安全加固**: 目标白名单默认开启 + Node mTLS 完整链路 (Controller 内置 mini-CA) + 状态持久化 (SQLite WAL) + Admin API 限流 (60 RPM) + `/metrics` Prometheus 端点 (15 指标) + main.py 拆分 (671 → 187 行)
> - **11 项技术债关闭** (R-NEW-1, R-NEW-2, O-NEW-1, S-NEW-3, M-3, NEW-5, NEW-2, NEW-3, T-NEW-4, R-NEW-3, C-5)
> - **97 单元测试 + 4 E2E 套件** 全部 PASS (从 v1.4.1 的 32 单元 +178%)
> - **CI 门禁**: pytest + ruff + gitleaks + pip-audit
> - 详见 [CHANGELOG.md](docs/CHANGELOG.md) v1.5.0 完整条目

---

## ⚖️ 法律免责声明

### 1. 核心免责原则

**本平台开发者、维护者、贡献者（以下统称"平台方"）不对以下情况承担任何法律责任：**

| 免责范围 | 具体说明 | 法律依据 |
|---------|----------|---------|
| **非授权使用** | 使用者未获得书面授权而发起攻击引发的刑事/民事法律纠纷 | 《刑法》第285-287条、《网络安全法》第27条 |
| **配置错误** | 白名单、熔断、限流配置不当导致的攻击失控及连带损害 | 《民法典》第1165条（过错责任原则） |
| **生产环境损害** | 在生产网络直接攻击造成的业务中断、数据丢失、经济损失 | 《网络安全法》第59条、《数据安全法》第45条 |
| **证书/密钥泄露** | 因保管不善导致的私钥、共享密钥泄露及后续安全事件 | 《商业秘密保护条例》第7条 |
| **第三方滥用** | 平台代码/配置/证书外传导致的第三方滥用攻击 | 《网络安全法》第28条（网络产品提供者义务） |

### 2. 使用前提条件（缺一不可）

使用本平台即视为已确认并同意以下所有条件：

- ✅ **已获得目标网络书面授权**（盖章/签名的正式授权文件，含授权网段、有效期、攻击类型、负责人联系方式）
- ✅ **仅在隔离实验网段内操作**（如 `10.100.0.0/16`、`192.168.0.0/16`，物理或逻辑隔离，无互联网路由）
- ✅ **遵守所在国家/地区法律法规及单位安全制度**（含《网络安全法》《数据安全法》《关键信息基础设施安全保护条例》等）
- ✅ **不外传、不扩散、不商用、不作非授权用途**（平台代码、配置、证书、密钥均属内部机密）
- ✅ **已完整阅读并签署《安全守则与使用规范》（docs/SAFETY_RULES.md）**

### 3. 知情同意确认

所有参与部署、操作、监督的人员，**必须**在使用前完成：
1. 完整阅读 `docs/SAFETY_RULES.md` 全文
2. 签署《知情同意书》（见安全守则文档末尾签署页）
3. 归档备查，接受审计检查

---

## 📊 项目状态 (v1.5.0)

| 维度 | 状态 | 详情 |
|------|------|------|
| **代码量** | ~8,200 LOC | controller 5,800 + attacker 2,400 |
| **单元测试** | 97/97 PASS | 16 controller 套件 + 2 attacker 套件 |
| **E2E 验证** | 4 套件 (含 Linux netns) | 真实 WSL 卸载重装 + 网络隔离 |
| **GHA** | 2 workflows | docker-publish (含 pytest+ruff+gitleaks+pip-audit) + binary-release |
| **技术债** | 0 Critical, 0 Medium, 13 Low | 详见 CHANGELOG.md v1.5.0 |
| **License** | Internal Only | 严禁外传/商用/非授权 |
| **文档** | 12 主流 doc | CHANGELOG/ARCHITECTURE/API_REFERENCE/SAFETY_RULES/SECURITY/CONTRIBUTING/REVIEW/MAINTAINERS/TEACHING_GUIDE/CODE_REVIEW/REGRESSION/DEEP_EVALUATION_v3 |

**v1.5.0 关键能力**:
- ✅ **mTLS 完整链路** (Controller 内置 mini-CA + Node 强制 mTLS, fail-closed)
- ✅ **目标白名单默认开启** (10.100.0.0/16 等 3 段, 越权自动拦截)
- ✅ **状态持久化** (SQLite WAL, 重启自动恢复)
- ✅ **Admin API 限流** (60 RPM, 429 + Retry-After)
- ✅ **Prometheus 指标** (15 个 + /metrics 端点)
- ✅ **systemd OOM 防护** (MemoryMax + OOMPolicy=stop)
- ✅ **WS 双模式 auth** (URL 兼容 + 首消息新方案)
- ✅ **CI 门禁** (pytest + ruff + gitleaks + pip-audit)
- ✅ **main.py 拆分** (671 → 187 行, 7 routes 模块)
- ✅ **Node 强制 mTLS** (缺证书 SystemExit 1, 嗅探风险消除)

**仍 open (v1.5.1 候选)**:
- 🟡 emergency_stop 无双人确认 (S-NEW-2, 降级为限流 + 审计)
- 🟢 13 项 Low (详见 DEEP_EVALUATION_v3.md 剩余)

---

## 🚀 快速开始（四种部署方式，任选其一）

### 方式〇：一键安装（最快上手）⭐

**主控制器** — 在一台 Linux 服务器以 root 执行：

```bash
bash <(curl -Ls https://raw.githubusercontent.com/zhang123999-qq/ddos-attack-platform/master/deploy/controller-install.sh)
```

交互式配置端口/密钥后自动完成 systemd 部署，结束时打印 WebUI 地址。

**管理快捷指令**（装完即用）：

```bash
ddos-controller            # 查看状态（版本/WebUI地址/在线节点数）
ddos-controller logs       # 跟踪日志        (l = 最近 50 行)
sudo ddos-controller restart    # 重启服务        (r 同义)
sudo ddos-controller update     # ⭐ 一键升级到最新 Release
sudo ddos-controller uninstall  # 完整卸载
```

> v1.3.3: 变更类操作需要 root。若当前 sudo 免密缓存有效，直接 `ddos-controller restart`
> 也会自动代执行；否则会明确提示加 sudo，不再出现含糊的 "Access denied"。
>
> v1.3.4: 安装器创建专用 `ddos` 系统用户（无登录权限），`config.env` 固定 `chmod 600`
> 保护 `SHARED_SECRET`；systemd 单元添加 `User=ddos Group=ddos`（之前因用户不存在会回退
> root 运行）。升级路径自动修正 owner 与权限。

**添加攻击节点** — 打开 WebUI「节点管理」→「➕ 添加节点」，
选择类型、填节点 ID，复制生成的命令到目标攻击机以 root 粘贴执行：

```bash
# 形态示例 (实际以 WebUI 生成的一行为准, 内含一次性 enroll token)
bash <(curl -Lsk https://<CONTROLLER_IP>:8443/install.sh) \
    -e https://<CONTROLLER_IP>:8443 \
    -t <enroll_token> --id attacker-http-02 --type http
```

节点自动：下载二进制（优先控制器内网分发，回退 GitHub）→ 写入配置 →
systemd 启动 → **自动注册出现在仪表盘**。无需 SSH、无需预配证书。

**节点管理快捷指令**（攻击机上执行）：

```bash
ddos-node            # 查看状态（进程/健康/节点ID）
ddos-node logs       # 跟踪日志        (l = 最近 50 行)
ddos-node restart    # 重启服务        (r 同义)
ddos-node uninstall  # 完整卸载
```

> 安全机制：enroll token = HMAC(SHARED_SECRET, "ddos-enroll:"+node_id+":"+小时桶)，
> 绑定单个节点且约 1 小时自然过期；节点通过控制器自签 CA + 指纹交叉校验后，
> 才在受验证 TLS 信道内换取运行密钥。

### 方式一：Docker 部署

```bash
# 1. 克隆项目
git clone <repo-url> && cd ddos-attack-platform

# 2. 生成证书与配置
cd deploy
./generate_certs.sh
./generate-configs.sh

# 3. 单机全栈启动（测试用）
cd ..
docker-compose up -d --build

# 4. 访问控制台
# Web UI: https://localhost:8443 (自签证书，浏览器点击"高级->继续访问")
# API 文档: https://localhost:8443/docs
```

### 方式二：源码运行（开发调试用）

```bash
# Controller
cd controller
cp config.env.example config.env
# 编辑 config.env: CONTROLLER_IP, ALLOWED_TARGET_CIDRS, SHARED_SECRET
pip install -r requirements.txt
python -m app.main

# Attacker (新终端)
cd attacker
cp config.env.example config.env
# 编辑: CONTROLLER_URL, NODE_ID, ATTACK_TYPES
pip install -r requirements.txt
python -m app.main
```

### 方式三：二进制部署（无需 Python，适合气隙/生产环境）

```bash
# 1. 构建二进制（需在有网环境）
cd build
pip install -r requirements-build.txt
python build.py all

# 2. 产物位置
# dist/controller/  - Controller 可执行文件 + 配置 + 场景
# dist/attacker/    - Attacker 可执行文件 + 配置

# 3. 部署到目标机器（离线拷贝）
tar -czf ddos-attack-platform-linux.tar.gz dist/
scp ddos-attack-platform-linux.tar.gz user@target:/opt/
ssh user@target "cd /opt && tar -xzf ddos-attack-platform-linux.tar.gz"

# 4. 启动
# Linux/macOS:
cd controller && ./start.sh
cd attacker   && ./start.sh

# Windows:
cd controller && start.bat
cd attacker   && start.bat
```

---

## 🏗️ 架构概览

```
内网环境 (例: 10.100.0.0/16)
├── 🎮 Controller (10.100.1.10)      # 指挥中心
│   ├── REST API + WebSocket         # 管理接口、实时推送
│   ├── mTLS 双向认证                # 零信任通信
│   ├── 场景编排 (CC/SYN/Slowloris/混合波/渐进波)
│   ├── 全局限流 (令牌桶三级: 全局/节点/Worker)
│   ├── 紧急熔断 (<100ms 全网停止)
│   └── 审计事件流 (默认内存会话级; AUDIT_FILE_ENABLED=true 落盘 JSONL)
│
├── ⚔️ Attacker-HTTP (10.100.1.20)   # 应用层攻击节点
│   ├── http_flood (CC攻击)          # aiohttp 高并发
│   ├── slowloris (慢速攻击)         # 连接耗尽
│   ├── 运行中每 2s 上报进度快照      # v1.3 实时反馈
│   └── 无需特权 (普通用户运行)
│
├── ⚔️ Attacker-RAW (10.100.1.21)    # 传输层攻击节点
│   ├── syn_flood                    # scapy 原始套接字 (支持域名目标, 自动解析)
│   ├── udp_flood / udp_reflection   # UDP 洪水 / 反射放大
│   └── 需 CAP_NET_RAW (cap_add, 非 privileged)
│
├── ⚔️ Attacker-N ...                # 横向扩展 (无状态)
│
└── 🎯 Target Network                # 被测防御体系/靶机网段
```

---

## 📋 详细部署指南

> ⚡ **更简单的方式**：见上方「方式〇：一键安装」——控制器一条命令、节点 WebUI 复制粘贴，
> 全程无需手动生成证书或编辑配置。以下为传统手动部署流程（精细控制时使用）。

### 1. 控制器部署（1 台机器）

```bash
cd controller
cp config.env.example config.env

# 必须修改的关键配置：
# CONTROLLER_IP=10.100.1.10          # Controller 绑定 IP
# SHARED_SECRET="$(openssl rand -hex 32)"  # 预共享密钥 (32字节)
# 注: v1.3 起无需配置 ALLOWED_TARGET_CIDRS (白名单技术强制已移除,
#     目标约束由授权流程管控, 见 SAFETY_RULES.md 红线条款)

# 生成证书
cd ../deploy && ./generate_certs.sh

# 启动
cd ../controller && docker-compose up -d
```

### 2. 攻击节点部署（N 台机器）

**HTTP/Slowloris 节点（普通权限，无需 root）**

```bash
cd attacker
cp config.env.example config.env

# 必须修改：
# NODE_ID=attacker-http-01
# CONTROLLER_URL=https://10.100.1.10:8443
# ATTACK_TYPES=http_flood,slowloris
# MAX_RPS=10000
# MAX_CONCURRENT=5000

docker-compose up -d
```

**SYN/UDP/反射节点（需 CAP_NET_RAW）**

```bash
# 修改 config.env:
# ATTACK_TYPES=syn_flood,udp_flood,udp_reflection
# MAX_PPS=50000
# NETWORK_INTERFACE=eth0

# docker-compose.yml 中启用:
# cap_add:
#   - NET_RAW
# 或者 privileged: true (不推荐)

docker-compose up -d
```

### 3. 访问控制台

| 入口 | 地址 | 说明 |
|------|------|------|
| **Web UI** | `https://<controller-ip>:8443` | 自签证书，浏览器需信任 |
| **API 文档** | `https://<controller-ip>:8443/docs` | Swagger UI |
| **API 调用** | `curl -k -H "Authorization: Bearer <TOKEN>" https://<ctrl>:8443/api/v1/...` | 需 Controller Token |
| **实时指标** | `wscat -c wss://<ctrl>:8443/ws/metrics?token=<TOKEN>&channels=nodes,attacks,metrics` | WebSocket |

---

## 🎯 预设攻击场景（开箱即用）

| 场景 ID | 攻击向量 | 适用节点 | 默认强度 | 典型用途 |
|---------|---------|----------|----------|----------|
| `cc_attack` | HTTP Flood (CC) | HTTP | 2,000 RPS / 200 并发 | WAF、限流、应用层防御测试 |
| `syn_flood` | SYN Flood | RAW | 10,000 pps | SYN Cookie、防火墙、连接队列测试 |
| `slowloris` | Slowloris | HTTP | 500 并发 | 连接耗尽、Web 服务器抗压 |
| `udp_reflection` | UDP 反射放大 (NTP/DNS) | RAW | 5,000 pps | 反射放大、清洗设备吞吐测试 |
| `mixed_wave` | HTTP + SYN + Slowloris 混合 | 混合 | 自定义 | 多层联动防御、应急响应演练 |
| `ramp_up` | 渐进式压力 (100→10,000 RPS) | 所有 | 阶梯式 | 容量规划、熔断阈值验证 |

> 💡 **场景自定义**：运行时通过 `overrides` 参数覆盖目标 IP、RPS、时长等：
> ```json
> POST /api/v1/scenarios/cc_attack/run
> { "overrides": { "target": {"ip": "10.100.10.10"}, "rps": 5000, "duration": 120 } }
> ```

---

## 🔐 安全机制（强制生效，不可关闭）

| 机制 | 实现位置 | 关键参数 |
|------|---------|---------|
| **目标白名单** | Controller `TargetValidator` + Attacker `SafeAttackBase.pre_flight_check` | `ALLOWED_TARGET_CIDRS` (双层校验) |
| **节点认证** | mTLS 1.2+ 双向验证 + HMAC-SHA256 Token | `SHARED_SECRET` (32字节) |
| **全局熔断** | `Orchestrator.emergency_stop()` → `NodeCommander.broadcast_emergency_stop()` | `<100ms` 全网生效 |
| **三级限流** | 全局(RateLimiter) → 节点配额 → Worker令牌桶 | `GLOBAL_MAX_RPS=50000`, `GLOBAL_MAX_PPS=100000` |
| **全链路审计** | `AuditLogger` JSONL 轮转 + WebSocket 实时推送 | 保留 ≥90 天，ELK 就绪 |
| **最小权限** | Docker `cap_add: [NET_RAW]` / 非 root UID 1000 | HTTP节点零特权，RAW节点仅 NET_RAW |

---

## 📁 目录结构

```
ddos-attack-platform/
├── controller/                 # 🎮 指挥中心
│   ├── Dockerfile
│   ├── app/
│   │   ├── main.py            # FastAPI 入口、路由、生命周期
│   │   ├── auth.py            # mTLS + Token 双重认证
│   │   ├── orchestrator.py    # 编排核心：节点/攻击/场景/限流
│   │   ├── audit.py           # 审计事件流 (默认内存缓冲, 可选 JSONL 落盘)
│   │   ├── registry.py        # 攻击注册表 + 权威状态机 + TTL 清理 (v1.3)
│   │   ├── models.py          # Pydantic 数据模型
│   │   ├── websocket.py       # 多频道 WebSocket 推送
│   │   └── node_commander.py  # Controller→Attacker HTTP 下发
│   ├── ui/                    # Web 控制台 (Jinja2 + 原生 JS)
│   ├── docker-compose.yml
│   └── config.env.example
│
├── attacker/                   # ⚔️ 攻击节点
│   ├── Dockerfile
│   ├── Dockerfile.raw         # RAW 节点专用 (含 libpcap)
│   ├── app/
│   │   ├── main.py            # 节点主程序、生命周期
│   │   ├── health.py          # 资源采集 (CPU/内存/网络/连接数)
│   │   ├── crypto.py          # mTLS 客户端、Token 派生
│   │   └── attacks/
│   │       ├── base.py        # 安全基类: 熔断/限速/审计/2s进度上报 (v1.3)
│   │       ├── http_flood.py  # aiohttp 高并发 HTTP Flood
│   │       ├── syn_flood.py   # scapy SYN Flood (域名目标自动解析)
│   │       ├── slowloris.py   # Slowloris (异步 sock_sendall)
│   │       ├── udp_flood.py   # UDP Flood / 反射放大
│   │       └── __init__.py    # AttackRegistry 自动注册
│   ├── docker-compose.yml
│   ├── docker-compose.raw.yml
│   └── config.env.example
│
├── deploy/                     # 🚀 部署工具链
│   ├── generate_certs.sh      # mTLS 证书生成 (CA + Controller + N节点)
│   ├── generate-configs.sh    # 从 config.yaml 生成各节点 .env
│   ├── distribute-certs.sh    # SSH 分发证书+配置+二进制
│   ├── unified-deploy.sh      # 统一部署编排器 (Docker/Binary 混合)
│   ├── install.sh             # 单节点一键部署
│   ├── install-service.sh     # systemd 服务安装
│   ├── config.yaml            # 集群拓扑唯一真相源
│   ├── config.env.template    # 统一配置模板
│   └── systemd/               # systemd 服务单元
│
├── scenarios/                  # 📚 教学场景 (YAML)
│   ├── cc_attack.yaml
│   ├── syn_flood.yaml
│   ├── slowloris.yaml
│   ├── udp_reflection.yaml
│   ├── mixed_wave.yaml
│   └── ramp_up.yaml
│
├── docs/                       # 📖 文档
│   ├── SAFETY_RULES.md        # ⚠️ 必读：安全守则与法律免责
│   ├── ARCHITECTURE.md        # 架构设计文档
│   ├── API_REFERENCE.md       # REST API 完整参考
│   └── TEACHING_GUIDE.md      # 教学大纲/实验手册
│
├── monitor/                    # 📊 监控栈
│   ├── prometheus.yml
│   └── grafana/
│       ├── datasources.yml
│       ├── dashboards.yml
│       └── dashboards/ddos-overview.json
│
├── build/                      # 🔨 二进制构建
│   ├── build.py               # PyInstaller 打包脚本
│   ├── controller.spec        # Controller 打包规格
│   ├── attacker.spec          # Attacker 打包规格
│   └── requirements-build.txt
│
├── target/                     # 🎯 靶机配置
│   └── nginx.conf             # 简易测试用 Nginx
│
├── docker-compose.yml          # 单机全栈启动 (测试用)
├── Makefile                    # 常用命令封装
└── README.md                   # 本文件
```

---

## 🛠️ 常用运维命令

```bash
# === Makefile 封装 ===
make certs              # 生成 mTLS 证书
make docker-build       # 构建所有 Docker 镜像
make deploy-all         # 单机全栈部署
make binary             # 构建二进制
make binary-package     # 构建并打包发布

# === 统一部署 (生产环境推荐) ===
cd deploy
./unified-deploy.sh generate-configs   # 从 config.yaml 生成配置
./unified-deploy.sh generate-certs     # 生成证书
./unified-deploy.sh distribute         # 分发到各节点
./unified-deploy.sh deploy-all         # 一键部署全集群
./unified-deploy.sh status             # 查看集群健康
./unified-deploy.sh logs controller    # 查看日志
./unified-deploy.sh stop               # 停止全集群

# === 一键安装节点/控制器管理 (安装器自带, v1.2.3+) ===
# 无参数即状态; 支持短别名 (s=status, r=restart, l=最近日志, u=update)
# 变更类操作需要 root: sudo 缓存有效时可不加 sudo, 否则会提示加 sudo
ddos-controller            # 控制器状态: 版本/WebUI地址/在线节点数
sudo ddos-controller update     # ⭐ 升级控制器到最新 GitHub Release
ddos-controller logs       # 跟踪日志 (Ctrl-C 退出)
ddos-node                  # 节点状态: 进程/健康检查/节点ID
sudo ddos-node restart     # 重启节点服务

# === 手动 Docker 操作 ===
docker logs -f ddos-controller         # Controller 日志
docker logs -f ddos-attacker-http      # HTTP 节点日志
docker exec -it ddos-controller bash   # 进入容器

# === API 快速测试 ===
# 获取 Token: 从 config.env 的 SHARED_SECRET 派生
TOKEN=$(echo -n "ddos-controller-auth" | openssl dgst -sha256 -hmac "<SHARED_SECRET>" | awk '{print $2}')

# 查看节点
curl -k -H "Authorization: Bearer $TOKEN" https://<ctrl>:8443/api/v1/nodes

# 发起攻击 (v1.3: target.ip 支持 IP/域名; 响应含 status/started_at, 运行中计数实时更新)
curl -k -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  https://<ctrl>:8443/api/v1/attacks/launch \
  -d '{"attack_type":"http_flood","target":{"ip":"10.100.10.10","port":80},"duration":60,"rps":2000,"concurrency":200}'

# 紧急熔断
curl -k -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  https://<ctrl>:8443/api/v1/emergency_stop \
  -d '{"reason":"test emergency","issued_by":"admin"}'
```

---

## ⚠️ 重要提醒（红线）

1. **仅限授权内网** - 严禁对未书面授权目标发起攻击。v1.5.0 起**目标白名单默认开启**（fail-closed）:
   `ALLOWED_TARGET_CIDRS=10.100.0.0/16,192.168.0.0/16,172.16.0.0/12` 之外的 IP/CIDR/域名一律拒绝
   （域名解析后任一 A 记录命中白名单即放行）。需对白名单外目标演练时，**必须**显式
   `ALLOW_ANY_TARGET=true` 并在授权书中明确范围；越权攻击按 SAFETY_RULES 红线追责
2. **网络隔离** - 必须使用独立 VLAN/macvlan，实验网段无互联网路由
3. **流量镜像** - 生产验证请用流量镜像/旁路，禁止直接攻击核心链路
4. **法律合规** - 使用前确保符合《网络安全法》、《数据安全法》及单位制度
5. **应急预案** - 演练前必须制定回滚方案，确认物理断网开关、熔断按钮可达
6. **证书轮换** - CA 2年、节点证书 1年（`DAYS_VALID_CA/DAYS_VALID_NODE` 可调）；SHARED_SECRET 人工 90 天轮换（须全节点同步，平台开启 `REQUIRE_SHARED_SECRET=true` 拒绝弱密钥启动）
7. **审计留痕** - v1.3 起 WebUI 审计面板为**会话级内存缓冲（500 条，重启即清）**；
   需满足 ≥90 天留存的部署必须设置 `AUDIT_FILE_ENABLED=true` 启用 JSONL 落盘，
   并接入 ELK/Splunk 集中归档

---

## 📚 相关文档

| 文档 | 说明 | 优先级 |
|------|------|--------|
| [SAFETY_RULES.md](docs/SAFETY_RULES.md) | 安全守则、法律免责、签署页 | 🔴 **必读** |
| [CHANGELOG.md](docs/CHANGELOG.md) | 版本变更日志 (v1.0 → v1.4.1-hotfix6) | 🟡 重要 |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | 系统架构、通信协议、数据模型、扩展指南 | 🟡 重要 |
| [API_REFERENCE.md](docs/API_REFERENCE.md) | 完整 REST API / WebSocket 参考 | 🟡 重要 |
| [SECURITY.md](docs/SECURITY.md) | 漏洞披露流程、安全策略 | 🟡 重要 |
| [DEEP_EVALUATION_v3.md](docs/DEEP_EVALUATION_v3.md) | 深度评估报告 (技术债 0/2/24) | 🟡 重要 |
| [CONTRIBUTING.md](docs/CONTRIBUTING.md) | 贡献指南 (commit/PR/release 流程) | 🟢 参考 |
| [TEACHING_GUIDE.md](docs/TEACHING_GUIDE.md) | 教学大纲、实验手册、评估标准 | 🟢 参考 |
| [MIXED_DEPLOY.md](docs/MIXED_DEPLOY.md) | 混合部署指南 (Docker + 二进制) | 🟢 参考 |
| [REGRESSION_REPORT_v1.4.0.md](docs/REGRESSION_REPORT_v1.4.0.md) | v1.4.0 回归测试报告 | 🟢 参考 |

---

## 📄 许可证

**内部教学专用**  
- ❌ 禁止外传  
- ❌ 禁止商用  
- ❌ 禁止用于非授权测试  
- ✅ 允许授权团队内部教学、演练、红蓝对抗

> **版权所有 © 2024 内部安全团队**  
> 未经书面授权，不得复制、修改、分发、公开展示或创作衍生作品。