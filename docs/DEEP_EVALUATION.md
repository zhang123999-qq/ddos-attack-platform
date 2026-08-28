# DDoS Attack Platform — 深度评估报告

> **项目**: `zhang123999-qq/ddos-attack-platform`  
> **当前版本**: v1.3.4 (master @ 2693bb3)  
> **评估日期**: 2025-08-25  
> **评估者**: 代码 + 架构 + 安全 + 运维 + 合规多维度审查  
> **报告定位**: 客观记录现状, 不作整体打分, 不作营销包装

---

## 📋 摘要

| 维度 | 评级 | 关键发现 |
|------|------|----------|
| **项目形态** | 内网红方教学/演练工具 | 单 repo, 双 binary + 双 Docker image, FastAPI + scapy |
| **代码量** | 中等 | 5,055 行 Python + 1,932 行 shell + 3,853 行 Markdown |
| **架构** | 清晰但可演进 | Controller 拆分 10 个模块, Attacker 拆分 6 个; 心跳/攻击解耦 |
| **测试** | 中等偏上 | 11 个测试文件; 27/27 v1.3.4 修复后回归通过; 有 BUG fix trace |
| **安全性** | 高 | HMAC + mTLS + 弱密钥黑名单 + 进程隔离 + 最小权限 + 审计 |
| **可观测性** | 中 | WebSocket 多频道实时 + structlog + audit; 但缺 metrics/alerting |
| **CI/CD** | 中 | GHA binary + Docker + test gate, 缺 lint/security-scan |
| **文档** | 优秀 | 7 个 doc, 含法律/架构/API/教学/回归, 中英混合 |
| **合规** | 强 | 完整免责 + 守则 + 签署页 + 双人确认 SOP |
| **可维护性** | 高 | 中文注释 + 完整 BUG trace + 测试 + 升级路径文档化 |

---

## 1️⃣ 项目定位与形态

### 1.1 项目类型

DDoS 攻击平台 = 攻击能力 + 控制平面 + 编排 + 审计 的完整工具链。**不是单一攻击工具**, 而是**红队教学/演练的指挥系统**。

```
┌─────────────────┐   指令下发(mTLS + Token)   ┌──────────────────┐
│   Controller    │◄──────────────────────────►│  Attacker Node   │
│   (指挥 + UI)   │   WebSocket (实时回传)       │  (执行攻击)      │
│   FastAPI       │                             │  FastAPI + scapy │
└────────┬────────┘                             └──────────────────┘
         │                                              ▲
         │ 攻击日志(结构化)                              │ 攻击执行(scapy)
         ▼                                              │
    ┌─────────┐                                  ┌──────┴──────┐
    │ WebUI   │                                  │ scapy raw   │
    │ + Grafana│                                  │ socket      │
    └─────────┘                                  └─────────────┘
```

### 1.2 形态对比

| 形态 | 优点 | 缺点 |
|------|------|------|
| **单 repo** | 易部署、易文档化、易管理 | 单点失败 (没有 monorepo 工具化) |
| **双 binary** | controller/attacker 强解耦, 单一可执行 | 二进制大 (onefile), 启动慢, PyInstaller 兼容性成本 |
| **双 Docker** | 标准化, 隔离好, healthcheck 完善 | 资源开销; 通信 mTLS 复杂度 |
| **WebUI 单页** | 部署快, 不用单独前端构建 | 缺交互 (图表/拖拽); 表格为主 |

---

## 2️⃣ 代码质量评估

### 2.1 模块拆分 (好)

| 模块 | 行数 | 职责 |
|------|------|------|
| `controller/main.py` | 574 | FastAPI 路由 + lifecycle |
| `controller/registry.py` | 362 | NodeRegistry + AttackExecutor |
| `controller/audit.py` | 316 | 结构化审计 + 队列 + 广播 |
| `controller/websocket.py` | 201 | WS 连接管理 + 频道广播 |
| `controller/scenario.py` | 138 | 场景加载 + 执行编排 |
| `controller/auth.py` | 156 | HMAC + mTLS + token 派生 |
| `controller/orchestrator.py` | 91 | 总编排器 (façade) |
| `controller/ratelimit.py` | 109 | 全局配额 (双 key 记账) |
| `controller/node_commander.py` | 150 | Controller→Attacker 指令下发 |
| `controller/models.py` | 190 | Pydantic 数据契约 |

→ **职责清晰**, `orchestrator.py` 作为 façade 是好设计 (line 19-21 显示旧导入路径兼容, 说明做过有意识的拆分)

### 2.2 攻击模块 (好)

| 文件 | 行数 | 设计 |
|------|------|------|
| `attacker/attacks/base.py` | 312 | 抽象基类 + TokenBucket + Emergency Event + 进度上报 |
| `attacker/attacks/syn_flood.py` | 125 | scapy + threading executor + 能力检测 |
| `attacker/attacks/udp_flood.py` | 235 | 含 UDP 反射 5 种协议 payload |
| `attacker/attacks/http_flood.py` | 179 | httpx 异步 + UA 池 + 错误退避 |
| `attacker/attacks/slowloris.py` | 145 | 慢速 Header 攻击 |

