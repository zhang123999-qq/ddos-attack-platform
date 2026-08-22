# DDoS Attack Platform - API 参考文档

> 基础 URL: `https://controller:8443/api/v1`
> 认证: `Authorization: Bearer <TOKEN>` (Controller API) / mTLS + Header (Node API)

---

## 🔐 认证方式

### Controller API (教学控制台/自动化脚本)
```bash
# Header
Authorization: Bearer <SHARED_SECRET_HMAC>
```
- Token 为 `SHARED_SECRET` 的 HMAC-SHA256 (`ddos-controller-auth`)

### Attacker Node API (节点间通信)
```bash
# Headers (mTLS 已在传输层验证)
X-Node-ID: <node_id>
X-Node-Token: <HMAC_SHA256(shared_secret, node_id)>
```

---

## 📋 Controller REST API

### 健康检查

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| GET | `/health` | 无 | 服务存活检查 |
| GET | `/ready` | 无 | 就绪检查 (含熔断状态) |

**响应示例**:
```json
{
  "status": "ready",
  "emergency_stop": false,
  "nodes_online": 3,
  "active_attacks": 1
}
```

---

### 节点管理

#### 获取节点列表
```http
GET /api/v1/nodes
```
**响应**:
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
      "max_rps": 10000,
      "max_concurrent": 5000,
      "status": "online",
      "last_heartbeat": "2024-01-15T10:30:00Z",
      "labels": {"role": "http-attacker"}
    }
  ]
}
```

#### 获取单节点详情
```http
GET /api/v1/nodes/{node_id}
```

---

### 攻击编排

#### 发起攻击
```http
POST /api/v1/attacks/launch
Content-Type: application/json
```

**请求体**:
```json
{
  "attack_type": "http_flood",
  "target": {
    "ip": "10.100.10.10",
    "port": 80,
    "protocol": "tcp",
    "path": "/api/search",
    "host_header": "example.com"
  },
  "duration": 60,
  "rps": 2000,
  "concurrency": 200,
  "scenario_id": "cc_attack",
  "node_ids": ["attacker-http-01", "attacker-http-02"],
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
  "reflector_list": ["10.100.200.10:123"]
}
```

**参数说明**:
| 字段 | 类型 | 必填 | 攻击类型 | 说明 |
|------|------|------|----------|------|
| attack_type | enum | 是 | 所有 | http_flood/syn_flood/slowloris/udp_flood/udp_reflection |
| target.ip | string | 是 | 所有 | 目标 IP (必须在白名单) |
| target.port | int | 否 | 所有 | 默认 80 |
| target.path | string | 否 | HTTP类 | 默认 / |
| duration | int | 否 | 所有 | 持续秒数 (1-3600)，默认 60 |
| rps | int | 否 | 所有 | 每秒请求/包数 (1-100000)，默认 1000 |
| concurrency | int | 否 | 所有 | 并发连接/线程数 (1-10000)，默认 100 |
| node_ids | string[] | 否 | 所有 | 指定节点，空=自动选择支持类型的在线节点 |
| method | enum | 否 | HTTP | GET/POST/HEAD |
| headers | object | 否 | HTTP | 自定义 Header |
| body | string | 否 | HTTP | POST Body |
| use_https | bool | 否 | HTTP | 是否使用 HTTPS |
| source_ip_spoof | bool | 否 | RAW | 是否伪造源 IP |
| spoof_cidr | string | 否 | RAW | 伪造源 IP 范围 |
| interface | string | 否 | RAW | 发包网卡 |
| slowloris_interval | int | 否 | Slowloris | Header 发送间隔(秒)，默认 15 |
| reflector_type | enum | 否 | UDP反射 | ntp/dns/memcached/ssdp |
| reflector_list | string[] | 否 | UDP反射 | 反射器 IP:端口列表 |

**响应**:
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
```

**响应**:
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
Content-Type: application/json
```

**请求体**:
```json
{
  "reason": "Business impact detected",
  "issued_by": "instructor-zhang",
  "target_node_ids": []  // 空=全网
}
```

**响应**:
```json
{
  "success": true,
  "data": {
    "stopped_attacks": ["atk-a1b2c3d4e5f6", "atk-b2c3d4e5f6a7"],
    "affected_nodes": ["attacker-http-01", "attacker-raw-01"]
  },
  "message": "Emergency stop executed"
}
```

#### 复位熔断
```http
POST /api/v1/emergency_stop/reset
```

#### 查询攻击列表
```http
GET /api/v1/attacks
```

#### 查询攻击详情
```http
GET /api/v1/attacks/{attack_id}
```

**响应**:
```json
{
  "success": true,
  "data": {
    "attack_id": "atk-a1b2c3d4e5f6",
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
        "metrics": {"latencies": [0.045, 0.052, ...]}
      }
    },
    "node_count": 2
  }
}
```

---

### 场景管理

#### 获取场景列表
```http
GET /api/v1/scenarios
```

**预设场景**:
| ID | 名称 | 说明 |
|----|------|------|
| cc_attack | CC攻击基础演练 | HTTP Flood 测试限流/WAF |
| syn_flood | SYN Flood演练 | 测试 SYN Cookie/连接队列 |
| slowloris | Slowloris演练 | 测试连接超时/连接池 |
| udp_reflection | UDP反射放大演练 | 测试 UDP限速/反射源过滤 |
| mixed_wave | 多层混合波演练 | 综合多向量攻击 |
| ramp_up | 渐进式压力测试 | 寻找性能拐点 |

#### 获取场景详情
```http
GET /api/v1/scenarios/{scenario_id}
```

#### 运行场景
```http
POST /api/v1/scenarios/{scenario_id}/run
Content-Type: application/json
```

**请求体** (可选参数覆盖):
```json
{
  "overrides": {
    "target": {"ip": "10.100.10.10"},
    "rps": 5000,
    "duration": 120
  }
}
```

#### 停止场景
```http
POST /api/v1/scenarios/{scenario_id}/stop
```

---

### 限流状态

```http
GET /api/v1/rate-limits
```

**响应**:
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
    "node_quotas": {
      "attacker-http-01": {"rps": 5000, "pps": 0, "concurrent": 200},
      "attacker-http-02": {"rps": 5000, "pps": 0, "concurrent": 200},
      "attacker-raw-01": {"rps": 0, "pps": 5000, "concurrent": 100}
    }
  }
}
```

