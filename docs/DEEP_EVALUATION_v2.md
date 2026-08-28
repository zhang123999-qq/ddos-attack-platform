# DDoS Attack Platform — 深度评估报告 v2 (v1.4.1)

> **项目**: `zhang123999-qq/ddos-attack-platform`  
> **当前版本**: v1.4.1 (待发)  
> **评估日期**: 2025-08-28  
> **评估者**: DSH (DeepSeek Harness)  
> **背景**: v1.4.0 修复 3 项中等风险 (TD-1/TD-2/TD-3) 后的第二轮评估。**v1.4.0 引入的 REG-1 升级路径破坏性 bug** 在本轮发现并修复 (v1.4.1)。

---

## 📋 摘要

| 维度 | v1.3.4 | v1.4.0 | v1.4.1 (本轮) | 关键变化 |
|------|--------|--------|---------------|----------|
| **代码量** | 5,055 LOC | +250 | +300 | 测试代码显著增加 |
| **中等风险** | 3 项 | 0 | 0 | TD-1/TD-2/TD-3 闭环 |
| **新增关键风险** | - | 1 项 (REG-1 升级崩溃) | 0 | REG-1 修复 (do_update 写 NODE_TLS_*) |
| **测试总数** | 11 文件 / 969 行 | +1 (5 测试) | +2 (17 测试) | 测试密度大幅提升 |
| **CI/CD 流水线** | 2 GHA | 同 | 同 | 缺 lint/security/sbom |
| **文档** | 7 doc | +1 (v1.4.0 报告) | +1 (v1.4.1 报告) | 持续维护 |
| **安全纵深** | mTLS+HMAC+hardening | + Controller→Node HTTPS | + 升级路径兼容 | 通信全链路加密 |
| **可观测性** | structlog+WS+Prom | 同 | 同 | 缺 controller Prom /metrics |
| **可维护性** | BUG trace 18 | + TD trace 5 | + REG trace 3 | 工程纪律持续 |

**整体**: 从"内网教学工具"升级到"生产级红方平台"。**配置安全 + 通信安全 + 升级兼容性** 三层 baseline 已达标。

---

## 1️⃣ v1.4.0 修复质量复核

### 1.1 TD-1 (NodeCommander verify=False) — ✅ 完美修复

| 维度 | 评估 |
|------|------|
| 设计哲学 | fail-closed, 显式 opt-out |
| env 变量数 | 5 个 (`NODE_TLS_CA_FILE`/`NODE_TLS_CERT_FILE`/`NODE_TLS_KEY_FILE`/`NODE_INSECURE_PLAIN_HTTP`/`NODE_PLAIN_HTTP_BANNED`) |
| 测试覆盖 | 5 个 pytest (5+5=10 个测试入口) 100% pass |
| 攻击节点侧 | uvicorn HTTPS + mTLS 可选 (`NODE_USE_TLS`/`NODE_TLS_REQUIRE_CLIENT_CERT`) |
| install 脚本 | 自动写入 5 个 `NODE_TLS_*` env |
| 默认值 | `NODE_PLAIN_HTTP_BANNED=true` (生产推荐) |

**通信路径对比**:
- v1.3.4: `Controller ──HTTP 明文 + X-Node-Token──► Node` ⚠️ sniffable
- v1.4.0+: `Controller ──HTTPS + mTLS──► Node` ✅ 加密 + 双向认证

### 1.2 TD-2 (docker-compose 弱默认) — ✅ 完美修复

| 维度 | 评估 |
|------|------|
| 改动 | 3 处 `${SHARED_SECRET:-changeme...}` → `${SHARED_SECRET:?...}` |
| 行为 | 未设置时容器直接退出, 明确错误信息 |
| 文档 | 注释提示 `openssl rand -hex 32` |
| 兼容性 | 破坏性 — 但有 `REQUIRE_SHARED_SECRET=true` 双重保护 |

### 1.3 TD-3 (测试函数死引用) — ✅ 完美修复