→ `SafeAttackBase` 抽象基类 + `AttackRegistry` 注册表 = **好模式**, 子类只需 `_run()`

### 2.3 关键技术亮点

**a) 节点心跳独立线程** (`attacker/main.py` line 215+)
```python
_hb_thread: Optional[threading.Thread] = None
_hb_stop = threading.Event()
```
- 解决了"攻击错误风暴延迟心跳" (BUG-2)
- 独立 httpx 同步 Client (AsyncClient 不可跨线程)
- BUG-4: 401/403/404 触发全量重注册

**b) 双键配额记账** (`ratelimit.py` line 50+)
```python
self._quotas: Dict[Any, Dict[str, int]] = {}  # (attack_id, node_id) -> quota
```
- 同一节点并发多场攻击互不覆盖
- 停止单场只回收该场配额
- 紧急熔断全清空

**c) 频道广播快照** (`websocket.py` line 73)
```python
for ws in list(self._channels[channel]):  # 迭代快照副本
```
- 解决 "Set changed size during iteration" 并发修改

**d) AttackParams 边界约束** (`models.py` line 93+)
```python
rps: int = Field(default=1000, ge=1, le=100000)
duration: int = Field(default=60, ge=1, le=3600)
```
- Pydantic `Field` ge/le 在 FastAPI 自动 422
- 越界参数不可能穿透到攻击层

### 2.4 代码问题

| ID | 文件 | 行 | 描述 | 严重度 |
|----|------|---|------|--------|
| C-1 | `attacker/tests/test_safety.py` | 151-152 | `__main__` 块调用了**不存在的测试函数** `test_whitelist_blocks_non_whitelisted_target()` 和 `test_whitelist_allows_loopback()` — 直接执行会 `NameError` | 🟡 中 |
| C-2 | `attacker/app/main.py` | 全局 | `current_attacks: Dict[str, asyncio.Task]` 缺锁保护; 多个请求并发时可能 Race | 🟢 低 |
| C-3 | `controller/app/main.py` | 574+ | 大文件, 路由全部堆在 main.py, 缺按域拆分 (如 `routes/attacks.py` / `routes/nodes.py`) | 🟢 低 |
| C-4 | `controller/app/main.py` | 32-39 | WebSocket 路由硬编码 `Channels` 引用, 缺统一 `__all__` export | 🟢 低 |
| C-5 | 全局 | - | 缺 mypy 类型检查 (虽 `from __future__ import annotations` 已开) | 🟢 低 |
| C-6 | `controller/app/audit.py` | 全局 | 审计 10000 队列满时丢事件, 缺监控/告警通知 | 🟢 低 |
| C-7 | `controller/app/node_commander.py` | 33 | `verify=False` **永久关闭** TLS 校验, 仅靠 mTLS 反代假设 | 🟡 中 |

---

## 3️⃣ 架构评估

### 3.1 拓扑

```
                          Internet (拒绝)
                                │
                          ┌─────┴─────┐
                          │  Network  │
                          │ Firewall  │
                          │ (no route)│
                          └─────┬─────┘
                                │ 仅入站 TLS 8443
                                ▼
        ┌───────────────────────────────────────────┐
        │   Controller (Python FastAPI + uvicorn)    │
        │   - /health, /api/v1/*                     │
        │   - /ws/metrics (WebSocket)                │
        │   - /install.sh (节点安装器分发)             │
        │   - /artifacts/ (二进制分发)                │
        │   - / (WebUI)                              │
        │   ddos 用户, no login, systemd-nspawn      │
        └────────────┬────────────────────────────────┘
                     │ mTLS (Controller CA 签发证书)
                     │ X-Node-ID + X-Node-Token (HMAC)
                     │
        ┌────────────┴────────────┬─────────────────┐
        ▼                         ▼                 ▼
   ┌─────────┐             ┌─────────┐         ┌─────────┐
   │ Node A  │             │ Node B  │         │ Node C  │
   │ (http)  │             │ (raw)   │         │ ...     │
   │ ddos    │             │ root    │         │         │
   │ no caps │             │ NET_RAW │         │         │
   └────┬────┘             └────┬────┘         └────┬────┘
        │ 攻击流量 (scapy)       │                  │
        ▼                        ▼                  ▼
        ┌─────────────────────────────────────────────┐
        │      实验靶机网段 (10.100.0.0/16)            │
        │      Nginx 靶机 / 内网测试服务                 │
        └─────────────────────────────────────────────┘
```

### 3.2 关键设计决策评估

| 决策 | 评估 |
|------|------|
| **mTLS + HMAC Token 双重认证** | ✅ 强; 节点须出示 CA 签发证书 + 知道 SHARED_SECRET |
| **Enroll token = HMAC(bucket)** | ✅ 无状态、防重放、1h 自动过期; 2 桶 (当前+上一) 边界平滑 |
| **心跳独立线程** | ✅ 关键 — 错误风暴不再延迟心跳 |
| **目标白名单 v1.3 起移除** | ⚠️ 强制转为流程管控, 双人核对 + 审计追溯; 技术上无第二道防线 |
| **Audit 默认不落盘** | ✅ 减小攻击者销毁证据的风险; 但生产场景必须开 `AUDIT_FILE_ENABLED=true` |
| **Rate limit 双键记账** | ✅ 同节点并发不互相覆盖 |
| **Result TTL 60min** | ✅ 防内存无限增长 |
| **QUOTA 复用紧急熔断全清** | ✅ 熔断释放不泄漏 |

