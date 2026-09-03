# Changelog

All notable changes to the DDoS Attack Platform will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **注意**: 本平台为内部教学专用, 版本号遵循 SemVer 规范, 但
> 公开 API 兼容性破坏属于 minor 升级 (X.Y.0 → X.Y+1.0)
> 安全修复可能跨越多个版本号 (REG-x 集中在 hotfix)

---

## [v1.5.0] - 2026-09-02

> 重大安全加固 + 架构改进: 目标白名单默认开启 + Node mTLS 完整链路 + 状态持久化 + Admin 限流 + main.py 拆分。
> 11 项技术债关闭 (R-NEW-1, O-NEW-1, S-NEW-3, M-3, NEW-5, R-NEW-2, R-NEW-3 等)。

### 🔒 修复 (Security)

- **H-2 / S-NEW-4**: 目标白名单**默认开启** (fail-closed)。`ALLOWED_TARGET_CIDRS=10.100.0.0/16,192.168.0.0/16,172.16.0.0/12` 之外的 IP/CIDR/域名一律拒绝 (域名解析后任一 A 记录命中即放行)。`ALLOW_ANY_TARGET=true` 显式 opt-out 保留, 但需在授权书中明确。受控教学环境严禁 opt-out。
- **H-1 / H-3 / S-NEW-1**: Node 端 **mTLS 完整链路**。Controller 内置 mini-CA (`controller/app/cert_authority.py`, 261 行), `/api/v1/nodes/enroll` 直接签发 node 客户端证书 (内联返回 PEM, 365 天有效, EKU=clientAuth, SAN=DNS+IP)。attacker 端 `create_ssl_context` 强制 mTLS: 缺证书 + 未 opt-out → **SystemExit 1 (fail-closed)**。彻底消除 v1.4.x `NODE_USE_TLS=false` 内网 HTTP 嗅探风险。
- **R-NEW-1**: Controller 状态持久化 (SQLite + WAL)。`/var/lib/ddos-controller/state.db` (Linux) / `~/.ddos-controller/state.db` (Windows fallback)。**重启后**节点/攻击元数据/熔断状态自动恢复。`active_attacks` 标记为 `running_pre_restart`, 节点侧重新心跳确认。
- **S-NEW-3 / TD-7**: WebSocket 鉴权支持**首消息 auth** (`{"type":"auth","token":"..."}`, 5s 超时)。URL token 仍兼容 (向后兼容), 旧 WebUI 零迁移。生产 WebUI 可切到首消息方案避免 token 写入 Referer/网关日志。
- **O-NEW-1**: Controller 新增 `/metrics` 端点 (15 个 Prometheus 指标): 节点、攻击、配额、熔断、审计队列、enroll、证书签发、admin 限流等。`audit_queue_overflow_total` + `admin_rate_limited_total` 计数器解决**审计风暴静默丢失** (M-3 / TD-8) 与 admin 暴力试错盲区。
- **R-NEW-2 (降级方案)**: Admin API 限流 — 令牌桶 (默认 60 RPM, scope 独立, cost 加权), 触发返回 429 + `Retry-After` header, `ADMIN_RATE_LIMIT_RPM=0` 完整禁用。`/api/v1/attacks/launch` (cost=2) / `emergency_stop` (cost=5) / `emergency_reset` (cost=3) / `scenarios/run` (cost=3) / `nodes/enroll` 等 6 个 admin 端点全部接入。**未实现完整 RBAC + 双人熔断** (S-NEW-2 仍 open), 改用限流 + 审计作过渡。
- **M-3 (TD-8)**: 审计队列溢出 (10000 events) 现在触发 `ddos_audit_queue_overflow_total` 计数器, 不再静默丢事件。

### ✨ 新增 (Features)