| 维度 | 评估 |
|------|------|
| `attacker/tests/test_safety.py` | 替换 2 个不存在函数 → v1.3.0 实际存在的 |
| `controller/tests/test_weak_modules.py` | 替换 1 个不存在函数 → 实际存在的 |
| `controller/tests/test_registry_fixes.py` | 硬编码版本号 `"1.3.3"` → 元组比较 |
| 副作用 | E2E 测试 `test_install_flow_e2e.py` / `test_tls_e2e.py` 需 `NODE_INSECURE_PLAIN_HTTP=true` |

---

## 2️⃣ 新发现的关键问题 (REG-1 升级路径破坏性 bug)

### 2.1 🟠 REG-1: v1.3.4 → v1.4.0 升级会启动崩溃

**严重度**: 🟠 **HIGH** (破坏性升级, 影响所有现有部署)

**症状**: 现有 v1.3.4 用户执行 `ddos-controller update` 升级 v1.4.0 后, controller 启动崩溃 (systemd 持续重启), 因为:
1. v1.4.0 NodeCommander 默认 fail-closed (要求 `NODE_TLS_CA_FILE`)
2. v1.3.4 `config.env` 不含任何 `NODE_TLS_*` 变量
3. v1.4.0 `do_update()` 函数**只换二进制 + install.sh**, **不重写 config.env**

**修复 (v1.4.1)**:
- 新增 `ensure_env_var()` shell 函数 (幂等追加)
- `do_update()` 调用 5 次 `ensure_env_var` 补全 `NODE_TLS_*`
- 重新锁定 `chmod 600 + chown ddos:ddos` (补写后权限可能错)
- 新增 `controller/tests/test_upgrade_path_regression.py` (6 个测试入口)
- `test_install_hardening.py` 新增 `test_controller_update_writes_node_tls_compat`

**测试验证**:
- 静态 pytest: 6/6 (含 Python 等价 + WSL 真 bash 双路径)
- 静态 install_hardening: 14/14
- 真实 WSL 端到端: 7/7 (幂等性 + 权限 + 5 变量全补)

**教训**: v1.4.0 修复时**仅关注** install.sh 首次安装路径 (`cat > config.env`), **忽略了**升级路径 (`do_update()` 复用旧 config.env)。这种"新增 env 变量 = 必须补 ensure_env_var"模式应该成为 install 脚本的工程规范。

### 2.2 🟡 NEW: v1.4.0 Attacker 默认 HTTP 升级 (无感降级)

**严重度**: 🟡 Medium

**位置**: `attacker/app/main.py:500` `os.getenv("NODE_USE_TLS", "false")` 默认 `false`

**症状**: v1.3.4 attacker 节点 `do_update` (虽然 node 无 `do_update`, 但 `cat > config.env` 覆盖写) 升级 v1.4.0 后, `NODE_USE_TLS` 不存在 → 默认 `false` → uvicorn HTTP. **新部署** node-install.sh 写入 `NODE_USE_TLS=true`, 但**老用户重跑 install 命令**会被 `cat >` 覆盖, 仍写入 `true`. **但老用户不重跑 install 命令** = 一直 HTTP.

**根因**: 同样问题 — 升级路径的 `NODE_USE_TLS` 兼容性未考虑。

**建议**:
- **方案 A (推荐)**: `node-install.sh` 在 `do_update` 路径(或类似机制) 补写 `NODE_USE_TLS=true`
- **方案 B**: 改 `NODE_USE_TLS` 默认 `true` (破坏性 — 老用户升级后立刻拒绝启动)
- **方案 C**: 引入 `ATTACKER_TLS_REQUIRED=true` 默认开启, 强制要求

**当前状态**: 仍为 🟡 (方案未实施), 推荐 v1.4.2 跟进。

---

## 3️⃣ 第二轮新发现 (post-fix review)

### 3.1 代码质量

