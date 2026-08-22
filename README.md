# DDoS Attack Platform v1.1 — 内网红方攻击演练平台

> **仅供授权内网网络安全团队教学/演练使用**  
> 部署前请阅读 [SAFETY_RULES.md](docs/SAFETY_RULES.md) 并签署授权确认书

> **v1.1 更新**: Controller↔Attacker 实时 HTTP 指令下发、二进制部署、审计修复、安全加固

## 🚀 快速开始（3 选 1）

### 方式 1：Docker 部署
```bash
docker-compose up -d --build
```

### 方式 2：源码运行
```bash
cd controller && pip install -r requirements.txt && python -m app.main
cd attacker   && pip install -r requirements.txt && python -m app.main
```

### 方式 3：二进制部署（无需 Python）
```bash
# 构建二进制
cd build && pip install -r requirements-build.txt && python build.py all
# 产物在 dist/controller/ 和 dist/attacker/
# 部署到目标机器
tar -xzf dist/ddos-attack-platform-linux.tar.gz
cd controller && ./start.sh
cd attacker   && ./start.sh

# Windows
cd controller && start.bat
cd attacker   && start.bat
```

## 🏗️ 架构概览

```
内网环境 (例: 10.100.0.0/16)
├── 🎮 Controller (10.100.1.10)     # 指挥中心：场景编排、实时控制、审计日志
│   ├── REST API + WebSocket
│   ├── mTLS 双向认证
│   ├── 场景预设 (CC/SYN/Slowloris/混合波)
│   └── 紧急熔断开关
│
├── ⚔️ Attacker-1 (10.100.1.20)     # 攻击节点：HTTP/Slowloris/应用层
├── ⚔️ Attacker-2 (10.100.1.21)     # 攻击节点：SYN/UDP/传输层 (需 --cap-add=NET_RAW)
├── ⚔️ Attacker-N ...               # 横向扩展
│
└── 🎯 Target Network               # 你们的防御体系/靶机网段
```

## ⚡ 快速开始

### 1. 控制器部署 (1台机器)
```bash
cd controller
cp config.env.example config.env
# 编辑: CONTROLLER_IP=10.100.1.10, ALLOWED_TARGET_CIDRS="10.100.0.0/16"
./generate_certs.sh
docker-compose up -d
```

### 2. 攻击节点部署 (N台机器)
```bash
# HTTP/Slowloris 节点 (普通权限)
cd attacker
cp config.env.example config.env
# 编辑: CONTROLLER_URL=https://10.100.1.10:8443, NODE_ID=attacker-http-01
#       ATTACK_TYPES=http_flood,slowloris
docker-compose up -d

# SYN/UDP 节点 (需 root 权限)
# 编辑: ATTACK_TYPES=syn_flood,udp_flood
# docker-compose.yml 中启用: privileged: true / cap_add: NET_RAW
docker-compose up -d
```

### 3. 访问控制台
- **Web UI**: https://10.100.1.10:8443 (自签证书，浏览器信任或用 curl)
- **API**: `curl -k -H "Authorization: Bearer <TOKEN>" https://10.100.1.10:8443/api/v1/...`
- **实时指标**: `wscat -c wss://10.100.1.10:8443/ws/metrics?token=<TOKEN>`

## 🎯 预设攻击场景

| 场景 ID | 说明 | 适用节点 | 默认强度 |
|---------|------|----------|----------|
| `cc_attack` | HTTP Flood (CC攻击) | HTTP节点 | 5000 RPS |
| `syn_flood` | SYN Flood | RAW节点 | 10000 pps |
| `slowloris` | Slowloris 慢速攻击 | HTTP节点 | 500 并发 |
| `udp_reflection` | UDP 反射放大 (NTP/DNS) | RAW节点 | 5000 pps |
| `mixed_wave` | 混合波: HTTP+SYN+Slowloris | 混合部署 | 自定义 |
| `ramp_up` | 渐进式压力测试 | 所有 | 100→10000 RPS |

## 🔐 安全机制 (强制生效)

| 机制 | 实现 |
|------|------|
| **目标白名单** | 仅允许攻击 `ALLOWED_TARGET_CIDRS` 内目标 |
| **节点认证** | mTLS 双向认证 + 预共享 Token |
| **全局熔断** | Controller 广播 `EMERGENCY_STOP`，所有节点 <100ms 停止 |
| **速率总控** | Controller 下发令牌桶配额，聚合不超上限 |
| **全链路审计** | 所有指令/执行/异常结构化日志 (JSONL + ELK) |
| **最小权限** | HTTP节点无特权；RAW节点仅 `CAP_NET_RAW` |

## 📁 目录结构

```
ddos-attack-platform/
├── controller/                 # 指挥中心
│   ├── Dockerfile
│   ├── app/
│   │   ├── main.py            # FastAPI 入口
│   │   ├── auth.py            # mTLS + Token 验证
│   │   ├── orchestrator.py    # 场景编排、攻击调度
│   │   ├── audit.py           # 审计日志
│   │   ├── models.py          # Pydantic 模型
│   │   ├── websocket.py       # 实时指标推送
│   │   └── scenarios/         # 预设场景定义
│   ├── ui/                    # 简易 Web 控制台 (可选)
│   ├── docker-compose.yml
│   └── config.env.example
│
├── attacker/                   # 攻击节点
│   ├── Dockerfile
│   ├── app/
│   │   ├── main.py            # 节点主程序
│   │   ├── node.py            # 注册、心跳、指令接收
│   │   ├── attacks/
│   │   │   ├── base.py        # 安全基类 (白名单/熔断/限速)
│   │   │   ├── http_flood.py  # aiohttp 高并发 HTTP Flood
│   │   │   ├── syn_flood.py   # scapy SYN Flood
│   │   │   ├── slowloris.py   # Slowloris 慢速攻击
│   │   │   ├── udp_flood.py   # UDP Flood/反射
│   │   │   └── registry.py    # 攻击类型注册表
│   │   ├── health.py          # 资源上报 (CPU/内存/网络/连接数)
│   │   └── crypto.py          # mTLS 客户端
│   ├── docker-compose.yml
│   └── config.env.example
│
├── deploy/                     # 部署工具
│   ├── generate_certs.sh      # mTLS 证书生成
│   ├── install.sh             # 一键部署 (SSH 分发)
│   └── config.env.template    # 统一配置模板
│
├── scenarios/                  # 教学场景 (YAML)
│   ├── cc_attack.yaml
│   ├── syn_flood.yaml
│   ├── slowloris.yaml
│   ├── udp_reflection.yaml
│   ├── mixed_wave.yaml
│   └── ramp_up.yaml
│
├── docs/
│   ├── SAFETY_RULES.md        # 必读安全守则
│   ├── ARCHITECTURE.md
│   ├── API_REFERENCE.md
│   └── TEACHING_GUIDE.md      # 教学大纲/实验手册
│
└── docker-compose.yml         # 全栈启动 (单机测试用)
```

## ⚠️ 重要提醒

1. **仅限授权内网** - 严禁对未授权目标发起攻击
2. **网络隔离** - 建议使用独立 VLAN 或 macvlan 网络
3. **流量镜像** - 生产网段建议配置流量镜像到靶机，而非直接攻击
4. **法律合规** - 使用前确保符合当地法律法规及单位安全管理制度
5. **应急预案** - 演练前制定回滚方案，确认熔断开关可达

## 📄 许可证

内部教学专用，禁止外传、禁止商用、禁止用于非授权测试。