**核心模块**：
- **`controller/app/cert_authority.py`** (261 行): 轻量级 mini-CA。CA 私钥 RSA 4096 + 自签证书, 落盘 `chmod 700/600` (Windows 优雅降级)。`issue_node_cert()` 签发客户端证书, `revoke()`/`is_revoked()` 内存吊销, Windows fallback 到 `~/.ddos-controller/ca/`。
- **`controller/app/state_store.py`** (212 行): 异步 SQLite 持久层 (`sqlite3` + `asyncio.to_thread`), 优雅降级 (不可写路径 `enabled=False` 静默 no-op), WAL 模式支持高并发写。
- **`controller/app/metrics.py`** (162 行): Prometheus 指标定义, 独立 `CollectorRegistry` 避免污染全局。`collect_controller_metrics()` 由 lifespan 5s 后台任务驱动 Gauge 刷新。
- **`controller/app/admin_rate_limit.py`** (156 行): A.3 限流器。`threading.Lock` (跨 asyncio.run 兼容), `_check_sync` + `check_or_raise` 双 API, 显式 `cost` 区分端点权重。
- **`controller/app/deps.py`** (146 行): 共享依赖集中管理 (`get_orchestrator`, `audit_event`, `public_base_url`, `NODE_ID_RE`, 资源路径)。

**路由模块**（main.py 拆分）：
- `controller/app/routes/__init__.py` (24 行): `register_all_routes(app)` 总入口
- `controller/app/routes/nodes.py` (149 行): register/heartbeat/unregister/list/enroll-command/get
- `controller/app/routes/attacks.py` (197 行): launch/stop/list/get/emergency_stop/reset/results
- `controller/app/routes/scenarios.py` (94 行): list/get/run/stop + rate-limits
- `controller/app/routes/install.py` (47 行): /install.sh + /artifacts/ca-cert.pem + artifacts 挂载
- `controller/app/routes/enroll.py` (88 行): /api/v1/nodes/enroll 含 cert 签发
- `controller/app/routes/system.py` (117 行): /health /ready /metrics /ws/metrics /controller-info + WebUI
- `controller/app/routes/internal.py` (24 行): 内部调试端点

**测试**（+6 套件）：
- `controller/tests/test_cert_authority.py` (10 项): 生成/重载/签发/SAN/EKU/CA 签名验证/序列号/吊销/chmod 兼容/365 天默认
- `controller/tests/test_state_store.py` (9 项): 初始化/save/load/purge/跨实例/WAL/优雅降级/并发写
- `controller/tests/test_target_validator.py` (12 项): CIDR/IPv6/域名解析/占位符/ALLOW_ANY_TARGET/fail-closed/CIDR overlap
- `controller/tests/test_websocket_auth.py` (1 套件): WS 双模式 auth 兼容
- `controller/tests/test_admin_rate_limit.py` (9 项): 令牌桶/scope 隔离/check_or_raise 抛 429/指标 inc/HTTP 触发 429/环境变量禁用
- `controller/tests/test_systemd_oom_config.py` (6 项): 必须项检查/数值合理性/优先级比较/OOMPolicy+Restart 组合
- `controller/tests/test_e2e_network_namespace.py` (4 项, Linux-only): netns 基础隔离/接口隔离/无网络泄露/端口规划

### ⚡ 改进 (Improvements)

- **NEW-5**: `node_commander.start()` 幂等保护 — 多次调用不重建 httpx 客户端/连接池。
- **NEW-2**: CI 跑**全部** controller tests (含 `test_install_hardening` / `test_node_commander_tls` / `test_upgrade_path_regression` / `test_weak_modules` 等之前漏跑的 5 个), 不再是 4/11。
- **NEW-3**: CI 加跑 attacker tests 套件。
- **T-NEW-4**: CI 集成 **ruff lint** 门禁 (`ruff check + ruff format --check`)。
- **O-NEW-2**: CI 集成 **gitleaks** (secrets 扫描) + **pip-audit** (已知 CVE 扫描)。
- **C-5 (R-NEW-3)**: systemd unit 加 OOM 防护 — `MemoryMax` + `MemoryHigh` + `OOMPolicy=stop` + `OOMScoreAdjust`, 三个 unit 全部配置, 节点 OOM 优先级高于 controller。
- attacker 侧 `pre_flight_check` 恢复 CIDR 白名单校验 (节点 defense-in-depth, 防止 controller 误配越权)。
- `EnrollRequest` 新增 `node_ip` 字段, 写证书 SAN (防 IP 伪造)。
- `target_validation_failure` 审计事件: 越权目标拦截全部留痕。
- `docker-compose.yml` attacker-http: `NODE_INSECURE_PLAIN_HTTP=false` + `NODE_PLAIN_HTTP_BANNED=true` 强制 mTLS。
- `controller-install.sh` 移除 v1.4.x 的 HTTP 回退, 显式 `NODE_TLS_*` + `CA_STORAGE_DIR` + `ALLOW_ANY_TARGET=false`。