---

## 🔌 WebSocket 实时指标

### 连接
```bash
wscat -c "wss://controller:8443/ws/metrics?token=<TOKEN>&channels=nodes,attacks,metrics,alerts,system&client_id=my-console"
```

### 消息格式
```json
{
  "type": "node_update|attack_start|attack_update|attack_stop|node_heartbeat|rate_limit_status|emergency_stop|audit_event",
  "timestamp": "2024-01-15T10:30:00Z",
  "data": { ... }
}
```

### 频道订阅控制
```json
// 客户端发送
{"type": "subscribe", "channels": ["nodes", "attacks"]}
{"type": "unsubscribe", "channels": ["audit"]}
{"type": "ping"}

// 服务端响应
{"type": "pong", "ts": "2024-01-15T10:30:00Z"}
```

---

## 📊 Node API (Attacker 节点暴露)

### 健康检查
```http
GET http://attacker:8080/health
```

### Prometheus 指标
```http
GET http://attacker:8080/metrics
```

**输出示例**:
```
ddos_node_cpu_percent{node_id="attacker-http-01"} 12.5
ddos_node_memory_percent{node_id="attacker-http-01"} 34.2
ddos_node_active_attacks{node_id="attacker-http-01"} 1
ddos_node_network_mbps{node_id="attacker-http-01"} 45.6
ddos_node_connections{node_id="attacker-http-01"} 234
```

### 节点信息
```http
GET http://attacker:8080/api/v1/info
Headers: X-Node-ID, X-Node-Token
```

### 当前攻击列表
```http
GET http://attacker:8080/api/v1/attacks
Headers: X-Node-ID, X-Node-Token
```

---

## 🚀 完整调用示例

### Python 示例
```python
import httpx
import hmac
import hashlib
import os

SHARED_SECRET = os.getenv("SHARED_SECRET").encode()
CONTROLLER_URL = "https://10.100.1.10:8443"

def get_token():
    return hmac.new(SHARED_SECRET, b"ddos-controller-auth", hashlib.sha256).hexdigest()

async def launch_cc_attack(target_ip: str, rps: int = 2000):
    async with httpx.AsyncClient(verify=False) as client:
        resp = await client.post(
            f"{CONTROLLER_URL}/api/v1/attacks/launch",
            headers={"Authorization": f"Bearer {get_token()}"},
            json={
                "attack_type": "http_flood",
                "target": {"ip": target_ip, "port": 80, "path": "/"},
                "duration": 60,
                "rps": rps,
                "concurrency": 200
            }
        )
        return resp.json()

async def emergency_stop(reason: str, issued_by: str):
    async with httpx.AsyncClient(verify=False) as client:
        resp = await client.post(
            f"{CONTROLLER_URL}/api/v1/emergency_stop",
            headers={"Authorization": f"Bearer {get_token()}"},
            json={"reason": reason, "issued_by": issued_by}
        )
        return resp.json()
```

### cURL 示例
```bash
# 设置 Token
TOKEN=$(echo -n "ddos-controller-auth" | openssl dgst -sha256 -hmac "$SHARED_SECRET" | cut -d' ' -f2)

# 发起攻击
curl -k -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -X POST https://10.100.1.10:8443/api/v1/attacks/launch \
  -d '{"attack_type":"http_flood","target":{"ip":"10.100.10.10"},"duration":60,"rps":2000}'

# 紧急停止
curl -k -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -X POST https://10.100.1.10:8443/api/v1/emergency_stop \
  -d '{"reason":"Training complete","issued_by":"instructor"}'

# 查看节点
curl -k -H "Authorization: Bearer $TOKEN" \
  https://10.100.1.10:8443/api/v1/nodes
```

---

## ⚠️ 错误码

| HTTP 码 | 含义 |
|---------|------|
| 200 | 成功 |
| 400 | 参数错误 (如目标不在白名单) |
| 401 | 认证失败 (Token 无效/过期) |
| 403 | 权限不足 (如非管理员触发熔断复位) |
| 404 | 资源不存在 (攻击ID/场景ID/节点ID) |
| 409 | 冲突 (熔断激活时发起攻击、场景重复运行) |
| 422 | 业务校验失败 (配额耗尽、节点不支持攻击类型) |
| 500 | 内部错误 |
| 503 | 服务未就绪 (编排器未初始化) |

---

## 📝 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2024-01-15 | 初始版本 |