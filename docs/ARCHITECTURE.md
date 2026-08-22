# DDoS Attack Platform - 架构设计文档

## 1. 系统概览

### 1.1 设计目标
- **分布式编排**: 单 Controller 管理多 Attacker 节点
- **安全可控**: mTLS 双向认证、白名单、熔断、审计
- **教学友好**: 预设场景、实时可视化、标准化评估
- **生产隔离**: 容器化部署、网络隔离、最小权限

### 1.2 核心组件
```
┌─────────────────────────────────────────────────────────────┐
│                      Controller                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────────┐  │
│  │ REST API │ │ WebSocket│ │Orchestrator│ │  Audit Logger  │  │
│  └──────────┘ └──────────┘ └──────────┘ └────────────────┘  │
│         │           │            │               │           │
│         └───────────┴────────────┴───────────────┘           │
│                         │                                      │
│              mTLS + Token Auth                                │
└─────────────────────────│─────────────────────────────────────┘
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
   ┌─────────┐       ┌─────────┐       ┌─────────┐
   │Attacker │       │Attacker │       │Attacker │
   │  HTTP   │       │  RAW    │       │  HTTP   │
   └─────────┘       └─────────┘       └─────────┘
```

---

## 2. 通信协议

### 2.1 Controller ↔ Attacker (mTLS + REST)

**认证流程**:
```
1. TLS 握手: 双向验证证书 (CA 签发)
2. HTTP Header: X-Node-ID + X-Node-Token (HMAC-SHA256)
3. Controller 验证 Token 有效性
4. 建立长连接/轮询接收指令
```

**API 端点**:
| 方向 | 端点 | 说明 |
|------|------|------|
| Node → Ctrl | `POST /api/v1/nodes/register` | 节点注册 |
| Node → Ctrl | `POST /api/v1/nodes/heartbeat` | 心跳上报 (10s) |
| Node → Ctrl | `POST /api/v1/results` | 攻击结果上报 |
| Ctrl → Node | `POST /api/v1/attacks/execute` | 下发攻击指令 |
| Ctrl → Node | `POST /api/v1/attacks/{id}/stop` | 停止指令 |
| Ctrl → Node | `POST /api/v1/emergency_stop` | 熔断广播 |

### 2.2 Controller ↔ Console (WebSocket)

**连接**: `wss://controller:8443/ws/metrics?token=xxx&channels=nodes,attacks,metrics,alerts,system`

**消息格式**:
```json
{
  "type": "node_update|attack_update|metric|alert",
  "timestamp": "2024-01-15T10:30:00Z",
  "data": { ... }
}
```

**频道**:
| 频道 | 内容 | 频率 |
|------|------|------|
| nodes | 节点注册/离线/状态变更 | 事件驱动 |
| attacks | 攻击启动/更新/停止 | 事件驱动 |
| metrics | 节点心跳、限流状态 | ~1Hz |
| alerts | 熔断、异常、阈值触发 | 事件驱动 |
| audit | 审计日志流 | 事件驱动 |

---

## 3. 数据模型

### 3.1 攻击指令 (AttackCommand)
```json
{
  "attack_id": "atk-a1b2c3d4",
  "attack_type": "http_flood",
  "params": {
    "target": {"ip": "10.100.10.10", "port": 80, "path": "/api"},
    "duration": 60,
    "rps": 2000,
    "concurrency": 200,
    "method": "POST",
    "headers": {"Content-Type": "application/json"},
    "body": "{\"query\":\"test\"}"
  },
  "scenario_id": "cc_attack",
  "node_ids": ["attacker-http-01"]
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
  "max_rps": 10000,
  "max_concurrent": 5000,
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
  "details": {"attack_type": "http_flood", "target": "10.100.10.10", "rps": 2000},
  "success": true
}
```

---

## 4. 安全架构

### 4.1 信任边界
```
┌────────────────────────────────────────┐
│          管理平面 (Controller)           │
│  - 场景编排  - 熔断决策  - 审计归档       │
└─────────────────┬──────────────────────┘
                  │ mTLS + Token
                  ▼
┌────────────────────────────────────────┐
│          控制平面 (Attacker Nodes)       │
│  - 指令执行  - 限流执行  - 结果上报       │
└─────────────────┬──────────────────────┘
                  │ 仅允许目标白名单
                  ▼
┌────────────────────────────────────────┐
│          数据平面 (Target Network)       │
│  - 被测防御系统  - 靶机业务              │
└────────────────────────────────────────┘
```

### 4.2 纵深防御措施

| 层级 | 措施 | 实现位置 |
|------|------|----------|
| 网络 | VLAN 隔离、防火墙策略 | 物理/虚拟网络 |
| 传输 | mTLS 1.2+、证书轮换 | Controller/Node |
| 应用 | Token 认证、白名单校验 | 双向 |
| 运行 | 非 root、Capability 限制 | Docker |
| 业务 | 全局限流、熔断、超时 | Orchestrator |
| 审计 | 结构化日志、ELK 接入 | Audit Logger |