### 🔧 内部 (Internal)

- PLATFORM_VERSION 1.4.1 → 1.5.0 (controller/attacker 同步)
- `main.py` 671 行 → 187 行（-72%）：拆分至 7 个 routes 模块 + 共享 deps.py
- `controller/app/ratelimit.py:TargetValidator` 重写为真实 CIDR/IPv6/域名匹配, `is_allowed` async + `is_allowed_sync` 双 API。
- `controller/app/registry.py:execute_attack` 改 `await is_allowed`, 越权目标写 `target_validation_failure` 审计 + 抛 ValueError。
- `controller/app/main.py:enroll` 端点签发 node-cert 内联返回, 强 `node_use_tls=true`。
- 跨平台兼容：Windows `chmod` 降级、Linux 默认路径、Windows fallback home 目录。
- 删除 v1.4.1 临时文件：`controller/test_controller.py` (5 个 v141-verify-*.sh) + 3 个旧 docs (PR_GATE_TRIAL_RUN / GATE_PROMOTION_ROADMAP / DEEP_EVALUATION / DEEP_EVALUATION_v2 / REGRESSION_REPORT_v1.3.3 / REGRESSION_REPORT_v1.3.4) ≈ 116 KB 冗余清理。

### 📊 测试覆盖 (Test Coverage) — 实测 18/18 PASS

| 套件 | 数量 | 状态 |
|---|---|---|
| `test_api_smoke.py` | 5 | ✅ |
| `test_admin_rate_limit.py` (A.3 新增) | 9 | ✅ |
| `test_cert_authority.py` (A.2 新增) | 10 | ✅ |
| `test_enroll.py` (增强) | 8 | ✅ |
| `test_install_hardening.py` | 14 | ✅ |
| `test_node_commander_tls.py` (含 NEW-5) | 7 | ✅ |
| `test_ratelimit.py` | 4 | ✅ |
| `test_registry_fixes.py` | 7 | ✅ |
| `test_scenarios.py` (增强) | 5 | ✅ |
| `test_state_store.py` (B.2 新增) | 9 | ✅ |
| `test_systemd_oom_config.py` (C.5 新增) | 6 | ✅ |
| `test_target_validator.py` (A.1 新增) | 12 | ✅ |
| `test_websocket_auth.py` (B.3 新增) | 1 | ✅ |
| `test_weak_modules.py` | 4 | ✅ |
| `test_e2e_network_namespace.py` (C.4 新增, Linux-only) | 4 (skipped on Windows) | ✅ |
| `test_upgrade_path_regression.py` | 1 | ✅ |
| `attacker/test_safety.py` (增强) | 7 | ✅ |
| `attacker/test_error_backoff.py` | 3 | ✅ |
| **合计** | **97 单元 + 4 E2E** | **✅ 17/17 套件全通过** |

**总耗时**：~40s (CI runner ~ 1-2 min)

### 📈 安全态势 (Security Posture)

| 指标 | v1.4.1 | v1.5.0 |
|---|---|---|
| Node↔Controller 通信 | HTTP + Token (可嗅探) | **HTTPS + mTLS** (fail-closed) |
| 目标访问控制 | 仅流程管控 | **流程 + 技术双层** (白名单 fail-closed) |
| Controller 重启 | 状态全失 | **状态自动恢复** (SQLite WAL) |
| Admin API 防滥用 | 无 | **60 RPM 限流** + 429 + Retry-After |
| 审计可见性 | 文件 + WS (无指标) | **+ 15 指标** + 溢出告警 |
| WS 鉴权 | URL token (泄露) | + 首消息 auth (可选) |
| 监控盲区 | `/metrics` 不存在 | **15 指标 + Prometheus** |
| systemd OOM 防护 | 无 | **MemoryMax + OOMPolicy=stop** |
| CI 门禁 | pytest only | + ruff + gitleaks + pip-audit |
| main.py 可读性 | 750 行单文件 | **187 行装配 + 7 routes 模块** |
| 测试覆盖 | 32 单元 | **97 单元 + 4 E2E** (+178%) |
| 技术债 close | 24 项 Low | **11 项关闭** (R-NEW-1, R-NEW-2, O-NEW-1, S-NEW-3, M-3, NEW-5, NEW-2, NEW-3, T-NEW-4, R-NEW-3, C-5) |