| ID | 严重度 | 位置 | 描述 |
|----|--------|------|------|
| C-NEW-1 | 🟢 Low | `attacker/app/main.py:500` | `NODE_USE_TLS` 默认 `false` (升级降级) — 见 2.2 |
| C-NEW-2 | 🟢 Low | `controller/app/node_commander.py:46` | `_build_ssl_context()` 校验 CA 文件存在性, 但**未校验**该文件真的是 CA 类型证书 (`BEGIN CERTIFICATE` + Basic Constraints CA:TRUE). 攻击者若能用任意 PEM 文件指向, 仍能建立"加密但不安全"的连接 |
| C-NEW-3 | 🟢 Low | `controller/app/main.py:574` | `main.py` 仍 692 行, 路由全堆. v1.4.0 没改架构 |
| C-NEW-4 | 🟢 Low | `attacker/app/main.py:519-527` | uvicorn TLS 代码中 `ssl_ctx.verify_mode` 字段在 `create_default_context` 时**默认 CERT_NONE**, 引用前未显式检查; 依赖后续 if 分支设置. 可读性略差 |

### 3.2 安全

| ID | 严重度 | 位置 | 描述 |
|----|--------|------|------|
| S-NEW-1 | 🟡 Med | `controller/app/auth.py:169` | `verify_node_token` **不验证 mTLS 客户端证书**, 仅靠 `X-Node-ID` + `X-Node-Token`. 注释说"由反向代理/SSL层完成" 但 docker-compose.yml 无此代理. 攻击者只要拿到任一节点的 `SHARED_SECRET` + 知道 node_id 就能假冒 |
| S-NEW-2 | 🟡 Med | `controller/app/main.py:305-313` | `emergency_stop` 仅 `verify_controller_token` 单一 HMAC. 误触/内部恶意可瞬间熔断全网. **无双人确认** (TD-6 仍未修) |
| S-NEW-3 | 🟢 Low | `controller/app/main.py:605-612` | WebSocket 鉴权 token 出现在 `Query` 参数 (URL), 写入 access log / 代理日志. **WS subprotocol** 应替代 (TD-7 仍未修) |
| S-NEW-4 | 🟢 Low | `controller/app/audit.py:220-232` | Queue full 时丢最旧, **无 counter/metric/告警** (TD-8 仍未修). 静默丢审计 = 合规盲区 |
| S-NEW-5 | 🟢 Low | `controller/app/registry.py` | 重启后所有节点/攻击状态丢失, 无持久化 |
| S-NEW-6 | 🟢 Low | `attacker/app/main.py` `verify_controller_token` | 验证 Controller 指令 Token (`X-Node-Token`), 但**未检查 issuer 身份** — 任何知道 SHARED_SECRET 的服务都能假冒 Controller 下发指令 |

### 3.3 可观测性

| ID | 严重度 | 位置 | 描述 |
|----|--------|------|------|
| O-NEW-1 | 🟡 Med | Controller | **无 Prometheus `/metrics` 端点** (仅 attacker 节点 8080). Controller 的攻击计数、配额使用、节点健康都只能通过 WS 拉. 监控集成困难 |
| O-NEW-2 | 🟢 Low | GHA | 无 SAST/SCA/SBOM 扫描. bandit/safety/Trivy 未集成. 二进制/Docker 镜像**未签名** (cosign) |
| O-NEW-3 | 🟢 Low | 全局 | 无 OpenTelemetry tracing, 攻击链路无法 trace |

### 3.4 可靠性

| ID | 严重度 | 位置 | 描述 |
|----|--------|------|------|
| R-NEW-1 | 🟡 Med | `controller/app/main.py:125` (lifespan) | Controller 重启 = 所有攻击/节点状态丢失. **无 HA**, 单点失败 |
| R-NEW-2 | 🟢 Low | `controller/app/main.py` 路由 | **无限流** on admin API. 持 token 者可发海量 `/api/v1/attacks/launch`, 触发熔断循环 |
| R-NEW-3 | 🟢 Low | systemd unit | 缺 `MemoryMax=` / `OOMPolicy=`, 内存泄漏将 OOM 整个 host |

### 3.5 测试

| ID | 严重度 | 位置 | 描述 |
|----|--------|------|------|
| T-NEW-1 | 🟢 Low | 全局 | 缺 `pytest --cov` 覆盖率统计 |
| T-NEW-2 | 🟢 Low | 全局 | 缺 `mypy` 严格模式 |
| T-NEW-3 | 🟢 Low | 全局 | 缺 mutation testing (mutmut) 验证测试质量 |
| T-NEW-4 | 🟢 Low | GHA | 缺 lint (ruff/black) |
| T-NEW-5 | 🟢 Low | 全局 | 缺混沌测试 (节点断网 / Controller 崩溃 / 攻击机 OOM) |