---

## 5. 限流与熔断设计

### 5.1 三级限流
```
全局配额 (Controller)
    │
    ├── HTTP 类: GLOBAL_MAX_RPS = 50000
    ├── RAW 类:  GLOBAL_MAX_PPS = 100000
    └── 连接数:   GLOBAL_MAX_CONCURRENT = 100000
         │
         ▼
节点配额 (动态分配)
    │
    ├── per-node RPS/PPS/Concurrent
    └── TokenBucket 实现
         │
         ▼
攻击实例 (Worker 级)
    │
    └── 令牌桶精确控速
```

### 5.2 熔断状态机
```
NORMAL ──(触发)──► EMERGENCY_STOP
    ▲                    │
    │                    ▼
    └───(管理员复位)─── RESET
```

**触发条件**:
- 管理员手动触发 (`POST /emergency_stop`)
- 全局配额耗尽
- 节点大规模离线 (>50%)
- 审计日志写入失败

**熔断效果**:
- 拒绝新攻击指令
- 广播停止信号给所有节点 (<100ms)
- 现有攻击强制终止
- WebSocket 推送告警

---

## 6. 部署架构

### 6.1 单机测试模式
```yaml
# docker-compose.yml (项目根目录)
services:
  controller:
    build: ./controller
    ports: ["8443:8443"]
  
  attacker-http:
    build: ./attacker
    ports: ["8080:8080"]
  
  attacker-raw:
    build: ./attacker
    privileged: true
    # network_mode: host  # 如需发送伪造包
```

### 6.2 多机生产模式
```
Controller (10.100.1.10)          管理网络
    │
    ├── mTLS 8443
    │
Attacker HTTP (10.100.1.20)       攻击网络 A
    │
    ├── HTTP/HTTPS 流量
    │
Attacker RAW (10.100.1.21)        攻击网络 B (需直连目标)
    │
    ├── 原始套接字 (SYN/UDP)
    │
Target Network (10.100.10.0/24)   靶机网络
    │
    └── 被测 WAF/应用/防火墙
```

### 6.3 证书分发
```
certs/
├── ca-cert.pem              # 所有节点信任
├── controller-cert.pem      # Controller 证书
├── controller-key.pem       # Controller 私钥
└── nodes/
    ├── attacker-http-01/
    │   ├── ca-cert.pem
    │   ├── node-cert.pem
    │   └── node-key.pem
    └── attacker-raw-01/
        ├── ca-cert.pem
        ├── node-cert.pem
        └── node-key.pem
```

---

## 7. 扩展指南

### 7.1 新增攻击类型
1. 在 `app/attacks/` 创建 `new_attack.py`
2. 继承 `SafeAttackBase`，实现 `_run()` 方法
3. 定义 `NAME`、`ATTACK_TYPE`、`REQUIRES_ROOT`
4. 在 `app/attacks/__init__.py` 导入触发注册
5. 在 `scenarios/` 添加 YAML 场景

### 7.2 新增防御指标采集
1. Attacker `health.py` 添加采集逻辑
2. `NodeHeartbeat` 模型添加字段
3. Controller `websocket.py` 广播新指标
4. Grafana Dashboard 添加面板

### 7.3 对接外部系统
| 系统 | 接口 | 方式 |
|------|------|------|
| SIEM | 审计日志 | Filebeat → Logstash → Elasticsearch |
| CMDB | 节点资产 | Controller 定时同步 API |
| Ticket | 熔断工单 | Webhook → JIRA/钉钉/企微 |
| 脆弱性扫描 | 靶机标记 | 扫描完成回调 Controller 更新标签 |

---

## 8. 性能基准

### 8.1 单节点极限 (参考值)
| 攻击类型 | 硬件 (8C16G) | 理论峰值 | 实测峰值 |
|----------|--------------|----------|----------|
| HTTP Flood | 8C16G, 千兆 | 50k RPS | ~35k RPS |
| Slowloris | 8C16G | 5k 并发 | ~3k 并发 |
| SYN Flood | 8C16G, 万兆 | 500k pps | ~200k pps |
| UDP Flood | 8C16G, 万兆 | 1M pps | ~400k pps |

### 8.2 水平扩展
- Controller 无状态，可部署多实例 + Redis 共享状态
- Attacker 无状态，横向扩展线性增长
- 建议: 每 10 个 Attacker 配 1 个 Controller 实例

---

**文档版本**: v1.0  
**架构版本**: DDoS Attack Platform v1.0  
**编写日期**: 2024-01-15  
**评审者**: 架构组、安全组、运维组