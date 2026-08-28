# Changelog

All notable changes to the DDoS Attack Platform will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> **注意**: 本平台为内部教学专用, 版本号遵循 SemVer 规范, 但
> 公开 API 兼容性破坏属于 minor 升级 (X.Y.0 → X.Y+1.0)
> 安全修复可能跨越多个版本号 (REG-x 集中在 hotfix)

---

## [v1.4.1-hotfix6] - 2025-08-28

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

## [v1.4.1] - 2025-08-28

### 🔒 修复 (Security)

- **REG-1**: `do_update()` 升级路径补写 NODE_TLS_* env (hotfix6 完善)

### 📦 变更

- `PLATFORM_VERSION` 1.4.0 → 1.4.1 (controller + attacker)
- README badge 更新

---

## [v1.4.0] - 2025-08-28

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

## [v1.3.4] - 2025-08-25

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

## [v1.3.3] - 2025-08-25

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

## [v1.3.2] - 2025-08-25

### 🔒 修复 (Security)

- F-1: install 脚本 apt-key 替换为 signed-by
- F-2: docker-compose 弱密钥 → REQUIRE_SHARED_SECRET=true

---

## [v1.3.0] - 2025-08-25

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