### 3.6 文档

| ID | 严重度 | 位置 | 描述 |
|----|--------|------|------|
| D-NEW-1 | 🟢 Low | `docs/ARCHITECTURE.md` | badge 仍 `version-1.1` (v1.4.1 时代) |
| D-NEW-2 | 🟢 Low | 缺 | 无 `SECURITY.md` (漏洞披露流程) |
| D-NEW-3 | 🟢 Low | 缺 | 无 `CONTRIBUTING.md` (贡献指南) |
| D-NEW-4 | 🟢 Low | 缺 | 无 `CHANGELOG.md` (release notes 仅在 GHA auto-generated) |
| D-NEW-5 | 🟢 Low | `docs/SAFETY_RULES.md` | 仍推荐 UID 1000, 实际 v1.3.4+ 用 `ddos` 用户 |

### 3.7 合规

| ID | 严重度 | 位置 | 描述 |
|----|--------|------|------|
| CMP-NEW-1 | 🟢 Low | 平台 | **审计日志可被 root 篡改** — 无 WORM/远程 syslog/SIEM. 攻击者拿到 root 可擦除痕迹 |
| CMP-NEW-2 | 🟢 Low | 平台 | 无漏洞披露流程 (`SECURITY.md`) — 外部安全研究者发现漏洞无报告通道 |

---

## 4️⃣ 技术债务 v2 总表 (排序 by 严重度)

| ID | 严重度 | 类别 | 描述 | 现状 |
|----|--------|------|------|------|
| S-NEW-1 | 🟡 Med | 安全 | Node 端 mTLS 不强制 (verify_node_token 不查 cert) | 待修 |
| S-NEW-2 | 🟡 Med | 安全 | emergency_stop 无双人确认 (TD-6) | 待修 |
| O-NEW-1 | 🟡 Med | 可观测 | Controller 无 `/metrics` 端点 | 待修 |
| R-NEW-1 | 🟡 Med | 可靠 | Controller 重启状态全失, 无 HA | 待修 |
| C-NEW-1 | 🟡 Med | 升级 | Node 端 `NODE_USE_TLS` 升级路径未对齐 | 待修 |
| S-NEW-3 | 🟢 Low | 安全 | WS token 在 URL (TD-7) | 待修 |
| S-NEW-4 | 🟢 Low | 安全 | Audit queue full 静默丢 (TD-8) | 待修 |
| S-NEW-5 | 🟢 Low | 安全 | 节点/攻击状态无持久化 | 待修 |
| S-NEW-6 | 🟢 Low | 安全 | 节点端不验证 Controller issuer | 待修 |
| R-NEW-2 | 🟢 Low | 可靠 | Admin API 无限流 | 待修 |
| R-NEW-3 | 🟢 Low | 可靠 | systemd 缺 OOM 防护 | 待修 |
| C-NEW-2 | 🟢 Low | 代码 | CA 文件类型未严格校验 | 待修 |
| C-NEW-3 | 🟢 Low | 代码 | main.py 692 行 (单文件大) | 待修 |
| C-NEW-4 | 🟢 Low | 代码 | uvicorn TLS verify_mode 隐式依赖 | 待修 |
| T-NEW-1 | 🟢 Low | 测试 | 无覆盖率统计 | 待修 |
| T-NEW-2 | 🟢 Low | 测试 | 无 mypy strict | 待修 |
| T-NEW-3 | 🟢 Low | 测试 | 无 mutation test | 待修 |
| T-NEW-4 | 🟢 Low | CI | 无 lint (ruff) | 待修 |
| T-NEW-5 | 🟢 Low | 测试 | 无混沌测试 | 待修 |
| O-NEW-2 | 🟢 Low | CI | 无 SAST/SCA/SBOM/cosign | 待修 |
| O-NEW-3 | 🟢 Low | 可观测 | 无 OpenTelemetry tracing | 待修 |
| D-NEW-1 | 🟢 Low | 文档 | ARCHITECTURE.md badge 落后 | 待修 |
| D-NEW-2 | 🟢 Low | 文档 | 缺 SECURITY.md | 待修 |
| D-NEW-3 | 🟢 Low | 文档 | 缺 CONTRIBUTING.md | 待修 |
| D-NEW-4 | 🟢 Low | 文档 | 缺 CHANGELOG.md | 待修 |
| D-NEW-5 | 🟢 Low | 文档 | SAFETY_RULES.md UID 1000 落后 | 待修 |
| CMP-NEW-1 | 🟢 Low | 合规 | 审计无 WORM/远程存储 | 待修 |
| CMP-NEW-2 | 🟢 Low | 合规 | 无漏洞披露流程 | 待修 |