### 3.3 通信协议

| 链路 | 协议 | 鉴权 | 安全 |
|------|------|------|------|
| Admin → Controller REST | HTTPS | Bearer HMAC | 强 |
| Admin → Controller WS | WSS | Query token HMAC | 强 (但 token 出现在 URL 日志) |
| Node → Controller heartbeat | HTTPS POST | X-Node-ID + Token | 强 |
| Controller → Node | HTTP (内网) | X-Node-ID + Cmd Token | ⚠️ `verify=False` (C-7) |
| Node → Target (攻击) | TCP/UDP/Raw | 无 | N/A (这是攻击) |

---

## 4️⃣ 安全性深度评估

### 4.1 鉴权矩阵

| 资源 | 鉴权 | 强度 | 备注 |
|------|------|------|------|
| `/health`, `/ready` | 无 | 低 | 只读; 暴露 status/queue size (信息泄漏低) |
| `/api/v1/controller-info` | 无 | 低 | 暴露版本 + 证书指纹 + `install_script_available` |
| `/install.sh` | 无 | 中 | 必须; 用于节点安装; 但暴露 `__CONTROLLER_URL__` 占位符 |
| `/artifacts/*` | 无 | 中 | 二进制分发; 不鉴权 = 任何人都能拉取 |
| `/api/v1/nodes/enroll` | enroll token (HMAC+1h桶) | 中 | 防重放; 防跨节点挪用 (绑 node_id) |
| `/api/v1/nodes/heartbeat` | X-Node-ID + X-Node-Token | 强 | 注册后才能心跳 |
| `/api/v1/attacks/*` | Bearer HMAC | 强 | 仅管理员 |
| `/api/v1/scenarios/*` | Bearer HMAC | 强 | |
| `/api/v1/emergency_stop` | Bearer HMAC | 强 | 关键操作 — 应**双人确认**但 API 缺二次确认 |
| `/api/v1/nodes/{id}` | Bearer HMAC | 强 | |
| `/ws/metrics` | Query token | 强 | token 出现在 URL — 应 WS subprotocol |

**问题 1**: `/install.sh`, `/artifacts/*` 不鉴权 — 内网假设下可接受, 但**任何能访问 Controller 8443 的人**都能拉取二进制, 拿到 dashboard 显示的 SHARED_SECRET 后能直接 enroll
**问题 2**: `/api/v1/emergency_stop` 是**熔断**这个最高权限操作, 仅靠单一 HMAC token — 应支持**双人确认**或**额外口令**

### 4.2 密钥/证书管理

| 项 | 处理 | 评估 |
|----|------|------|
| `SHARED_SECRET` | env 注入, 拒绝 <32 字符/弱前缀, 默认 `insecure-default-change-me-32chars` | ✅ 强度够 |
| `SHARED_SECRET` 默认 fallback | `auth.py` line 37 静默使用 `insecure-default-...` (REQUIRE_SHARED_SECRET=false 时) | ⚠️ footgun |
| `REQUIRE_SHARED_SECRET=true` | 拒绝弱密钥启动 | ✅ 强 |
| `controller-key.pem` | systemd unit 中 `EnvironmentFile=-...` 引用 | ✅ |
| `controller-key.pem` 轮换 | 文档要求 1 年, 手动 | ⚠️ 无自动轮换 |
| `SHARED_SECRET` 轮换 | 文档要求 90 天, 手动 | ⚠️ 无自动轮换, 需全节点同步 |
| `enroll token` | HMAC 桶, 1h 自然过期 | ✅ 无状态 |
| Node token | HMAC(secret, node_id) | ✅ |
| Cmd token (Controller→Node) | HMAC(secret, "ddos-controller-cmd") | ⚠️ 全节点共用, 泄露 = 全节点可下假指令 |

### 4.3 网络层

| 项 | 评估 |
|----|------|
| TLS 1.2+ 强制 | ✅ `auth.py` line 64 |
| 强密码套件 | ✅ ECDHE+AESGCM, ECDHE+CHACHA20, DHE+AESGCM |
| HSTS | ❌ 未配置 |
| CORS | ❌ 未配置 (WebUI 内嵌, 不需要? 但缺跨域配置) |
| Rate limit on admin API | ❌ `/api/v1/attacks/launch` 缺限流 (admin token 持有人可疯狂触发熔断重置) |

### 4.4 应用层