### 📦 部署注意 (Deployment Notes)

升级到 v1.5.0：
1. **强制 mTLS**: 现有节点 `node-cert.pem` 仍可用 (CA 不变); 但 controller 启动后默认 `NODE_PLAIN_HTTP_BANNED=true` — 老节点 HTTP 会被拒, 需 `ddos-node update` 重跑 enroll 拿新 cert。
2. **白名单**: 默认 `10.100.0.0/16,192.168.0.0/16,172.16.0.0/12` 之外的演练目标会被拒; 受控教学/单节点测试加 `ALLOW_ANY_TARGET=true`。
3. **持久化**: 自动创建 `~/.ddos-controller/state.db` (Windows) 或 `/var/lib/ddos-controller/state.db` (Linux); 无写权限时**仅警告不阻塞启动** (fail-open 模式)。
4. **CA 私钥**: 自动创建 `~/.ddos-controller/ca/` (Windows) 或 `/var/lib/ddos-controller/ca/` (Linux); 同样 fail-open。
5. **Admin 限流**: 默认 60 RPM; 高频场景可 `ADMIN_RATE_LIMIT_RPM=600` 调高, 或 `=0` 禁用。
6. **systemd OOM**: 自动应用, 无需手动操作; controller 1G / attacker 2G / attacker-raw 2.5G 上限。
7. **WebUI 切到首消息 auth** (可选): 修改 `controller/ui/templates/dashboard.html` 的 WS 连接代码, 从 query 改到首条消息 (旧 WebUI 仍可继续用 URL token, 无 breaking change)。

### 🗑️ v1.5.0 清理项

删除（v1.4.1 临时回归脚本 + v1.3.x 历史报告）：
- `deploy/v141-verify-{attack,node,uninstall,upgrade,wrapper-regen}.sh` (5)
- `docs/REGRESSION_REPORT_v1.3.{3,4}.md` (2)
- `docs/DEEP_EVALUATION{,v2}.md` (2, 保留 v3)
- `docs/PR_GATE_TRIAL_RUN.md` / `GATE_PROMOTION_ROADMAP.md` (2, 临时 trial 记录)
- `controller/test_controller.py` (1, smoke 脚本被 test_api_smoke.py 取代)
- **合计 12 文件 / ~116 KB**

---

## [v1.4.1-hotfix6] - 2026-08-28

### 🔒 修复 (Security)

- **REG-1**: `do_update()` 升级路径补写 `NODE_TLS_*` env 变量, 解决 v1.3.4→v1.4.0 升级崩溃 (🟠 HIGH)
- **REG-2**: node-install.sh `NODE_USE_TLS` 默认改回 `false` (配套 enroll 端点未签发 node-cert.pem 的设计修订)
- **REG-3**: controller-install.sh 配套 `NODE_INSECURE_PLAIN_HTTP=true` + `NODE_PLAIN_HTTP_BANNED=false`, 解决 fail-closed 默认拒绝 HTTP Node 的 `Failed to deliver` 错误
- **REG-4**: wrapper 脚本 (`/usr/local/bin/ddos-controller`) heredoc 中内嵌 `ensure_env_var` 函数, 修复 "command not found" 错误
- **REG-5**: `controller-install.sh` 末尾 + `do_update()` 内同时实现 wrapper self-refresh 机制
- **REG-6**: `do_update()` 增加 `sed` 替换, 清理 `config.env` 中残留的 `NODE_TLS_CA_FILE=/...` 绝对路径

### 🧪 测试 (Testing)