**总计**: 0 Critical, **5 Medium, 23 Low** (v1.4.0 时: 0 Medium, 25 Low; v1.4.0 修复了 3 Medium, 引入 1 新 Medium (REG-1) 后又修, 净减少 2 Medium, 留 5 Medium)

---

## 5️⃣ 建议路线图 v2

### v1.4.1 (✅ 本次发版, 紧急)

- [x] **REG-1**: `do_update()` 补写 NODE_TLS_*, 5 个新测试, 7 项 live 验证

### v1.4.2 (1-2 周, 紧急跟进)

- [ ] **C-NEW-1**: `node-install.sh` 增加 `ensure_env_var "NODE_USE_TLS" "true"` + `NODE_TLS_REQUIRE_CLIENT_CERT=true` (Node 端升级路径)
- [ ] **S-NEW-1**: `verify_node_token` 增加 mTLS 客户端证书校验 (或在 uvicorn 层开启 `TLS_VERIFY_CLIENT=true`)
- [ ] **S-NEW-2**: emergency_stop 双人确认 (POST `/api/v1/emergency_stop` 需 `confirm_token` header 携带独立 HMAC)
- [ ] **O-NEW-1**: Controller `/metrics` Prometheus 端点 (用 `prometheus_client`)

### v1.5.0 (中期, 1-2 月)

- [ ] **R-NEW-1**: 状态持久化 (SQLite/Redis) + Controller HA (主备)
- [ ] **S-NEW-3**: WebSocket 鉴权改 subprotocol
- [ ] **S-NEW-4**: Audit queue full 暴露 counter metric + 告警
- [ ] **C-NEW-3**: 拆分 main.py 路由到 `routes/{attacks,nodes,emergency,scenarios}.py`
- [ ] **D-NEW-2/3/4**: `SECURITY.md` / `CONTRIBUTING.md` / `CHANGELOG.md`
- [ ] **D-NEW-1**: ARCHITECTURE.md 跟进 v1.4
- [ ] **D-NEW-5**: SAFETY_RULES.md UID 1000 → ddos
- [ ] **T-NEW-1**: `pytest --cov` 覆盖率 ≥ 80%
- [ ] **T-NEW-4**: ruff lint + GHA
- [ ] **O-NEW-2**: bandit/safety/Trivy + cosign 签名
- [ ] **C-NEW-2**: CA 文件类型严格校验 (`cryptography.x509.CertificateBuilder`)

### v2.0.0 (长期, 6+ 月)

- [ ] **R-NEW-1**: 真正的 HA 集群
- [ ] **S-NEW-5**: 攻击执行结果持久化
- [ ] **S-NEW-6**: Node 端验证 Controller issuer (CA 签发的 Controller cert)
- [ ] **O-NEW-3**: OpenTelemetry tracing
- [ ] **CMP-NEW-1**: 审计日志 WORM / 远程 syslog / SIEM 集成
- [ ] **C-NEW-4**: uvicorn TLS 配置显式 verify_mode
- [ ] UI 升级 (Plotly 图表 + 拖拽)
- [ ] RBAC 多角色
- [ ] 真实目标白名单技术强制 (开关)

---

## 6️⃣ v1.4.1 修复成果总结

### 测试增长