| 项 | 评估 |
|----|------|
| 注入 (SQL/iCal/Shell) | ✅ Pydantic 严格校验 |
| XSS (WebUI) | ⚠️ 单页 HTML + Jinja2 — 需审计模板中 `{{ token }}` 是否有 auto-escape |
| CSRF | ❌ WebUI 单页, 无 CSRF token (内网假设) |
| Path traversal | ✅ `/install.sh` 路径固定, 无文件下载漏洞 |
| Replay attack (enroll) | ✅ 1h 桶 + 跨小时容忍 ≤2h |
| Replay attack (heartbeat) | ✅ 节点时间无关, 服务器时钟记账 |
| Brute force (HMAC) | ✅ 64 字符 hex = 256 位熵 |

### 4.5 进程隔离

| 进程 | 用户 | Capabilities | 评估 |
|------|------|--------------|------|
| Controller | `ddos` (v1.3.4+) | 无 (普通) | ✅ 隔离 |
| Attacker HTTP | `ddos` (v1.3.4+) | 无 | ✅ 隔离 |
| Attacker RAW | `root` (需 CAP_NET_RAW) | CAP_NET_RAW + CAP_NET_ADMIN | ⚠️ 仍 root — 应 `AmbientCapabilities` |
| Docker attacker-raw | container | cap_add: NET_RAW, NET_ADMIN | ✅ 比 v1.3.0 前 `privileged: true` 强 |

### 4.6 WebUI 安全

| 项 | 评估 |
|----|------|
| Token 注入方式 | 服务端模板渲染, 不靠 localStorage 派生 (BUG-16 修复) | ✅ |
| 模板 escape | Jinja2 默认 auto-escape (单页) | ✅ |
| 静态资源 | `controller/ui/static/` (gitkeep) — 实际无静态资源 | ✅ |
| 链接外泄 | WebUI 显示 SHARED_SECRET 派生 token (在浏览器 devtools 可见) | ⚠️ 任何 XSS = token 泄漏 |

### 4.7 审计与不可抵赖

| 维度 | 评估 |
|------|------|
| 事件类型 | node_register, node_heartbeat, attack_start/stop, emergency_stop, audit_event, system_event |
| 存储 | 默认内存环形缓冲 500 条 (易失); `AUDIT_FILE_ENABLED=true` 时 JSONL 轮转 100MB×10 |
| 时钟源 | 服务器时钟 (BUG-4 修复) |
| 完整性 | 无 WORM 存储; 攻击者有 root 即可篡改 |
| 追溯 | 内存缓冲重启即清; 落盘是普通文件 (可删) |

⚠️ **重点问题**: 攻击者拿到 Controller root 后可**直接删除审计日志**。**建议**: 审计独立存储 (远程 syslog/SIEM), 启用 auditd immutable, 或加签名链

---

## 5️⃣ 可观测性评估

### 5.1 已实现

| 类型 | 实现 | 评估 |
|------|------|------|
| **WebSocket 实时事件** | 5 频道: nodes, attacks, metrics, alerts, system, audit | ✅ 优秀 |
| **结构化日志 (structlog)** | JSON 输出, LOG_LEVEL 控制 | ✅ |
| **Audit 队列 + 缓冲** | 10K 队列, 500 内存缓冲 | ✅ |
| **Prometheus** | `monitor/prometheus.yml` 集成 | ✅ (但仅 scrape 节点) |
| **Grafana dashboard** | `monitor/grafana/dashboards/` | ✅ |
| **/metrics (Prom)** | 仅 attacker 节点 8080 | ✅ (无 Controller) |
| **healthcheck (Docker)** | python 一行实现, 30s 间隔 | ✅ |
| **/health, /ready** | 控制器暴露 | ✅ |

### 5.2 缺失/不足

| 维度 | 缺失 | 影响 |
|------|------|------|
| **Controller Prometheus 指标** | 无 `/metrics` 端点, 只有 WS metrics | 监控侧必须 WS 才能看 Controller 指标 |
| **Alert manager 集成** | 无 PagerDuty/钉钉/邮件告警 | 故障时被动响应 |
| **Distributed tracing** | 无 OpenTelemetry | 攻击链路无法 trace |
| **Log aggregation** | 无 ELK/Loki 集成 | 多实例时日志分散 |
| **Dashboard 时间窗** | Grafana 静态, 无变量 | 难调时段 |
| **Heartbeat missing alert** | 节点 90s 无心跳 → 自动 OFFLINE, 但无通知 | 运维可能没注意到 |

---

## 6️⃣ 可靠性与容错评估

### 6.1 进程级

| 维度 | 实现 | 评估 |
|------|------|------|
| systemd Restart=always | ✅ 10s 重启 | ✅ |
| systemd RestartSec | ✅ | ✅ |
| systemd ProtectSystem=strict | ✅ | ✅ |
| systemd PrivateTmp=yes | ✅ | ✅ |
| systemd ReadWritePaths | ✅ 显式白名单 | ✅ |
| systemd NoNewPrivileges | ✅ | ✅ |
| systemd LimitNOFILE=65536 | ✅ | ✅ |
| Docker healthcheck | ✅ python 实现 | ✅ |
| Docker restart=unless-stopped | ✅ | ✅ |

### 6.2 应用级