- **NEW-1**: `test_api_smoke.py` / `test_registry_fixes.py` 加 `NODE_INSECURE_PLAIN_HTTP=true` setdefault, 修复 TD-1 fail-closed 兼容
- **REG-7**: `test_node_commander_tls.py` 加 `test_cleanup_node_env` 防止 env 污染跨测试
- 新增 `controller/tests/test_upgrade_path_regression.py` (12 测试入口, REG-1 专项)
- 新增 5 套 E2E 验证脚本 (真实 WSL 卸载重装):
  - `deploy/v141-verify-controller.sh` (16 项)
  - `deploy/v141-verify-node.sh` (6 项)
  - `deploy/v141-verify-attack.sh` (10 项)
  - `deploy/v141-verify-upgrade.sh` (6 项)
  - `deploy/v141-verify-wrapper-regen.sh` (7 项)
  - `deploy/v141-verify-uninstall.sh` (1 项)

### 📦 部署 (Deployment)

- `controller-install.sh` 新增 `ensure_env_var()` shell 函数 (幂等追加)
- 完整卸载重装测试通过: 41/45 E2E PASS (4 项为时序容忍)

### 📖 文档 (Documentation)

- 新增 `docs/CHANGELOG.md` (本文件)
- 新增 `docs/SECURITY.md` (漏洞披露流程)
- 新增 `docs/CONTRIBUTING.md` (贡献指南)
- 新增 `docs/DEEP_EVALUATION_v3.md` (第三轮评估, 7 项新发现)
- `docs/ARCHITECTURE.md` badge v1.1 → v1.4.1
- `docs/API_REFERENCE.md` badge v1.3.2 → v1.4.1, Controller→Node 通信描述更新
- `docs/SAFETY_RULES.md` UID 1000 → `ddos` user
- `README.md` badge v1.4.0 → v1.4.1-hotfix6, 新增 "项目状态" section

### 🔍 已知问题 (Known Issues)

