# DDoS Attack Platform — API 参考文档 v1.5.0

[![Version](https://img.shields.io/badge/version-1.5.0-blue.svg)]()
[![Base URL](https://img.shields.io/badge/base%20url-%2Fapi%2Fv1-green.svg)]()
[![Auth](https://img.shields.io/badge/auth-Bearer%20%7C%20mTLS-orange.svg)]()

> **文档版本**: v1.5.0  
> **首次编写**: 2026-08-22  
> **最近更新**: 2026-09-02  
> **维护者**: API 工作组  
> **文档密级**: 内部机密

> **基础 URL**: `https://<controller-host>:8443/api/v1`  
> **认证方式**: 
> - Controller API → `Authorization: Bearer <TOKEN>` (HMAC 派生)
> - Node→Controller 上报 → **HTTPS + mTLS** (v1.5.0+ 强制, 缺证书 fail-closed) + `X-Node-ID` + `X-Node-Token` (HMAC 派生)
> - Controller→Node 下发 → **HTTPS + mTLS** (v1.5.0+ 强制) + `X-Node-ID` + `X-Node-Token` (Cmd Token, HMAC 派生)
> - WebSocket `/ws/metrics` → URL `?token=` (旧) 或首消息 `{"type":"auth","token":"..."}` (新, 推荐)
> - Controller `/metrics` → 公开 (内部监控专用, 应在内网/VLAN 部署)
> 
> **内容类型**: `application/json`  
> **字符编码**: UTF-8  
> **日期格式**: ISO 8601 (UTC), 如 `2026-09-01T10:30:00Z`  
> **当前版本**: v1.5.0

---

## ⚖️ 免责声明

本 API 文档仅供授权内网教学/演练使用。调用攻击类接口前，请确认：
- ✅ 已获得目标网络书面授权
- ✅ 目标 IP/域名与授权书范围一致（⚠️ v1.3 起平台不再技术强制白名单，越权属红线违规）
- ✅ 已制定应急预案，确认熔断按钮可达
- ❌ 严禁用于任何非授权测试、生产环境攻击

---

## 🔐 认证方式详解

### 1. Controller API (教学控制台、自动化脚本、CI/CD)

```http
Authorization: Bearer <CONTROLLER_TOKEN>
```

**Token 生成方式**：
```bash
# Controller Token = HMAC-SHA256(SHARED_SECRET, "ddos-controller-auth")
TOKEN=$(echo -n "ddos-controller-auth" | openssl dgst -sha256 -hmac "$SHARED_SECRET" | awk '{print $2}')
```

| 参数 | 说明 |
|------|------|
| `SHARED_SECRET` | 32 字节十六进制字符串，Controller 与所有 Attacker 共享 |
| 算法 | HMAC-SHA256 |
| 数据 | `ddos-controller-auth` (固定字符串) |

### 2. Node API (Attacker 节点接收指令)

```http
X-Node-ID: <node_id>
X-Node-Token: <NODE_TOKEN>
```

**Node Token 生成方式**：
```bash
# Node Token = HMAC-SHA256(SHARED_SECRET, node_id)
NODE_TOKEN=$(echo -n "attacker-http-01" | openssl dgst -sha256 -hmac "$SHARED_SECRET" | awk '{print $2}')
```

**Controller 下发指令时使用的 Token**：
```bash
# Controller Cmd Token = HMAC-SHA256(SHARED_SECRET, "ddos-controller-cmd")
CMD_TOKEN=$(echo -n "ddos-controller-cmd" | openssl dgst -sha256 -hmac "$SHARED_SECRET" | awk '{print $2}')
```

> 💡 **安全提示**：所有 API 均通过 mTLS 1.2+ 传输层加密，Header Token 为应用层二次验证（纵深防御）。

---

## 📋 统一响应格式

### 成功响应
```json
{
  "success": true,
  "data": { ... },
  "message": "Operation completed"
}
```

### 错误响应
```json
{
  "success": false,
  "error": "ERROR_CODE",
  "message": "Human readable description"
}
```

### 分页响应
```json
{
  "items": [...],
  "total": 100,
  "page": 1,
  "page_size": 20
}
```

---

## 🏥 健康检查与就绪探针

| 端点 | 方法 | 认证 | 用途 | Kubernetes Probe |
|------|------|------|------|------------------|
| `/health` | GET | 无 | 进程存活 (Liveness) | `livenessProbe` |
| `/ready` | GET | 无 | 服务就绪 (Readiness) | `readinessProbe` |

**`/health` 响应**：
```json
{
  "status": "healthy",
  "service": "ddos-controller",
  "version": "1.1.0",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

**`/ready` 响应**：
```json
{
  "status": "ready",
  "emergency_stop": false,
  "nodes_online": 3,
  "active_attacks": 1
}
```

> ⚠️ `emergency_stop: true` 时 `/ready` 仍返回 `200`，但 `status: "ready"` 配合 `emergency_stop` 字段供上游判断。

---

## 🎮 Controller REST API (需 Controller Token)

### 节点管理

#### 获取节点列表
```http
GET /api/v1/nodes
Authorization: Bearer <TOKEN>
```

**响应**：
```json
{
  "success": true,
  "data": [
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
  ]
}
```

#### 获取单节点详情
```http
GET /api/v1/nodes/{node_id}
Authorization: Bearer <TOKEN>
```

> v1.3.3: 离线 (OFFLINE) 节点的详情同样可查——仅当节点 ID 从未注册过时才返回 404。

#### 节点心跳上报 (Node → Controller)
```http
POST /api/v1/nodes/heartbeat
X-Node-ID: attacker-http-01
X-Node-Token: <NODE_TOKEN>
Content-Type: application/json

{
  "node_id": "attacker-http-01",
  "timestamp": "2026-08-25T10:00:00Z",
  "cpu_percent": 12.5,
  "memory_percent": 34.2,
  "network_mbps": 45.6,
  "active_connections": 234,
  "current_attacks": ["atk-abc123"],
  "status": "online"
}
```

> **认证**: 通过 `X-Node-ID` + `X-Node-Token` HTTP Header (HMAC-SHA256 派生); **非** `Authorization: Bearer`
>
> **Body 字段均为顶层扁平结构** (无 `metrics.*` 嵌套);`node_id` 字段必须与 `X-Node-ID` Header 一致
>
> **响应**:
> - `200 OK`: 心跳记账成功 (服务器时钟, 忽略 `timestamp` 字段)
> - `401 Unauthorized`: Token 无效
> - `403 Forbidden`: 节点未注册 (强制触发 register 流程后自动恢复)
>
> v1.3.3: 心跳记账改用 **服务器时钟** (避免节点本地时间漂移);未知节点心跳记 warning 不再静默;节点侧 `HEARTBEAT_INTERVAL=10` (默认, 可调)

---

### 攻击编排

#### 发起攻击
```http
POST /api/v1/attacks/launch
Authorization: Bearer <TOKEN>
Content-Type: application/json
```

> v1.3.3: `launch`/`stop` 为动作保留字——对它们发起 GET 请求返回 **405**（附正确用法提示），而非误导性的 404 "Attack not found"。

**请求体完整参数**：

| 字段 | 类型 | 必填 | 适用攻击 | 说明 | 约束 |
|------|------|------|----------|------|------|
| `attack_type` | enum | ✅ | 所有 | `http_flood` \| `syn_flood` \| `slowloris` \| `udp_flood` \| `udp_reflection` | - |
| `target` | object | ✅ | 所有 | 目标规格 | 见下表 |
| `duration` | int | ❌ | 所有 | 持续时间(秒) | 1-3600，默认 60 |
| `rps` | int | ❌ | 所有 | 每秒请求/包数 | 1-100000，默认 1000 |
| `concurrency` | int | ❌ | 所有 | 并发连接/线程数 | 1-10000，默认 100 |
| `scenario_id` | string | ❌ | 所有 | 关联场景 ID | - |
| `node_ids` | string[] | ❌ | 所有 | 指定节点，空=自动匹配 | - |
| `priority` | int | ❌ | 所有 | 调度优先级，0-100，默认 0（数值越大越优先） | 0-100 |
| `method` | enum | ❌ | HTTP | `GET` \| `POST` \| `HEAD` | 默认 GET |
| `headers` | object | ❌ | HTTP | 自定义 Header | - |
| `body` | string | ❌ | HTTP | POST Body 内容 | - |
| `use_https` | bool | ❌ | HTTP | 使用 HTTPS | 默认 false |
| `verify_ssl` | bool | ❌ | HTTP | 验证服务端证书 | 默认 false |
| `source_ip_spoof` | bool | ❌ | RAW | 伪造源 IP | 默认 false |
| `spoof_cidr` | string | ❌ | RAW | 伪造源 IP CIDR | 默认 10.0.0.0/8 |
| `interface` | string | ❌ | RAW | 发包网卡名 | 如 eth0 |
| `slowloris_interval` | int | ❌ | Slowloris | Header 发送间隔(秒) | 5-60，默认 15 |
| `reflector_type` | enum | ❌ | UDP反射 | `ntp` \| `dns` \| `memcached` \| `ssdp` \| `snmp` (v1.1) | - |
| `reflector_list` | string[] | ❌ | UDP反射 | 反射器 `IP:PORT` 列表 | 必填 |

**Target 对象**：
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `ip` | string | ✅ | 目标地址 — IPv4/IPv6/CIDR/**域名** (v1.3 起支持域名, scapy 类攻击自动解析 A 记录)。⚠️ v1.3 起不再校验白名单，仅拒绝场景模板占位符 |
| `port` | int | ❌ | 目标端口，默认 80 |
| `protocol` | enum | ❌ | `tcp` \| `udp`，默认 tcp |
| `path` | string | ❌ | HTTP 路径，默认 / |
| `host_header` | string | ❌ | Host 头值 |

**请求示例**：
```json
{
  "attack_type": "http_flood",
  "target": {"ip": "10.100.10.10", "port": 80, "protocol": "tcp", "path": "/api/search"},
  "duration": 60,
  "rps": 2000,
  "concurrency": 200,
  "method": "POST",
  "headers": {"Content-Type": "application/json"},
  "body": "{\"query\":\"test\"}",
  "node_ids": ["attacker-http-01"]
}
```

**响应**：
```json
{
  "success": true,
  "data": {
    "attack_id": "atk-a1b2c3d4e5f6",
    "target_nodes": ["attacker-http-01", "attacker-http-02"],
    "allocated": {
      "attacker-http-01": {"rps": 2000, "pps": 0, "concurrent": 200},
      "attacker-http-02": {"rps": 2000, "pps": 0, "concurrent": 200}
    }
  },
  "message": "Attack launched"
}
```

#### 停止攻击
```http
POST /api/v1/attacks/{attack_id}/stop?reason=manual
Authorization: Bearer <TOKEN>
```

**响应**：
```json
{
  "success": true,
  "data": {"stopped": true, "nodes": ["attacker-http-01"]},
  "message": "Attack stop requested"
}
```

#### 紧急熔断 (停止所有攻击)
```http
POST /api/v1/emergency_stop
Authorization: Bearer <TOKEN>
Content-Type: application/json
```

**请求体**：
```json
{
  "reason": "Business impact detected - latency P99 > 5s",
  "issued_by": "instructor-zhang",
  "target_node_ids": []  // 空数组 = 全网节点
}
```

**响应**：
```json
{
  "success": true,
  "data": {
    "stopped_attacks": ["atk-a1b2c3d4e5f6", "atk-b2c3d4e5f6a7"],
    "affected_nodes": ["attacker-http-01", "attacker-raw-01"],
    "nodes_notified": 5
  },
  "message": "Emergency stop executed"
}
```

#### 复位熔断
```http
POST /api/v1/emergency_stop/reset
Authorization: Bearer <TOKEN>
```

**响应**：
```json
{
  "success": true,
  "message": "Emergency stop reset"
}
```

#### 查询攻击列表
```http
GET /api/v1/attacks
Authorization: Bearer <TOKEN>
```

> **v1.3 语义**：返回**运行中 + 60 分钟内结束**的所有攻击，每条均含权威
> `status`（`launching/starting/running/stopping/stopped/completed/failed/emergency_stopped`）
> 与 `started_at`——页面刷新后列表与状态不丢失。已结束攻击在 60 分钟 TTL 后自动清除。

#### 查询攻击详情
```http
GET /api/v1/attacks/{attack_id}
Authorization: Bearer <TOKEN>
```

**响应**：
```json
{
  "success": true,
  "data": {
    "attack_id": "atk-a1b2c3d4e5f6",
    "status": "running",
    "started_at": "2024-01-15T10:30:00Z",
    "finished_at": null,
    "stop_reason": null,
    "command": { ... },
    "results": {
      "attacker-http-01": {
        "attack_id": "atk-a1b2c3d4e5f6",
        "node_id": "attacker-http-01",
        "status": "running",
        "started_at": "2024-01-15T10:30:00Z",
        "total_requests": 15000,
        "successful_requests": 14800,
        "failed_requests": 200,
        "bytes_sent": 5242880,
        "bytes_received": 10485760,
        "metrics": {
          "latencies": [0.045, 0.052],
          "error_counts": {"ClientConnectorError": 200}
        }
      }
    },
    "node_count": 2
  }
}
```

> **v1.3 实时性**：节点运行期间每 **2 秒**上报进度快照，控制器按单调递增合并
> 计数器（total/successful/failed/bytes 只增不减）并经 WebSocket `attacks` 频道推送。
> 每节点错误样本上限 50 条，聚合摘要见 `metrics.error_counts`。

---

### 场景管理

#### 获取场景列表
```http
GET /api/v1/scenarios
Authorization: Bearer <TOKEN>
```

**预设场景表**：

| 场景 ID | 名称 | 攻击向量 | 适用节点 | 典型用途 |
|---------|------|----------|----------|----------|
| `cc_attack` | CC攻击基础演练 | HTTP Flood | HTTP | WAF、应用层限流、IP 信誉测试 |
| `syn_flood` | SYN Flood 演练 | SYN Flood | RAW | SYN Cookie、连接队列、防火墙状态检测 |
| `slowloris` | Slowloris 慢速攻击 | Slowloris | HTTP | 连接耗尽、Web 服务器抗压、连接池测试 |
| `udp_reflection` | UDP 反射放大演练 | UDP Reflection (NTP/DNS) | RAW | 反射放大、清洗设备吞吐、源端口过滤 |
| `mixed_wave` | 多层混合波演练 | HTTP + SYN + Slowloris | 混合 | 多层联动防御、应急响应、红蓝对抗 |
| `ramp_up` | 渐进式压力测试 | 阶梯式 HTTP/SYN | 所有 | 容量规划、性能拐点、熔断阈值验证 |

#### 获取场景详情
```http
GET /api/v1/scenarios/{scenario_id}
Authorization: Bearer <TOKEN>
```

#### 运行场景 (支持参数覆盖)
```http
POST /api/v1/scenarios/{scenario_id}/run
Authorization: Bearer <TOKEN>
Content-Type: application/json
```

> ⚠️ **overrides 必填**：内置场景 YAML 的目标 IP 均为 `TARGET_IP_PLACEHOLDER` 模板，
> 服务端在启动前同步校验——若 overrides 未提供 `target.ip`（或值非法），
> 接口直接返回 **400** 与具体缺失步骤说明，不会出现 200 后静默不执行的情况。
> 注意：**完全未携带请求体**时由 FastAPI 参数校验拦截，返回 **422**（`Field required`）；
> 只有请求体存在但 `overrides.target.ip` 缺失/非法时才走业务校验返回 **400**。

**请求体**：
```json
{
  "overrides": {
    "target": {"ip": "10.100.10.10"},
    "rps": 5000,
    "duration": 120,
    "concurrency": 300
  }
}
```

**响应**：
```json
{
  "success": true,
  "data": {"run_id": "scenario-run-a1b2c3d4"},
  "message": "Scenario started"
}
```

**校验失败响应 (400)**：
```json
{
  "detail": "Step 0: target.ip is still a placeholder. Pass overrides like {'target': {'ip': '10.100.10.10'}}"
}
```

#### 停止场景
```http
POST /api/v1/scenarios/{scenario_id}/stop
Authorization: Bearer <TOKEN>
```

---

### 一键安装引导 (v1.1)

| 端点 | 方法 | 认证 | 说明 |
|------|------|------|------|
| `/api/v1/nodes/enroll-command?type=http\|raw&node_id=X` | GET | ✅ Bearer | 生成节点一键安装命令（WebUI「添加节点」数据源），含约 1h 有效期 |
| `POST /api/v1/nodes/enroll` | POST | enroll token | 节点自助接入：`{node_id, enroll_token}` → 返回运行配置（shared_secret/白名单/CA 地址/TLS 指纹） |
| `GET /api/v1/controller-info` | GET | 无 | 公开元信息：版本、TLS 指纹（供节点钉扎校验）、可用制品列表 |
| `GET /install.sh` | GET | 无 | 分发节点安装脚本（自动注入控制器地址） |
| `GET /artifacts/ca-cert.pem` | GET | 无 | 分发控制器 CA 证书（自签场景供节点信任链引导） |
| `GET /artifacts/{file}` | GET | 无 | 二进制制品分发（`ddos-attacker-linux-x86_64.tar.gz` 等，挂载 ./artifacts 目录） |

**enroll token 机制**：`HMAC-SHA256(SHARED_SECRET, "ddos-enroll:" + node_id + ":" + UTC小时桶)`，
绑定单个节点、服务端零存储、当前+上一小时桶内有效。失败响应 403（带 1s 延迟防爆破），全部尝试入审计日志
（事件类型 `node_enroll_success` / `node_enroll_failed` / `enroll_command_issued`）。

### 限流状态查询

```http
GET /api/v1/rate-limits
Authorization: Bearer <TOKEN>
```

**响应**：
```json
{
  "success": true,
  "data": {
    "global_rps": 50000,
    "global_pps": 100000,
    "global_concurrent": 100000,
    "used_rps": 15000,
    "used_pps": 0,
    "used_concurrent": 500,
    "quotas": [
      {"attack_id": "atk-a1b2", "node_id": "attacker-http-01", "rps": 5000, "pps": 0, "concurrent": 200},
      {"attack_id": "atk-a1b2", "node_id": "attacker-http-02", "rps": 5000, "pps": 0, "concurrent": 200},
      {"attack_id": "atk-c3d4", "node_id": "attacker-raw-01", "rps": 0, "pps": 5000, "concurrent": 100}
    ]
  }
}
```

> 配额按 `(attack_id, node_id)` 二元组记账：同一节点可并发承载多场攻击且互不覆盖；
> 停止单场攻击只回收该场配额，紧急熔断清空全部。

---

## 🔌 WebSocket 实时指标流

### 连接建立
```bash
wscat -c "wss://<controller>:8443/ws/metrics?token=<TOKEN>&channels=nodes,attacks,metrics,alerts,system&client_id=web-ui"
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `token` | ✅ | Controller Token (同 REST API) |
| `channels` | ❌ | 逗号分隔，默认 `nodes,attacks,metrics,alerts,system` |
| `client_id` | ❌ | 客户端标识，默认 `web-ui` |

### 消息格式
```json
{
  "type": "node_update|attack_start|attack_update|attack_stop|node_heartbeat|rate_limit_status|emergency_stop|audit_event|system_event",
  "timestamp": "2024-01-15T10:30:00Z",
  "data": { ... }
}
```

### 客户端控制指令
```json
// 订阅额外频道
{"type": "subscribe", "channels": ["audit"]}

// 取消订阅
{"type": "unsubscribe", "channels": ["metrics"]}

// 心跳保活
{"type": "ping"}

// 服务端响应
{"type": "pong", "ts": "2024-01-15T10:30:00Z"}
```

### 频道数据载荷示例

**节点心跳** (`metrics` 频道)：
```json
{
  "type": "node_heartbeat",
  "timestamp": "2024-01-15T10:30:00Z",
  "data": {
    "node_id": "attacker-http-01",
    "cpu_percent": 45.2,
    "memory_percent": 38.5,
    "network_mbps": 125.6,
    "active_connections": 1234,
    "current_attacks": ["atk-a1b2c3d4"],
    "status": "attacking"
  }
}
```

**攻击更新** (`attacks` 频道)：
```json
{
  "type": "attack_update",
  "timestamp": "2024-01-15T10:30:05Z",
  "data": {
    "attack_id": "atk-a1b2c3d4",
    "node_id": "attacker-http-01",
    "result": {
      "status": "running",
      "total_requests": 5000,
      "successful_requests": 4950,
      "failed_requests": 50,
      "bytes_sent": 2048000,
      "bytes_received": 4096000,
      "metrics": {"latencies": [0.041, 0.043, 0.039]}
    }
  }
}
```

**紧急熔断** (`alerts` + `system` 频道)：
```json
{
  "type": "emergency_stop",
  "timestamp": "2024-01-15T10:30:00Z",
  "level": "critical",
  "data": {"reason": "Latency P99 > 5s", "issued_by": "instructor-zhang"}
}
```

---

## 📊 Node API (Attacker 节点暴露，Node Token 认证)

> **基础 URL**: `http://<attacker-host>:8080` (HTTP，内网无 TLS 终结)  
> **认证 Headers**: `X-Node-ID` + `X-Node-Token`

### 健康检查
```http
GET /health
```
```json
{"status": "healthy", "node_id": "attacker-http-01", "timestamp": "2024-01-15T10:30:00Z"}
```

### Prometheus 指标
```http
GET /metrics
```
```
ddos_node_cpu_percent{node_id="attacker-http-01"} 12.5
ddos_node_memory_percent{node_id="attacker-http-01"} 34.2
ddos_node_active_attacks{node_id="attacker-http-01"} 1
ddos_node_network_mbps{node_id="attacker-http-01"} 45.6
ddos_node_connections{node_id="attacker-http-01"} 234
```

> ⚠️ **端点归属**: 此 `GET /metrics` 是 **攻击节点** 的 Prometheus 兼容端点 (无需鉴权, 内网监听 `NODE_PORT=8080`)。**Controller 端无对应 HTTP 路由**;Controller 侧的实时指标通过 WebSocket `/ws/metrics` 推送 (见下方「WebSocket 实时事件流」)。如需采集 Controller 指标, 请订阅 WS 而不是 HTTP 探测。

### 节点详细信息
```http
GET /api/v1/info
X-Node-ID: attacker-http-01
X-Node-Token: <NODE_TOKEN>
```

### 当前执行的攻击
```http
GET /api/v1/attacks
X-Node-ID: attacker-http-01
X-Node-Token: <NODE_TOKEN>
```

### Controller → Node 下发指令端点 (v1.1)

Controller 通过 NodeCommander 调用以下节点端点下发指令（认证同上）：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/attacks/execute` | POST | 执行攻击指令（body 为 AttackCommand JSON） |
| `/api/v1/attacks/{attack_id}/stop` | POST | 停止指定攻击 |
| `/api/v1/emergency_stop` | POST | 紧急熔断（body: `{reason, issued_by}`），置位后拒绝新指令 |
| `/api/v1/emergency_stop/reset` | POST | **v1.1 新增**：熔断复位，清除节点侧全局熔断状态 |
| `/api/v1/attacks` | GET | 查询节点当前攻击及结果摘要 |
| `/api/v1/info` | GET | 节点详细信息 |

---

## 🚀 完整调用示例

### Python 3.11+ (httpx)
```python
import httpx
import hmac
import hashlib
import os
import asyncio

SHARED_SECRET = os.getenv("SHARED_SECRET", "changeme32charslongsecretkey123456").encode()
CONTROLLER_URL = "https://10.100.1.10:8443"

def get_controller_token() -> str:
    return hmac.new(SHARED_SECRET, b"ddos-controller-auth", hashlib.sha256).hexdigest()

async def launch_cc_attack(target_ip: str, rps: int = 2000, duration: int = 60):
    async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
        resp = await client.post(
            f"{CONTROLLER_URL}/api/v1/attacks/launch",
            headers={"Authorization": f"Bearer {get_controller_token()}"},
            json={
                "attack_type": "http_flood",
                "target": {"ip": target_ip, "port": 80, "path": "/"},
                "duration": duration,
                "rps": rps,
                "concurrency": 200
            }
        )
        resp.raise_for_status()
        return resp.json()

async def emergency_stop(reason: str, issued_by: str):
    async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
        resp = await client.post(
            f"{CONTROLLER_URL}/api/v1/emergency_stop",
            headers={"Authorization": f"Bearer {get_controller_token()}"},
            json={"reason": reason, "issued_by": issued_by}
        )
        resp.raise_for_status()
        return resp.json()

# 使用示例
if __name__ == "__main__":
    # 发起 CC 攻击
    result = asyncio.run(launch_cc_attack("10.100.10.10", rps=2000))
    print(f"Attack launched: {result['data']['attack_id']}")
    
    # 30 秒后紧急停止
    await asyncio.sleep(30)
    result = asyncio.run(emergency_stop("Training complete", "auto-script"))
    print(f"Emergency stop: {result['data']['stopped_attacks']}")
```

### cURL 速查表
```bash
# 环境变量
export SHARED_SECRET="changeme32charslongsecretkey123456"
export CONTROLLER_URL="https://10.100.1.10:8443"
export TOKEN=$(echo -n "ddos-controller-auth" | openssl dgst -sha256 -hmac "$SHARED_SECRET" | awk '{print $2}')

# 发起 HTTP Flood
curl -k -X POST "$CONTROLLER_URL/api/v1/attacks/launch" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"attack_type":"http_flood","target":{"ip":"10.100.10.10","port":80},"duration":60,"rps":2000,"concurrency":200}'

# 发起 SYN Flood (需 RAW 节点)
curl -k -X POST "$CONTROLLER_URL/api/v1/attacks/launch" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"attack_type":"syn_flood","target":{"ip":"10.100.10.10","port":80},"duration":60,"rps":10000,"concurrency":4,"source_ip_spoof":true,"spoof_cidr":"10.0.0.0/8","interface":"eth0"}'

# 运行预设场景
curl -k -X POST "$CONTROLLER_URL/api/v1/scenarios/mixed_wave/run" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"overrides":{"target":{"ip":"10.100.10.10"},"rps":3000,"duration":120}}'

# 紧急熔断
curl -k -X POST "$CONTROLLER_URL/api/v1/emergency_stop" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"reason":"Business impact","issued_by":"instructor"}'

# 复位熔断
curl -k -X POST "$CONTROLLER_URL/api/v1/emergency_stop/reset" -H "Authorization: Bearer $TOKEN"

# 查看节点
curl -k "$CONTROLLER_URL/api/v1/nodes" -H "Authorization: Bearer $TOKEN"

# 查看限流状态
curl -k "$CONTROLLER_URL/api/v1/rate-limits" -H "Authorization: Bearer $TOKEN"

# WebSocket 连接
wscat -c "$CONTROLLER_URL/ws/metrics?token=$TOKEN&channels=nodes,attacks,metrics,alerts,system"
```

---

## ⚠️ 错误码对照表

| HTTP 码 | 错误码 | 含义 | 处理建议 |
|---------|--------|------|----------|
| 200 | - | 成功 | - |
| 400 | `VALIDATION_ERROR` | 参数校验失败 (目标格式非法、场景占位符未覆盖、参数越界) | 检查请求体、确认目标 IP/域名格式合法 |
| 401 | `UNAUTHORIZED` | 认证失败 (Token 无效/过期/缺失) | 重新生成 Token、检查 SHARED_SECRET 一致性 |
| 403 | `FORBIDDEN` | 权限不足 (非管理员操作熔断复位) | 确认调用者身份、权限矩阵 |
| 404 | `NOT_FOUND` | 资源不存在 (攻击 ID/场景 ID/节点 ID) | 确认资源 ID 正确性 |
| 409 | `CONFLICT` | 状态冲突 (熔断激活时发起攻击、场景重复运行) | 先复位熔断或停止冲突场景 |
| 422 | `QUOTA_EXHAUSTED` | 业务校验失败 (全局配额耗尽、节点不支持攻击类型) | 等待配额释放、调整节点部署 |
| 500 | `INTERNAL_ERROR` | 内部错误 | 查看 Controller 日志、联系运维 |
| 503 | `SERVICE_UNAVAILABLE` | 服务未就绪 (编排器未初始化) | 等待启动完成、检查依赖服务 |

---

## 📝 版本历史

| 版本 | 日期 | 变更摘要 |
|------|------|----------|
| **v1.4.1-hotfix6** | 2026-08-28 | **REG-1~6 全闭环**: do_update 写 NODE_TLS_* / 升级路径兼容 / 6 项 install 脚本 bug 修复; fail-closed 默认 (TD-1) NodeCommander TLS; wrapper self-refresh; config.env sed 清理; 测试隔离 (REG-7); 5 套 E2E 验证脚本; 文档体系完整化 (CHANGELOG/SECURITY/CONTRIBUTING/DEEP_EVALUATION_v3); Controller→Node 通信链路描述更新 (HTTPS+mTLS / HTTP 临时回退) |
| v1.4.0 | 2026-08-28 | **TD-1/2/3**: NodeCommander `verify=False` → `verify=True` 默认 fail-closed; 5 个新 env (`NODE_TLS_CA_FILE` / `NODE_TLS_CERT_FILE` / `NODE_TLS_KEY_FILE` / `NODE_INSECURE_PLAIN_HTTP` / `NODE_PLAIN_HTTP_BANNED`); docker-compose 弱密钥启动崩溃; 测试死引用修复 |
| v1.3.4 | 2026-08-25 | **安装器加固**: `controller-install.sh` 与 `node-install.sh` 现在创建专用 `ddos` 系统用户 (无登录权限) 并将安装目录、配置文件、systemd 单元 chown 到该用户；`config.env` 固定 `chmod 600`（含 SHARED_SECRET），systemd unit `chmod 640`；Controller unit 添加 `User=ddos Group=ddos`（v1.3.3 之前因 `ddos` 用户不存在会回退 root 运行）；attacker `http` 类型用 `ddos` 用户；`raw` 类型仍为 root（需要 CAP_NET_RAW）；升级路径同步修正 owner 与权限，避免 GHA tarball 中 build UID (1001) 残留；**文档补充**: `/metrics` 端点归属澄清（仅 attacker 节点，Controller 走 WS）；`/api/v1/nodes/heartbeat` 完整 schema 与 Header 鉴权约定 |
| v1.3.3 | 2026-08-25 | **BUG-2**: 节点心跳移入独立 OS 线程（攻击错误风暴不再延迟心跳）+ HTTP flood 连续错误指数退避（封顶 250ms）；**BUG-4**: 心跳记账改用服务器时钟，未知节点心跳记 warning 不再静默；节点侧每 60s 幂等重发 register + 收到 401/403/404 立即重注册（控制器重启后节点自愈回联 ≤60s）；**BUG-1**: `ddos-controller`/`ddos-node` wrapper 变更类操作提权门卫（root 直行 → sudo -n → 明确提示）；**BUG-5**: 二进制安装器随附 node-install.sh 到安装目录（/install.sh 端点可用），INSTALL_SCRIPT 候选新增二进制同目录；**BUG-6**: GET /nodes/{id} 读全量字典，离线节点详情可查（仅未注册过返回 404）；**OBS-7**: structlog 过滤级别接通 LOG_LEVEL env；**OBS-8**: launch/stop 保留字 GET 返回 405 + 文档 400/422 边界澄清；audit writer 跨事件循环复用自旋修复（Queue 每次 start 重建）；PLATFORM_VERSION 单一事实源 |
| v1.3.2 | 2026-08-25 | 目标支持域名/IP（TargetSpec RFC1123 校验，scapy 类攻击 getaddrinfo 自动解析）；**目标白名单技术强制移除**（仅保留场景占位符拒绝）；攻击列表/详情新增权威 `status/started_at/finished_at/stop_reason`，返回运行中+60min TTL 内已结束攻击；节点每 2s 周期上报进度快照（单调合并）；`metrics.error_counts` 错误聚合摘要（样本上限 50）；WebSocket attack_start 携带完整 command |
| v1.2 | 2024-12-20 | 新增一键安装引导端点组：enroll-command / nodes/enroll / controller-info / install.sh / artifacts 分发；无状态 enroll token 机制说明 |
| v1.1 | 2024-12-19 | 新增 Node API 文档、完善错误码、补充 Python/cURL 示例、修复 datetime 序列化说明；rate-limits 响应改为 quotas 数组（按 attack_id+node_id 记账）；场景运行 overrides 必填（400 校验）；新增 emergency_stop/reset 端点；节点注册/心跳/注销/结果上报身份一致性校验 (403)；TLS_VERIFY_CLIENT 开关说明 |
| v1.0 | 2024-01-15 | 初始版本：REST API、WebSocket、场景管理、限流查询 |

---

**文档控制**：
- API 变更需向前兼容，破坏性变更需发布 v2.0
- 所有变更同步更新 OpenAPI Spec (`controller/openapi.json`)
- 变更通知：内部邮件列表 `sec-api@internal`