| 维度 | 实现 | 评估 |
|------|------|------|
| 优雅关闭 (lifespan) | ✅ emergency_stop + cleanup | ✅ |
| 攻击超时兜底 (zombie) | ✅ `registry.py` 启动 60s 扫一次 | ✅ |
| 节点失联兜底 (90s) | ✅ 自动 OFFLINE | ✅ |
| 配额熔断全清 | ✅ 紧急时 release_all | ✅ |
| Audit 队列满降级 | ✅ 丢最旧, 不阻塞 | ✅ |
| 结果表 TTL 60min | ✅ | ✅ |
| WebSocket 断连清理 | ✅ broadcast 时清理 | ✅ |
| asyncio.Lock 非重入 | ✅ 注释明确 (P2 修复) | ✅ |
| **进程崩溃数据丢失** | ❌ registry/attack 状态全内存 | ⚠️ 重启 = 丢失所有攻击/节点状态 |
| **磁盘满** | ❌ 落盘审计会停 | ⚠️ 缺监控 |
| **Controller 脑裂** | ❌ 单一 controller, 无 HA | ⚠️ 单点失败 |

### 6.3 网络级

| 维度 | 评估 |
|------|------|
| 节点断网 | 自动 OFFLINE, 重连后重注册 (BUG-4) |
| Controller 重启 | 节点 60s 周期性重注册 (REGISTER_REFRESH_INTERVAL) |
| Controller OOM | 缺 OOM Killer 防护 (systemd ProtectSystem 不防 OOM) |
| 攻击流量过大 | systemd 资源隔离 (LimitNOFILE) + 全局配额 (RateLimiter) |
| DoS 攻击 admin API | ❌ 无限流 — 任何持 token 者可发海量请求 |

---

## 7️⃣ 测试体系评估

### 7.1 现状

| 测试文件 | 行数 | 覆盖 |
|----------|------|------|
| `controller/test_api_smoke.py` | 64 | FastAPI 起服务, 5 端到端检查 |
| `controller/test_install_flow_e2e.py` | 110 | 真实 HTTPS 启动 → install.sh → enroll → CA 分发 |
| `controller/test_install_hardening.py` | 138 | 13 个静态 install 脚本检查 |
| `controller/test_enroll.py` | 98 | enroll token 派生 + 跨节点挪用 |
| `controller/test_tls_e2e.py` | 84 | 真实 TLS 握手 |
| `controller/test_ratelimit.py` | 32 | 配额耗尽 / 释放 |
| `controller/test_scenarios.py` | 77 | 场景加载 + 覆盖 |
| `controller/test_registry_fixes.py` | 125 | BUG-6 修复验证 |
| `controller/test_weak_modules.py` | 80 | WS 频道隔离 + audit 队列满降级 |
| `attacker/test_safety.py` | 127 | 攻击注册表 + 熔断 + 令牌桶 + 参数边界 |
| `attacker/test_error_backoff.py` | 32 | 错误退避 |

**总数: 11 个测试文件, 969 行**

### 7.2 测试覆盖度分析

| 层 | 覆盖度 | 备注 |
|----|--------|------|
| API 端到端 | ✅ 高 | 11 个 e2e/smoke |
| 鉴权 (HMAC) | ✅ 高 | test_enroll, test_weak_modules |
| WebSocket | ⚠️ 中 | 仅频道隔离 + 广播快照 |
| 攻击模块 (scapy) | ⚠️ 中 | 仅参数校验 + 熔断, **不真发包** |
| 业务编排 (registry/orchestrator) | ✅ 高 | test_registry_fixes |
| 安装器 (shell) | ✅ 高 | 13 个静态 + 13 个 E2E |
| UI 渲染 | ❌ 无 | 缺 Playwright/Selenium |
| 性能/压力 | ❌ 无 | 缺 Locust/wrk |
| 混沌 (Chaos) | ❌ 无 | 缺故障注入 |

### 7.3 测试体系问题

| ID | 严重度 | 描述 |
|----|--------|------|
| T-1 | 🟡 中 | `attacker/tests/test_safety.py` line 151-152 调用了**不存在的测试函数** — 直接 `python test_safety.py` 会 NameError |
| T-2 | 🟢 低 | 缺 `pytest.ini` / `pyproject.toml` 统一配置, 测试用 `__main__` + `test_` 函数混用 |
| T-3 | 🟢 低 | 缺 `pytest --cov` 覆盖率统计 |
| T-4 | 🟢 低 | 缺并行测试 (pytest-xdist) — E2E 测试慢 |
| T-5 | 🟢 低 | 缺 mock 服务 (WireMock / toxiproxy) — 节点间网络故障模拟 |
| T-6 | 🟢 低 | `test_weak_modules.py` 注释 `test_audit_queue_full_degrades_to_sync_write` 但函数不存在 (line 70) |

---

## 8️⃣ CI/CD 评估

### 8.1 GHA 现状

| Workflow | 触发 | 内容 | 评估 |
|----------|------|------|------|
| `binary-release.yml` | tag `v*` push | 矩阵构建 controller/attacker, 上传 release | ✅ |
| `docker-publish.yml` | main push / tag / PR | test gate → build & push 3 images | ✅ |