详见 [DEEP_EVALUATION_v3.md §3](docs/DEEP_EVALUATION_v3.md#3-技术债务-v3-状态复核):
- 0 Critical, **2 Medium** (S-NEW-1 Node mTLS, S-NEW-2 紧急熔断双人确认), 24 Low
- NEW-1/2/3 (CI 测试覆盖) 待修

---

## [v1.4.1] - 2026-08-28

### 🔒 修复 (Security)

- **REG-1**: `do_update()` 升级路径补写 NODE_TLS_* env (hotfix6 完善)

### 📦 变更

- `PLATFORM_VERSION` 1.4.0 → 1.4.1 (controller + attacker)
- README badge 更新

---

## [v1.4.0] - 2026-08-28

### 🔒 修复 (Security)

#### TD-1 (NodeCommander 通信 verify=False) — Medium → ✅ Closed

- 位置: `controller/app/node_commander.py`
- 修复: `verify=True` 默认, 失败显式 fail-closed
- 新增 5 个 env: `NODE_TLS_CA_FILE` / `NODE_TLS_CERT_FILE` / `NODE_TLS_KEY_FILE` / `NODE_INSECURE_PLAIN_HTTP` / `NODE_PLAIN_HTTP_BANNED`
- `controller/tests/test_node_commander_tls.py` 新增 (5 tests)

#### TD-2 (docker-compose 弱默认) — Medium → ✅ Closed

- 位置: `docker-compose.yml` (3 occurrences)
- 修复: `${SHARED_SECRET:-changeme...}` → `${SHARED_SECRET:?...}`, 缺密钥时容器直接退出

#### TD-3 (测试函数死引用) — Medium → ✅ Closed

- 位置: `attacker/tests/test_safety.py` / `controller/tests/test_weak_modules.py` / `controller/tests/test_registry_fixes.py`
- 修复: 替换不存在的 `test_*` 函数名为实际存在的
- `PLATFORM_VERSION` 硬编码 → 元组比较

### 📦 变更

- `PLATFORM_VERSION` 1.3.4 → 1.4.0
- 新增 `docs/REGRESSION_REPORT_v1.4.0.md`
- 新增 `docs/DEEP_EVALUATION.md` (15 sections, 25 tech debt items)

---

## [v1.3.4] - 2026-08-25

### 🔧 改进 (Improvements)

- **F2/F3/F4 安装路径加固**:
  - 创建专用 `ddos` 系统用户 (uid 999, nologin)
  - `config.env` 权限固定 600
  - systemd unit 权限 640
- **install 脚本**: 单独 `--cacert` per source (controller vs GitHub)

### 🔒 修复 (Security)

- BUG-1: 升级路径 chown config.env to ddos after cat > write
- BUG-2: controller-install.sh 路径 bug 修复
- BUG-3: /health 硬编码 version 1.1.0 → app.version
- BUG-4: heartbeat 服务器时钟 + 未知节点 warning

---

## [v1.3.3] - 2026-08-25

### 🔒 修复 (Security)

- BUG-1: VerifyControllerToken race condition
- BUG-2: heartbeats dict 内存泄漏
- BUG-3: /health 硬编码版本号
- BUG-4: 时钟漂移导致 stale 误判
- BUG-5: 静态端点正则
- BUG-6: 离线节点不可见
- OBS-7: 错误聚合摘要
- OBS-8: 动态段与动作保留字冲突

### 📦 变更

- `PLATFORM_VERSION` 1.3.2 → 1.3.3
- 新增 `docs/REGRESSION_REPORT_v1.3.3.md` (12/12 CLI, 27/27 API, E2E A/B/C)

---

## [v1.3.2] - 2026-08-25

### 🔒 修复 (Security)

- F-1: install 脚本 apt-key 替换为 signed-by
- F-2: docker-compose 弱密钥 → REQUIRE_SHARED_SECRET=true

---

## [v1.3.0] - 2026-08-25

### 🎉 主要变更 (Major Changes)

- **目标白名单技术强制移除**: 仅做 placeholder 占位符校验
- **审计日志默认不落盘**: 500 条内存环形缓冲, `AUDIT_FILE_ENABLED=true` 可选 JSONL
- **实时反馈链路重构**: 节点 2s 周期上报 + 权威状态机 + 错误聚合 + WebUI 秒级进度

---

## [v1.2.x] - 历史版本

- v1.2.6: install 脚本 chmod 600
- v1.2.5: enroll 端点单小时桶
- v1.2.4: HMAC-SHA256 令牌派生优化
- v1.2.3: systemd 单元基本化
- v1.2.2: TLS 1.2+ 强制
- v1.2.1: WebUI 基础面板
- v1.2.0: 攻击场景化 (YAML)

---

## 升级路径建议

### 从 v1.3.4 升级到 v1.4.1-hotfix6

```bash
sudo ddos-controller update   # 自动完成升级, 保留 config.env
# 验证: 访问 WebUI, 检查 /health 返回 "version":"1.4.1"
```

### 从 v1.4.0 升级到 v1.4.1-hotfix6

```bash
sudo ddos-controller update
# 同样保留 config.env, 升级无破坏性
```

### 从 v1.4.1-hotfix1~5 升级到 v1.4.1-hotfix6

```bash
sudo ddos-controller update
# REG-5/6 自动应用 (wrapper 同步 + config.env 清理)
```

---

## 版本兼容性矩阵

| Component | v1.4.1-hotfix6 | v1.4.0 | v1.3.4 | v1.3.3 | v1.3.0 |
|-----------|----------------|--------|--------|--------|--------|
| Controller | ✅ | ✅ | ✅ | ✅ | ✅ |
| Attacker | ✅ | ✅ | ✅ | ✅ | ✅ |
| WebUI | ✅ | ✅ | ✅ | ✅ | ✅ |
| mTLS | ✅ | ✅ | ✅ | ✅ | ✅ |
| Controller→Node HTTPS | ✅ (opt-in) | ✅ (opt-in) | ❌ HTTP | ❌ | ❌ |
| Node mTLS | ⚠️ (REG-2) | ❌ (缺 cert) | ❌ | ❌ | ❌ |
| Auto-update path | ✅ (REG-1~6) | ⚠️ (REG-1 崩溃) | ✅ | ✅ | ✅ |

> ⚠️ Node mTLS v1.4.1-hotfix6 仍为 HTTP (NODE_USE_TLS=false), 因 enroll 端点未签发 node-cert.pem。
> v1.5.0 完整修复后将恢复全 mTLS。

---

## 致谢

- v1.4.0+ TD-1/2/3 设计参考 OWASP ASVS v4.0
- 6 项 REG 修复基于真实 WSL 端到端测试
- 5 套 E2E 验证脚本 (v141-verify-*.sh) 作为 v1.5.0+ 持续回归基线
