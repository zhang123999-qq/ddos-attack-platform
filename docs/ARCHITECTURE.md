# DDoS Attack Platform — 架构设计文档

[![Version](https://img.shields.io/badge/version-1.1-blue.svg)]()
[![Status](https://img.shields.io/badge/status-Production%20Ready-green.svg)]()
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows-lightgrey.svg)]()

> **文档版本**: v1.1  
> **适用平台**: DDoS Attack Platform v1.1+  
> **编写日期**: 2024-01-15  
> **最近更新**: 2024-12-19  
> **评审者**: 架构组、安全组、运维组  
> **文档密级**: 内部机密

---

## ⚖️ 法律免责声明

> **重要提醒**：本架构文档描述的系统设计仅供授权内网教学/演练参考。实际部署使用前，必须：
> - ✅ 完整阅读并签署 [安全守则](docs/SAFETY_RULES.md)
> - ✅ 获得目标网络书面授权
> - ✅ 确保网络环境物理/逻辑隔离
> - ❌ 严禁将架构设计用于非授权攻击场景

---

## 1. 系统概览

### 1.1 设计目标

| 目标 | 描述 | 实现方式 |
|------|------|----------|
| **分布式编排** | 单 Controller 管理多 Attacker 节点 | REST API + WebSocket + 节点注册表 |
| **安全可控** | 零信任通信、紧急熔断、全链路审计、目标流程管控 | mTLS 1.2+、HMAC-SHA256、令牌桶三级限流（v1.3: 目标白名单技术强制移除，约束转授权流程） |
| **教学友好** | 预设场景、实时可视化、标准化评估 | 6 大内置场景、Web UI、Grafana 仪表盘 |
| **生产隔离** | 容器化部署、网络隔离、最小权限原则 | Docker、VLAN/macvlan、CAP_NET_RAW 仅 RAW 节点 |

### 1.2 核心组件架构

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Controller (管理平面)                            │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌────────────────────┐  │
│  │   REST API   │ │  WebSocket   │ │ Orchestrator │ │   Audit Logger     │  │
│  │  (管理接口)   │ │  (实时推送)   │ │  (编排核心)   │ │  (结构化审计)       │  │
│  └──────────────┘ └──────────────┘ └──────────────┘ └────────────────────┘  │
│         │                │                │                    │              │
│         └────────────────┴────────────────┴────────────────────┘              │
│                                  │                                             │
│                    ┌─────────────┴─────────────┐                               │
│                    │  HTTPS/HMAC Token 双向认证   │                               │
│                    │    + HMAC-SHA256 Token     │                               │
│                    └─────────────┬─────────────┘                               │
└─────────────────────────────────│───────────────────────────────────────────────┘
                                   │
         ┌─────────────────────────┼─────────────────────────┐
         ▼                         ▼                         ▼
┌───────────────┐         ┌───────────────┐         ┌───────────────┐
│  Attacker     │         │  Attacker     │         │  Attacker     │
│  HTTP (L7)    │         │  RAW (L4)     │         │  HTTP (L7)    │
│  http_flood   │         │  syn_flood    │         │  http_flood   │
│  slowloris    │         │  udp_flood    │         │  slowloris    │
│  无特权       │         │  CAP_NET_RAW  │         │  无特权       │
└───────────────┘         └───────────────┘         └───────────────┘
         │                         │                         │
         └─────────────────────────┼─────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Target Network (数据平面)                            │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  被测防御系统：WAF / 下一代防火墙 / DDoS清洗设备 / 应用网关 / 负载均衡  │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 通信协议

### 2.1 Controller ↔ Attacker (REST + HMAC Token)

**认证流程**：

```
1. TLS 1.2+ 握手：双向验证证书 (CA 签发，SubjectAltName 含 IP/DNS)
2. HTTP Header 认证：X-Node-ID + X-Node-Token (HMAC-SHA256)
3. Controller 验证 Token 有效性 (共享密钥派生)
4. 建立长连接，Attacker 轮询/推送接收指令
```

**API 端点对照表**：

| 方向 | 端点 | 说明 | 认证 |
|------|------|------|------|
| Node → Ctrl | `POST /api/v1/nodes/register` | 节点注册 | HTTPS + Node Token (HMAC) |
| Node → Ctrl | `POST /api/v1/nodes/heartbeat` | 心跳上报 (默认 10s) | HTTPS + Node Token (HMAC) |
| Node → Ctrl | `POST /api/v1/results` | 攻击结果上报 | HTTPS + Node Token (HMAC) |
| Node → Ctrl | `POST /api/v1/nodes/unregister` | 节点优雅注销 (v1.1) | HTTPS + Node Token (HMAC) |
| Ctrl → Node | `POST /api/v1/attacks/execute` | 下发攻击指令 | HTTP + Controller Cmd Token (HMAC) |
| Ctrl → Node | `POST /api/v1/attacks/{id}/stop` | 停止指令 | HTTP + Controller Cmd Token (HMAC) |
| Ctrl → Node | `POST /api/v1/emergency_stop` | 熔断广播 | HTTP + Controller Cmd Token (HMAC) |
| Ctrl → Node | `POST /api/v1/emergency_stop/reset` | 熔断复位广播 (v1.1) | HTTP + Controller Cmd Token (HMAC) |
| Node → Ctrl | `POST /api/v1/nodes/enroll` | 节点自助接入换配置 (v1.2, enroll token 认证) | 无状态 Enroll Token |
| Ops → Ctrl | `GET /api/v1/nodes/enroll-command` | 生成节点安装命令 (v1.2) | Bearer Controller Token |
| Node → Ctrl | `GET /install.sh` `/artifacts/*` `/api/v1/controller-info` | 安装脚本/CA/制品分发 (v1.2) | 公开 |

> **身份一致性校验 (v1.1)**：register / heartbeat / unregister / results 均校验
> 请求体中的 `node_id` 与认证派生的节点身份一致，不一致返回 403，防止跨节点伪造。

### 2.2 Controller ↔ Console (WebSocket)

**连接地址**：
```
wss://<controller>:8443/ws/metrics?token=<CTRL_TOKEN>&channels=nodes,attacks,metrics,alerts,system
```

**消息格式**：
```json
{
  "type": "node_update|attack_update|metric|alert|audit|system",
  "timestamp": "2024-01-15T10:30:00Z",
  "data": { ... }
}
```

**频道订阅表**：

| 频道 | 内容 | 推送频率 | 典型用途 |
|------|------|----------|----------|
| `nodes` | 节点注册/离线/状态变更 | 事件驱动 | 节点拓扑监控 |
| `attacks` | 攻击启动/更新/停止 | 事件驱动 | 实时攻击态势 |
| `metrics` | 节点心跳、CPU/内存/网络、限流状态 | ~1 Hz | 资源监控大屏 |
| `alerts` | 熔断、异常、阈值触发 | 事件驱动 | 告警中心 |
| `audit` | 审计日志流 | 事件驱动 | 合规审计 |
| `system` | 系统启停、配置变更 | 事件驱动 | 运维感知 |

---

## 3. 核心数据模型

### 3.1 攻击指令 (AttackCommand)

```json
{
  "attack_id": "atk-a1b2c3d4",
  "attack_type": "http_flood",
  "params": {
    "target": {"ip": "10.100.10.10", "port": 80, "protocol": "tcp", "path": "/api"},
    "duration": 60,
    "rps": 2000,
    "concurrency": 200,
    "method": "POST",
    "headers": {"Content-Type": "application/json"},
    "body": "{\"query\":\"test\"}",
    "use_https": false,
    "verify_ssl": false,
    "source_ip_spoof": false,
    "spoof_cidr": "10.0.0.0/8",
    "interface": "eth0",
    "slowloris_interval": 15,
    "reflector_type": "ntp",
    "reflector_list": ["1.2.3.4:123", "5.6.7.8:123"]
  },
  "scenario_id": "cc_attack",
  "node_ids": ["attacker-http-01"],
  "priority": 0,
  "created_at": "2024-01-15T10:30:00Z"
}
```

### 3.2 节点信息 (NodeInfo)

```json
{
  "node_id": "attacker-http-01",
  "node_type": "http",
  "supported_attacks": ["http_flood", "slowloris"],
  "ip": "10.100.1.20",
  "hostname": "attacker-http-01",
  "cpu_cores": 8,
  "memory_gb": 16.0,
  "network_interfaces": ["eth0:10.100.1.20"],
  "max_rps": 10000,
  "max_pps": 50000,
  "max_concurrent": 5000,
  "status": "online",
  "last_heartbeat": "2024-01-15T10:30:00Z",
  "registered_at": "2024-01-15T10:00:00Z",
  "labels": {"role": "http-attacker", "zone": "dmz"}
}
```

### 3.3 审计事件 (AuditEvent)

```json
{
  "event_id": "audit-20240115103000-a1b2",
  "event_type": "attack_start",
  "timestamp": "2024-01-15T10:30:00Z",
  "actor": "instructor",
  "attack_id": "atk-a1b2c3d4",
  "node_id": "attacker-http-01",
  "scenario_id": "cc_attack",
  "details": {"attack_type": "http_flood", "target": "10.100.10.10", "rps": 2000},
  "success": true,
  "error_message": null
}
```

**审计事件类型枚举**：
- `attack_start` / `attack_stop` / `attack_complete` / `attack_failed`
- `emergency_stop` / `node_register` / `node_heartbeat` / `node_disconnect`
- `config_change` / `auth_failure` / `target_validation_failure`

---

## 4. 安全架构

### 4.1 信任边界 (三平面隔离)

```
┌────────────────────────────────────────────────────────────┐
│                    管理平面 (Controller)                      │
│  职责：场景编排、熔断决策、审计归档、配额分配、节点生命周期     │
│  权限：读写所有资源、广播熔断、查看完整审计                   │
└────────────────────────────┬─────────────────────────────────┘
                              │ TLS + HMAC-SHA256 Token
                              ▼
┌────────────────────────────────────────────────────────────┐
│                    控制平面 (Attacker Nodes)                  │
│  职责：指令执行、本地限流执行、结果采集上报、健康上报、          │
│        运行期 2s 周期进度快照 (v1.3)                          │
│  权限：仅执行预注册攻击类型、不可修改配置、无文件/Shell 访问    │
└────────────────────────────┬─────────────────────────────────┘
                              │ 目标约束为流程管控 (v1.3 起无技术白名单)
                              ▼
┌────────────────────────────────────────────────────────────┐
│                    数据平面 (Target Network)                  │
│  角色：被测防御体系、靶机业务、流量镜像源                      │
│  隔离：物理/逻辑 VLAN、无互联网路由、旁路/镜像部署            │
└────────────────────────────────────────────────────────────┘
```

### 4.2 纵深防御措施矩阵

| 层级 | 措施 | 实现位置 | 验证方式 |
|------|------|----------|----------|
| **网络层** | VLAN 物理隔离、防火墙微隔离、无互联网路由 | 交换机/路由器/防火墙 | 网络拓扑图、ACL 审计 |
| **传输层** | mTLS 1.2+、双向验证、证书轮换(1-2年)、CRL/OCSP | Controller/Node 通信层 | `openssl verify`、证书指纹核对 |
| **应用层** | HMAC-SHA256 Token、攻击类型白名单 (AttackRegistry 预注册)、参数合法性检查、场景占位符拒绝 | Controller Orchestrator + Attacker SafeAttackBase | 单测覆盖、渗透测试 |
| **运行层** | 非 root (UID 1000)、最小 Capability (CAP_NET_RAW 仅 RAW 节点)、只读根文件系统 | Docker 容器、systemd | `docker inspect`、能力审计 |
| **业务层** | 三级令牌桶限流、熔断状态机、攻击超时自动停止 | Orchestrator RateLimiter | 压测验证、混沌工程 |
| **审计层** | 结构化事件流 (v1.3 默认会话级内存缓冲 500 条；`AUDIT_FILE_ENABLED=true` 时 JSONL 落盘 + 轮转)、ELK 接入 | AuditLogger + Filebeat | 日志采样、哈希校验 |

### 4.3 实时反馈链路 (v1.3)

```
节点攻击线程 ──每 2s──> AttackResult(status=RUNNING, metrics={"snapshot":true})
                              │ POST /api/v1/results (现有上报通道复用)
                              ▼
        Controller AttackExecutor.collect_result
          ├─ 单调合并: total/successful/failed/bytes 按字段取最大值 (防快照回退)
          ├─ 权威状态机: _attack_meta[attack_id] = {status, started_at, finished_at, stop_reason}
          │    launching → running → (stopping) → stopped/completed/failed/emergency_stopped
          ├─ 终态判定: 全部预期节点上报终态, 或 prune 循环对超时僵尸 (duration+120s) 兜底收尾
          └─ 结果表 TTL 清理: 已结束攻击保留 60 分钟后移除 (内存有界)
                              │ WebSocket attacks 频道推送
                              ▼
                     WebUI mergeResult() 合并 + 1s 秒级重绘
                       进度条 / 行内错误摘要 error_counts / 停止·清除按钮
```

---

## 5. 限流与熔断设计

### 5.1 三级令牌桶限流架构

```
┌─────────────────────────────────────────────────────────────────┐
│                     全局配额 (Controller 聚合)                     │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │  HTTP 类攻击:  GLOBAL_MAX_RPS = 50,000 req/s                 │  │
│  │  RAW 类攻击:   GLOBAL_MAX_PPS = 100,000 pkt/s                │  │
│  │  连接数总控:   GLOBAL_MAX_CONCURRENT = 100,000               │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                              │                                     │
│              动态配额分配 (RateLimiter.allocate)                   │
│                              ▼                                     │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │                    节点配额 (每节点动态)                       │  │
│  │  per-node RPS/PPS/Concurrent = min(请求, 剩余全局)            │  │
│  │  释放: 攻击停止/熔断/节点离线 → RateLimiter.release()        │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                              │                                     │
│                              ▼                                     │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │                  Worker 级精确控速 (TokenBucket)              │  │
│  │  rate = 节点配额 / 并发数  |  burst = min(rate * 2, 10000)   │  │
│  │  await wait_for_token() → 发包 → 统计成功/失败                │  │
│  └─────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 5.2 熔断状态机

```
                    ┌────────────────────┐
                    │      NORMAL        │ ◄──────────────────┐
                    │  (正常编排攻击)      │                    │
                    └─────────┬──────────┘                    │
                              │ 触发条件                       │
               ┌───────────────┼───────────────┐               │
               ▼               ▼               ▼               │
     ┌─────────────────┐ ┌───────────┐ ┌──────────────┐       │
     │ 管理员手动触发   │ │ 全局配额  │ │ 审计写入失败  │       │
     │ POST /emergency │ │ 耗尽      │ │ (关键路径)    │       │
     └────────┬────────┘ └─────┬─────┘ └──────┬───────┘       │
              │               │               │                │
              └───────────────┼───────────────┘                │
                              ▼                                 │
                     ┌────────────────────┐                     │
                     │  EMERGENCY_STOP    │                     │
                     │  (全网熔断态)       │                     │
                     │  - 拒绝新攻击       │                     │
                     │  - 广播停止 <100ms  │                     │
                     │  - 强制终止进行中   │                     │
                     │  - 释放所有配额     │                     │
                     └─────────┬──────────┘                     │
                               │ 管理员复位                      │
                               │ POST /emergency_stop/reset      │
                               └────────────────────────────────┘
```

**触发条件与响应 SLA**：

| 触发条件 | 检测方式 | 响应时间 | 恢复方式 |
|---------|---------|---------|---------|
| 管理员手动 | `POST /api/v1/emergency_stop` | <50ms | 管理员 `POST /emergency_stop/reset` |
| 全局配额耗尽 | `RateLimiter.allocate` 返回 `QuotaExhaustedError` | 即时 | 配额释放后自动恢复接受新攻击 |
| 节点大规模离线 | `NodeRegistry._cleanup_loop` 检测 >50% 离线 | 90s 后 | 节点重新注册心跳恢复 |
| 审计写入失败 | `AuditLogger._writer_loop` 连续失败 | 即时 | 磁盘恢复/队列排空后自动重试 |

---

## 6. 部署架构

### 6.1 单机测试模式 (开发/PoC)

```yaml
# docker-compose.yml (项目根目录)
services:
  controller:
    build: ./controller
    ports: ["8443:8443"]
    volumes:
      - ./certs:/certs:ro
      - ./scenarios:/app/scenarios:ro

  attacker-http:
    build: ./attacker
    ports: ["8080:8080"]
    volumes:
      - ./certs:/certs:ro
    depends_on:
      controller:
        condition: service_healthy

  attacker-raw:
    build: ./attacker
    cap_add: [NET_RAW]          # 推荐而非 privileged: true
    # network_mode: "host"       # 如需发送伪造源 IP 包到外部网络
    ports: ["8081:8080"]
    depends_on:
      controller:
        condition: service_healthy
```

### 6.2 多机生产模式 (推荐拓扑)

```
管理网络 (10.100.1.0/24)          攻击网络 A (10.100.10.0/24)      攻击网络 B (10.100.20.0/24)     靶机网络 (10.100.30.0/24)
┌─────────────────────┐           ┌─────────────────────┐          ┌─────────────────────┐        ┌─────────────────────┐
│  Controller         │◄──mTLS──►│  Attacker-HTTP-01   │          │  Attacker-RAW-01    │        │  Target WAF/App     │
│  10.100.1.10        │   8443    │  10.100.1.20        │          │  10.100.1.21        │        │  10.100.30.10       │
│  :8443 (API/WS)     │           │  :8080 (HTTP)       │          │  :8080 (HTTP)       │        │  :80/443            │
│                     │           │  http_flood         │          │  syn_flood          │        │                     │
│  - 场景编排          │           │  slowloris          │          │  udp_flood          │        │  - 被测防御系统      │
│  - 限流/熔断         │           │  无特权             │          │  udp_reflection     │        │  - 旁路/镜像部署      │
│  - 审计归档          │           │                     │          │  CAP_NET_RAW        │        │                     │
└─────────────────────┘           └─────────────────────┘          └─────────────────────┘        └─────────────────────┘
```

**网络隔离要求**：
- 管理网络 ↔ 攻击网络：仅允许 mTLS 8443
- 攻击网络 → 靶机网络：仅允许攻击端口 (80/443/自定义)
- 靶机网络 ↛ 管理/攻击网络：无反向路由
- 所有网段无互联网路由

### 6.3 证书分发结构

```
certs/
├── ca-cert.pem                 # 所有节点信任的根证书
├── controller-cert.pem         # Controller 服务端证书
├── controller-key.pem          # Controller 私钥 (600)
└── nodes/
    ├── attacker-http-01/
    │   ├── ca-cert.pem         # CA 证书副本
    │   ├── node-cert.pem       # 节点客户端证书
    │   └── node-key.pem        # 节点私钥 (600)
    └── attacker-raw-01/
        ├── ca-cert.pem
        ├── node-cert.pem
        └── node-key.pem
```

**分发命令**：`./deploy/distribute-certs.sh` (基于 `config.yaml` 自动 SSH 分发)

> **拉取式替代 (v1.2)**：一键安装模式下节点无需预配证书——控制器安装器现场生成
> 自签证书 (SAN=本机 IP)，节点经 `/artifacts/ca-cert.pem` 引导信任链 + 指纹交叉校验，
> 再以 enroll token 在受验证 TLS 信道换取运行配置。见 README「方式〇」。

---

## 7. 扩展指南

### 7.1 新增攻击类型 (6 步)

1. **创建实现文件**：`attacker/app/attacks/new_attack.py`
2. **继承基类**：
   ```python
   class NewAttack(SafeAttackBase):
       NAME = "new_attack"
       ATTACK_TYPE = AttackType.NEW_ATTACK  # 需先在 models.py 中定义枚举
       REQUIRES_ROOT = False  # 或 True
       DEFAULT_RPS = 1000
       DEFAULT_CONCURRENCY = 100
       
       async def _run(self):
           # 实现攻击逻辑，使用 self._rate_limited_loop() 受控发包
           pass
   ```
3. **注册枚举**：在 `controller/app/models.py` 和 `attacker/app/models.py` 的 `AttackType` 中添加
4. **触发注册**：在 `attacker/app/attacks/__init__.py` 中 `from app.attacks import new_attack`
5. **添加场景**：在 `scenarios/new_attack.yaml` 定义教学场景
6. **更新文档**：同步更新 `ARCHITECTURE.md`、`API_REFERENCE.md`、`TEACHING_GUIDE.md`

### 7.2 新增防御指标采集 (4 步)

1. `attacker/app/health.py` 添加采集逻辑 (如 `psutil.disk_io_counters()`)
2. `controller/app/models.py` 的 `NodeHeartbeat` 模型添加字段
3. `controller/app/websocket.py` 的 `broadcast_node_heartbeat` 透传新字段
4. `monitor/grafana/dashboards/ddos-overview.json` 添加面板

### 7.3 对接外部系统

| 外部系统 | 接口方式 | 数据流向 | 关键字段 |
|---------|---------|---------|---------|
| **SIEM** (ELK/Splunk) | Filebeat → Logstash → ES | 审计日志 JSONL 实时推送 | `event_id`, `event_type`, `timestamp`, `actor`, `attack_id`, `success` |
| **CMDB** (资产管理) | Controller 定时任务 `GET /api/v1/nodes` | 节点资产同步 | `node_id`, `ip`, `hostname`, `cpu_cores`, `memory_gb`, `labels` |
| **工单系统** (JIRA/钉钉/企微) | Webhook: `POST /webhook/emergency` | 熔断事件实时通知 | `event_type=emergency_stop`, `reason`, `issued_by`, `affected_nodes` |
| **漏扫/资产扫描** | 扫描完成回调 `PATCH /api/v1/nodes/{id}/labels` | 靶机标签更新 | `labels.vuln_level`, `labels.exploitable` |

---

## 8. 性能基准 (参考值)

### 8.1 单节点极限测试环境

| 攻击类型 | 测试硬件 | 网络 | 理论峰值 | 实测峰值 | 瓶颈分析 |
|----------|---------|------|----------|----------|----------|
| **HTTP Flood** | 8C16G, 虚拟机 | 千兆 vNIC | 50k RPS | ~35k RPS | aiohttp 连接池、CPU 上下文切换 |
| **Slowloris** | 8C16G | 千兆 | 5k 并发 | ~3k 并发 | 文件描述符限制、内核 TCP 缓冲区 |
| **SYN Flood** | 8C16G, 物理机 | 万兆 | 500k pps | ~200k pps | scapy Python GIL、单核发包 |
| **UDP Flood** | 8C16G, 物理机 | 万兆 | 1M pps | ~400k pps | 同 SYN Flood、内存带宽 |
| **UDP Reflection** | 8C16G | 万兆 | 视反射比 | ~300k pps | 反射源响应率、带宽放大比 |

> 💡 **优化建议**：
> - RAW 类攻击建议物理机部署，关闭 Hyper-Threading，绑定 CPU 核心 (`taskset -c 0-3`)
> - 多线程发包受限于 Python GIL，极限场景可考虑 Rust/Go 重写发包模块

### 8.2 水平扩展策略

| 组件 | 扩展方式 | 状态共享 | 扩展建议 |
|------|---------|---------|---------|
| **Controller** | 多实例 + Redis | 必须 (节点注册表、攻击状态、配额) | 每 10 个 Attacker 配 1 个 Controller 实例 |
| **Attacker-HTTP** | 无状态横向扩展 | 无 (仅心跳上报) | 线性增长，负载均衡由 Controller 调度 |
| **Attacker-RAW** | 无状态横向扩展 | 无 | 需独占网卡/物理机，避免资源争用 |

**Redis 共享状态方案** (v1.2 规划)：
- 节点注册表：`Redis Hash: ddos:nodes:{node_id}`
- 活跃攻击：`Redis Hash: ddos:attacks:{attack_id}`
- 全局配额：`Redis Sorted Set: ddos:quotas` (分布式令牌桶)

---

## 9. 运维与观测

### 9.1 关键监控指标 (SLO)

| 指标 | 正常范围 | 告警阈值 | 严重阈值 |
|------|---------|---------|---------|
| Controller 存活 | 1 | < 1 (30s) | < 1 (60s) |
| 节点在线率 | 100% | < 90% | < 50% (触发自动熔断) |
| 心跳延迟 | < 500ms | > 2s | > 5s |
| 攻击指令下发成功率 | 100% | < 99% | < 95% |
| 审计事件流延迟 | < 10ms | > 100ms | > 1s (触发熔断)；落盘模式需 `AUDIT_FILE_ENABLED=true` |
| 证书过期剩余天数 | > 30天 | ≤ 30天 | ≤ 7天 |

### 9.2 Grafana 仪表盘 (预置)

- **DDoS Overview**: 集群总览 (节点数、攻击数、聚合 RPS/PPS、熔断状态)
- **Node Details**: 单节点资源 (CPU/内存/网络/连接数/攻击明细)
- **Attack Analysis**: 攻击时序、成功率、延迟分布、字节统计
- **Rate Limits**: 全局/节点配额使用率、配额分配历史
- **Audit Trail**: 审计日志流、操作审计、合规报表

---

## 10. 版本历史

| 版本 | 日期 | 变更摘要 | 影响范围 |
|------|------|---------|---------|| 版本 | 日期 | 变更摘要 | 影响范围 |
|------|------|----------|----------|
| v1.3.2 | 2025-08-25 | 方案 A/B/C：目标域名/IP 无限制（白名单技术强制移除，占位符守卫保留，scapy getaddrinfo 解析）；攻击日志默认不落盘（内存环形缓冲 500 条 + AUDIT_FILE_ENABLED 开关）；结果表 60min TTL 清理 + 僵尸兜底；权威状态机 + 节点 2s 周期快照上报 + WebUI mergeResult 秒级重绘 + 行内错误聚合摘要 + 停止/清除操作列；安装器移除白名单询问 | 全栈 |
| v1.2 | 2024-12-20 | 一键安装体系（控制器交互式安装器 + 节点拉取式自助接入：无状态 enroll token、CA/制品分发、WebUI 命令生成）、部署脚本安全加固（SSH accept-new、eval 注入封堵、密钥强制校验）、REQUIRE_SHARED_SECRET | 部署链 + 控制器 API |
| v1.1 | 2024-12-19 | Controller↔Attacker 实时 HTTP 指令下发、二进制部署、审计修复、安全加固、datetime 序列化修复 | 全栈 |
| v1.0 | 2024-01-15 | 初始版本：基础编排、5 种攻击、mTLS、WebSocket、Docker 部署 | 全栈 |

---

## 11. 文档控制

- 修改需经**架构组评审**、**安全组批准**，版本号语义化递增
- 所有历史版本 Git 永久保留，接受审计溯源
- 电子版：`docs/ARCHITECTURE.md` (Git 版本控制)
- 变更通知：通过内部邮件列表 `sec-arch@internal` 发布