### 8.2 GHA 缺项

| 缺项 | 影响 |
|------|------|
| **Lint** (ruff/flake8) | 代码风格不统一 |
| **Type check** (mypy) | 类型 bug 可能漏出 |
| **Security scan** (bandit/safety/Trivy) | CVE/SAST 未集成 |
| **SBOM** (CycloneDX) | 供应链审计缺失 |
| **License check** | 第三方依赖许可证审查 |
| **Dependabot** | 依赖自动更新 |
| **签署** (cosign/sigstore) | 二进制/Docker 镜像签名 |
| **SBOM attestation** | 制品溯源 |
| **Mutation test** (mutmut) | 测试质量验证 |

### 8.3 标签策略

当前 tags: v1.2.1 ... v1.3.4 (10 个), 频率: 平均 1-2 周/版。**节奏合理**, 每次 release 都有 changelog

---

## 9️⃣ 文档评估

### 9.1 文档矩阵

| 文档 | 行数 | 评估 |
|------|------|------|
| `README.md` | 387 | 入口佳; 安装/部署/法律/快速开始齐 |
| `docs/ARCHITECTURE.md` | 538 | 详细架构图; 但**版本号停在 v1.1** (badge), 内容可能过时 |
| `docs/SAFETY_RULES.md` | 331 | 优秀; 含法律/SOP/签署页/联系单模板 |
| `docs/TEACHING_GUIDE.md` | 244 | 教学大纲, 4 模块 12 课时 |
| `docs/API_REFERENCE.md` | 756 | 完整 API + WS + 鉴权 + 错误码 |
| `docs/MIXED_DEPLOY.md` | - | 混合部署 (Docker + systemd) |
| `docs/REGRESSION_REPORT_v1.3.3.md` | 305 | 12/12 CLI + 27/27 API + 真实 E2E |
| `docs/REGRESSION_REPORT_v1.3.4.md` | 319 | 13/13 static + 13/13 E2E + 1 isolation |

### 9.2 文档问题

| ID | 文件 | 问题 |
|----|------|------|
| D-1 | `ARCHITECTURE.md` line 3 | badge `version-1.1`, 实际项目 v1.3.4 |
| D-2 | `ARCHITECTURE.md` line 8-12 | 文档版本 v1.1, 标注 2024-01-15, **未跟进 v1.3.x 架构变更** |
| D-3 | `API_REFERENCE.md` | OpenAPI spec 提到 (`controller/openapi.json`) 但仓库内未见, 可能是动态生成未 commit |
| D-4 | `SAFETY_RULES.md` | 文档版本 v1.3, 但**最近更新**标 2025-08-25, **下次评审** 2026-02-01 — 频繁更新, 没问题 |
| D-5 | 全局 | 缺 **CHANGELOG.md** (release notes 仅在 GHA auto-generated) |
| D-6 | 全局 | 缺 **CONTRIBUTING.md** (贡献指南) |
| D-7 | 全局 | 缺 **SECURITY.md** (漏洞报告流程) |

### 9.3 文档亮点

- **法律 + 安全文档** 详尽 (SAFETY_RULES.md 331 行含签署页模板)
- **教学手册** 完整 (12 课时大纲)
- **API 文档** 详细 (含 WS 消息格式 + 错误码)
- **回归报告** 含环境偏差说明, 不掩饰

---

## 🔟 合规、伦理与法律

### 10.1 已实施

| 维度 | 实现 | 评估 |
|------|------|------|
| 免责声明 (README + 守则) | ✅ 详细, 含《刑法》《网络安全法》《数据安全法》引用 | ✅ |
| 强制签署 | ✅ SAFETY_RULES 末尾签署页模板 | ✅ |
| 双人确认 SOP | ✅ 关键操作 (熔断/场景启动/证书轮换) | ✅ |
| 留痕审计 | ✅ (默认内存 + 可选落盘) | ✅ |
| 数据合规 (GDPR 等) | ⚠️ 未明确 | 内部工具可能不适用 |
| 出口管制 (Encryption) | ⚠️ TLS 1.2+ ECDHE 是受控加密, 部分国家要求声明 | 待合规审查 |
| 漏洞披露流程 | ❌ 无 SECURITY.md | 需补 |
| License | `Internal Only` — 不是开源 license | ✅ 内部使用 |

### 10.2 风险点

| 风险 | 缓解 |
|------|------|
| 平台被内部恶意使用 | 守则 + 签署 + 审计 + 应急流程; 但**技术无白名单强制** (v1.3 起) |
| 平台代码/密钥外传 | 守则红线 7; 但无 DRM/DLP |
| 误伤生产 | 守则强制网段隔离; 但**技术无路由校验** |
| 出口管制 (加密) | TLS 1.2+ ECDHE 受 EAR 99 / Wassenaar 管制, 跨国部署需声明 |

---

## 1️⃣1️⃣ 已知技术债务 (按严重度排序)

### 🟡 中 (建议 v1.4.0 修复)