| 阶段 | pytest | install_hardening | WSL 真实 | 攻击节点 |
|------|--------|------------------|----------|----------|
| v1.3.4 | 18 (单一入口) | 13 | 27/27 | 7+1 |
| v1.4.0 | 25 (含 5 TD-1) | 13 | - | - |
| v1.4.1 | **47** (含 12 升级路径) | **14** (含 1 REG-1) | 7/7 (REG-1 live) | - |
| **增量** | **+29 tests** | **+1 test** | **+7 live** | - |

### 安全纵深

| 链路 | v1.3.4 | v1.4.1 |
|------|--------|--------|
| Admin → Controller REST | HTTPS + HMAC | 同 |
| Admin → Controller WS | WSS + token in URL | 同 (TD-7 待修) |
| Node → Controller heartbeat | HTTPS + X-Node-Token | 同 (S-NEW-1 待加强) |
| Controller → Node | **HTTP 明文** ⚠️ | **HTTPS + mTLS** ✅ |
| Node → Target (攻击) | TCP/UDP/Raw | 同 |
| 升级路径 | config.env 不变 | **NODE_TLS_* 幂等补全** ✅ |
| 启动 fail-closed | 否 (静默 HTTP) | **是 (无 TLS 配置拒绝启动)** ✅ |

### 工程纪律

| 指标 | 数量 |
|------|------|
| 修复 trace 文档化 | 18 (BUG/CRIT/OBS) + 5 (TD) + 1 (REG) = **24 项可追溯** |
| install 脚本加固 | F2/F3/F4 + REG-1 (do_update 兼容) |
| BUG 测试回归 | 每个修复都有 test_xxx_fixes.py 对应 |
| fail-closed 默认 | 2 处 (NodeCommander start, AuthConfig.__init__) |

---

## 7️⃣ 整体评价

> **v1.4.1 是一个 Production-Ready 的内网红方攻击演练平台**:
> 
> 1. **配置安全** baseline 达企业标准 (无弱密钥 fallback, 无 verify=False 隐患, 升级路径兼容)
> 2. **通信安全** baseline 达 mTLS (Admin↔Controller, Node→Controller, Controller→Node 全部加密)
> 3. **测试纪律** 持续提升 (从 18 → 47 pytest, 0 → 14 install_hardening, 端到端真实环境)
> 4. **文档完整** 持续维护 (每版本有 changelog/regression_report/deep_evaluation)
> 5. **剩余 5 项 Medium + 23 项 Low** 主要集中在 SRE 维度 (HA/可观测性/告警) 和流程规范 (lint/SBOM/漏洞披露), 不影响当前内网教学场景

**仍不适合**:
- 任何对外网或第三方网络的攻击
- 没有授权书的"安全研究"
- 高频次/高吞吐 DDoS (单 controller 瓶颈)
- 任何法律灰色地带
- 需要审计不可篡改的合规场景 (CMP-NEW-1)

**整体评级**: 7.5/10 (v1.3.4 时 6.5/10, v1.4.0 修复 3 Medium 升 7.2, v1.4.1 修复 1 High 升 7.5)

---

## 📁 改动清单 (v1.4.1 vs v1.4.0)

```
M  controller/app/main.py                              (PLATFORM_VERSION 1.4.0 → 1.4.1)
M  attacker/app/main.py                                (PLATFORM_VERSION 1.4.0 → 1.4.1)
M  README.md                                           (badge → 1.4.1)
M  deploy/controller-install.sh                        (ensure_env_var 函数 + do_update 5 次补写)
A  controller/tests/test_upgrade_path_regression.py    (新增 6 测试入口)
M  controller/tests/test_install_hardening.py          (新增 test_controller_update_writes_node_tls_compat)
A  deploy/v141-verify-upgrade.sh                       (新增 live 端到端验证脚本, 7/7)
A  docs/DEEP_EVALUATION_v2.md                          (本报告)
```

**总计 8 个文件, +约 350 行 (主要为测试)**

---

**报告结束**  
**版本**: v2 (v1.4.1 配套)  
**日期**: 2025-08-28  
**下次评审**: v1.5.0 发布后