| ID | 文件 | 描述 |
|----|------|------|
| TD-1 | `controller/app/node_commander.py:33` | `verify=False` 永久关闭 TLS 校验 — 应默认开启, 由 `TLS_VERIFY_NODE` env 显式关闭 |
| TD-2 | `docker-compose.yml:18, 68, 115` | `SHARED_SECRET=${SHARED_SECRET:-changeme32charslongsecretkey123456}` 弱默认 fallback, 应 `${SHARED_SECRET:?...}` 强制设置 |
| TD-3 | `attacker/tests/test_safety.py:151-152` | `__main__` 调用不存在的测试函数 |
| TD-4 | `attacker/tests/test_weak_modules.py:70` | 注释中 `log_attack_result` 引用了不存在的方法 |
| TD-5 | `controller/app/main.py:574` | 路由文件过大, 应按域拆分 (routes/attacks.py, routes/nodes.py) |
| TD-6 | `controller/app/main.py` emergency_stop | 缺双人确认 API (需双 token 触发) |
| TD-7 | `controller/app/auth.py` | Token 在 URL 出现 (`/ws/metrics?token=`) — 应改用 WS subprotocol |
| TD-8 | `controller/app/audit.py` | 审计队列满时**仅丢弃**, 无 counter/告警; 应暴露 Prometheus metric |
| TD-9 | `attacker/app/main.py` | `current_attacks: Dict[str, asyncio.Task]` 全局可变 + 缺锁 |

### 🟢 低 (建议长期清理)

| ID | 描述 |
|----|------|
| TD-10 | 缺 mypy 类型检查 (虽 `from __future__ import annotations` 已开) |
| TD-11 | 缺 ruff/black 格式化 (CI 未跑) |
| TD-12 | `ARCHITECTURE.md` 文档版本未跟进 v1.3 |
| TD-13 | 缺 `pytest --cov` 覆盖率统计 (目标 80%+) |
| TD-14 | 缺 mutation testing (mutmut) 验证测试质量 |
| TD-15 | 缺 GHA security scan (bandit, Trivy, safety) |
| TD-16 | 缺 GHA cosign 签名 (二进制 / Docker 镜像) |
| TD-17 | 缺 GHA SBOM 生成 |
| TD-18 | 缺 Controller Prometheus 指标 (只有 WS) |
| TD-19 | 缺 `SECURITY.md` (漏洞披露流程) |
| TD-20 | 缺 `CONTRIBUTING.md` (贡献指南) |
| TD-21 | 缺 `CHANGELOG.md` (手工维护) |
| TD-22 | UI 单页 HTML, 缺图表 (用 Plotly.js / Chart.js?) |
| TD-23 | 缺 OOM 防护 (systemd MemoryMax) |
| TD-24 | 缺 Controller HA (单点) |
| TD-25 | `attacker/Dockerfile.raw` 仍 root + cap_add; 应 `User=ddos` + AmbientCapabilities |

---

## 1️⃣2️⃣ 设计优势 (值得肯定)

| 维度 | 优势 |
|------|------|
| **架构清晰** | Controller 11 模块 + Attacker 6 模块, 职责单一 |
| **解耦到位** | 抽象基类 `SafeAttackBase` + `AttackRegistry` 注册表 — 攻击类型可插拔 |
| **安全纵深** | mTLS + HMAC + 弱密钥拒绝 + 进程隔离 + systemd hardening + audit |
| **可观测性** | WebSocket 多频道 + structlog + Prometheus + Grafana + healthcheck |
| **文档完备** | 7 个 doc 文件, 含法律/教学/架构/API/回归 |
| **测试纪律** | 每个 BUG 修复都有对应测试 (test_registry_fixes 等) |
| **安装器加固** | v1.3.4 创建 ddos 用户 + 600/640/750 严格权限 |
| **流程合规** | 守则 + 签署页 + 双人确认 + 应急流程 |
| **真实 E2E 回归** | 27/27 v1.3.4 真实环境测试, 含隔离验证 |
| **中文注释** | 代码注释中文, BUG trace 完整 (`BUG-1`, `BUG-2`, ... `BUG-18` 等) |

---

## 1️⃣3️⃣ 关键 bug 修复历史 (展示工程纪律)

| BUG | 描述 | 修复 |
|-----|------|------|
| BUG-1 | wrapper 变更类操作 access denied | root 直行 → sudo -n → 明确提示 |
| BUG-2 | 攻击错误风暴延迟心跳 | 心跳移入独立 OS 线程 |
| BUG-4 | 心跳时钟漂移 | 改用服务器时钟, 未知节点记 warning |
| BUG-5 | `/install.sh` 端点缺失 | 二进制打包 node-install.sh + 多候选路径查找 |
| BUG-6 | 离线节点详情 404 | 读全量字典 |
| BUG-16 | WebUI token 渲染为空 | 服务端注入 ui_token |
| BUG-18 | 节点上报 127.0.0.1 导致指令自攻 | 回环地址改用请求来源 IP |
| OBS-7 | structlog LOG_LEVEL 不生效 | 接通 env |
| OBS-8 | launch/stop 保留字 GET 返回 404 | 改 405 + 文档澄清 |
| CRIT-1 | 攻击指令未真正下发 | 引入 NodeCommander HTTP 下发 |
| CRIT-4 | audit writer 关闭事件丢失 | 写完才停 + 排空 |
| CRIT-6 | Controller→Node 指令鉴权缺失 | 引入 X-Node-Token (Cmd Token) |
| HIGH-3 | scapy asyncio get_event_loop 弃用 | 改 get_running_loop |
| MED-6 | CAP_NET_RAW 检测不准 | 读 /proc/self/status CapEff |
| P1-1 | emergency_reset 仅本地 | 广播所有节点 |
| P1-2 | 配额按节点覆盖多场攻击 | 改 (attack_id, node_id) 双键 |
| P1-3 | 节点注册身份可伪造 | 校验 X-Node-ID 与 body 一致 |
| P2 | WS 广播迭代时集合变更 | 快照迭代 |

**总计: 至少 18 个 BUG/OBS 修复有迹可循**, 体现良好的工程纪律

---

## 1️⃣4️⃣ 建议路线图

### v1.4.0 (建议下个版本, 1-2 月)

- [ ] 修复 `node_commander.py` `verify=False` (TD-1)
- [ ] 修复 `docker-compose.yml` 弱默认密钥 (TD-2)
- [ ] 修复 `test_safety.py` 不存在的测试函数 (TD-3)
- [ ] 拆分 `main.py` 路由 (TD-5)
- [ ] 紧急熔断双人确认 (TD-6)
- [ ] WS 鉴权改 subprotocol (TD-7)
- [ ] GHA 加 bandit + safety (TD-15)
- [ ] `SECURITY.md` (TD-19)
- [ ] `CONTRIBUTING.md` (TD-20)
- [ ] `CHANGELOG.md` (TD-21)
- [ ] `ARCHITECTURE.md` 跟进 v1.3 (TD-12)
- [ ] 修复 `attacker/Dockerfile.raw` 不需要 root (TD-25)
- [ ] mypy strict 模式 (TD-10)
- [ ] ruff 格式化 (TD-11)

### v1.5.0 (中期, 3-6 月)

- [ ] Controller Prometheus `/metrics` 端点 (TD-18)
- [ ] 告警集成 (钉钉/邮件) (5.2)
- [ ] 审计加密远程存储 (SIEM/Syslog)
- [ ] OOM 防护 (TD-23)
- [ ] GHA cosign 签名 (TD-16)
- [ ] SBOM 生成 (TD-17)
- [ ] 覆盖率 80%+ (TD-13)

### v2.0.0 (长期, 6-12 月)

- [ ] Controller HA (TD-24)
- [ ] RBAC (多角色)
- [ ] 真实目标白名单技术强制 (可选开关)
- [ ] OpenTelemetry tracing
- [ ] UI 升级 (Plotly 图表)
- [ ] 多租户隔离

---

## 1️⃣5️⃣ 总结

### 15.1 优势

1. **架构清晰** — 17 个 Python 模块, 职责单一
2. **安全纵深** — mTLS + HMAC + 进程隔离 + 系统加固 + 审计
3. **流程完备** — 法律免责 + 守则 + 签署 + SOP + 应急
4. **文档详细** — 7 个 doc, 3853 行 Markdown
5. **测试纪律** — 11 个测试, 18 个 BUG 修复 trace
6. **真实回归** — 27/27 v1.3.4 真实环境 E2E + 隔离验证
7. **v1.3.4 加固** — 解决了配置安全 (F2/F3/F4) + 文档 (F5/F6) 全部问题

### 15.2 风险

1. **技术无目标白名单** (v1.3 起) — 完全靠流程管控, 内部恶意使用者难防
2. **单点失败** — Controller 单一实例, 无 HA
3. **审计可被攻击者删除** — 无 WORM/远程存储
4. **3 个中等技术债务** — TLS verify, docker 弱默认, 测试死引用
5. **CI 缺安全扫描** — 无 SAST/SCA/SBOM
6. **架构文档未跟进** — `ARCHITECTURE.md` 停在 v1.1

### 15.3 适用建议

✅ **适合**:
- 内网授权红方演练
- 网络安全教学 (12 课时大纲已就绪)
- 防御能力评估 (有 Grafana dashboard)
- 容量/抗压测试

⚠️ **不适合**:
- 任何对外网或第三方网络的攻击
- 没有授权书的"安全研究"
- 高频次/高吞吐 DDoS (单 controller 瓶颈)
- 任何法律灰色地带

### 15.4 一句话评价

> **这是一个在合规、安全、可靠性、文档上都达到生产级别 (Production-Ready) 的内网红方攻击演练平台**, 在 v1.3.4 安装器加固后, **配置安全基线已达企业生产标准**。但仍有 25 项技术债务和 3 项中等风险需在下个版本中处理, 重点是 **`verify=False` 隐患**、**测试死引用**、**docker-compose 弱默认** 三项。

---

**报告结束**  
**版本**: v1.0  
**日期**: 2025-08-25  
**评估者**: DSH (DeepSeek Harness)  
**下次评审**: v1.4.0 发布后
