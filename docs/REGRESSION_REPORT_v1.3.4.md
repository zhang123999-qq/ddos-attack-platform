# v1.3.4 修复后回归测试报告

> **项目**: DDoS Attack Platform  
> **版本**: v1.3.4  
> **日期**: 2025-08-25  
> **测试环境**: WSL2 Ubuntu 26.04, Python 3.14, systemd  
> **目标**: 验证 v1.3.4 安装器加固 (F2/F3/F4) + 文档补全 (F5/F6) 修复后, 6 项发现全部消除

---

## 📋 测试范围

| 类型 | 范围 | 数量 | 结果 |
|------|------|------|------|
| 静态脚本检查 | `controller-install.sh` / `node-install.sh` 内容验证 | 13 | ✅ 13/13 |
| E2E 安装验证 | 真实 WSL Ubuntu, 完整 `controller-install.sh` + `node-install.sh` 链路 | 13 | ✅ 13/13 |
| 隔离安全验证 | `nobody` 用户读 `config.env` 拒绝 | 1 | ✅ |
| **总计** | | **27** | **✅ 27/27** |

---

## 1️⃣ 静态脚本检查 (test_install_hardening.py)

```
PASS: controller-install.sh creates ddos user (F2)
PASS: controller-install.sh chowns install dir (F2+F3)
PASS: controller-install.sh chmod 600 config.env (F3)
PASS: controller-install.sh chmod 750 install dir (F3)
PASS: controller-install.sh chmod 640 service unit (F4)
PASS: controller systemd unit uses ddos user (F2)
PASS: controller-install.sh update re-applies perms (F2+F3)
PASS: node-install.sh creates ddos user (F2)
PASS: node-install.sh chowns install dir (F2+F3)
PASS: node-install.sh chmod 600 config.env (F3)
PASS: node-install.sh chmod 640 service unit (F4)
PASS: node-install.sh separates --cacert per source (v1.3.4 patch2)
PASS: node unit uses ddos for http, root for raw (F2)

ALL 13 INSTALLER HARDENING TESTS PASSED
```

---

## 2️⃣ E2E 真实环境安装验证 (test-install-perms.sh)

执行链路:
1. 完全清理旧安装 (含 `ddos` 用户, `/opt/...`, `/etc/...`, systemd units, wrappers)
2. 从 `master` HEAD 拉取最新 `controller-install.sh`
3. 运行安装, 提供 `port=8443` + `secret=regression-v134-secret-32chars-abcdef`
4. 检查 13 个观察点
5. enroll 一个 `attacker-http-v134` 节点
6. 运行 `node-install.sh` (从 `master` HEAD 拉取)
7. 检查攻击节点 5 个观察点

### 测试结果 (13/13 PASS)

| # | 检查 | 实际值 | 状态 |
|---|------|--------|------|
| 1 | ddos 用户已创建 | `uid=999(ddos) gid=986(ddos)` | ✅ |
| 2 | `/etc/ddos-controller/config.env` 权限 | `600 ddos:ddos` | ✅ |
| 3 | `/opt/.../controller` 权限 | `750 ddos:ddos` | ✅ |
| 4 | `ddos-controller.service` 权限 | `640` | ✅ |
| 5 | `User=ddos Group=ddos` 在 unit 内 | ✓ | ✅ |
| 6 | controller 进程 owner | `ddos` | ✅ |
| 7 | `/health` 正常 | `{"status":"healthy"}` | ✅ |
| 8 | `enroll-command` 返回安装命令 | ✓ | ✅ |
| 9 | `node-install.sh` 安装完成 | "Node installed and healthy" | ✅ |
| 10 | `/etc/ddos-attacker/config.env` 权限 | `600 ddos:ddos` | ✅ |
| 11 | `ddos-attacker.service` 权限 | `640` | ✅ |
| 12 | attacker 进程 owner | `ddos` (http 类型) | ✅ |
| 13 | 节点注册 + 心跳 | `online=1` | ✅ |
| 14 | 隔离: `nobody` 用户读 `config.env` | 被拒 (空内容) | ✅ |

---

## 3️⃣ 6 项发现的修复状态 (v1.3.3 → v1.3.4)

| ID | 严重度 | 描述 | v1.3.3 状态 | v1.3.4 状态 | 修复点 |
|----|--------|------|------------|------------|--------|
| F1 | – | BUG-5 partial DRIFT | 误报 (运行时读 disk 正确) | N/A (无需修复) | 实测: 改 disk → served 立即同步 |
| F2 | 🟡 High | controller-install.sh 不创建 ddos 用户 → root 运行 | 未修复 | **✅ 已修复** | controller-install.sh / node-install.sh 加 `useradd ddos` + chown; unit 加 `User=ddos Group=ddos` |
| F3 | 🟡 High | config.env 644 含 SHARED_SECRET | 未修复 | **✅ 已修复** | `chmod 600` + `chown ddos:ddos` (在 `cat >` 写后) |
| F4 | 🟢 Med | systemd unit 文件 644 | 未修复 | **✅ 已修复** | `chmod 640 /etc/systemd/system/ddos-*.service` |
| F5 | 🔵 Low | `/metrics` 文档未说明 (HTTP 不存在, 只有 WS) | 未修复 | **✅ 已修复** | API_REFERENCE.md 加 ⚠️ 端点归属说明 |
| F6 | 🔵 Low | heartbeat API 契约未文档化 | 未修复 | **✅ 已修复** | API_REFERENCE.md 加完整 schema 章节 |

---

## 4️⃣ 修复的额外 bug (live test 发现)

| ID | 严重度 | 描述 | 修复点 |
|----|--------|------|--------|
| **F2-2** | 🟡 High | `cat > config.env` 后 owner 是 root;需显式 `chown ddos:ddos` | 在 `chmod 600` 之后加 `chown $SERVICE_USER:$SERVICE_USER $ETC_DIR/config.env` |
| **F2-3** | 🟡 High | `node-install.sh` 下载循环对所有 URL 用 `--cacert $TMP_CA`, GitHub URL 因 `TMP_CA` 是 leaf cert 而 curl 报 "is badly used here" | 下载循环按 URL 类型分支: `ENDPOINT` URL 用 `--cacert`, GitHub URL 用系统 CA |

---

## 5️⃣ 安全性提升对比

| 指标 | v1.3.3 | v1.3.4 |
|------|--------|--------|
| controller 进程 owner | `root` | `ddos` (无登录权限) |
| attacker (http) 进程 owner | `root` | `ddos` |
| `/etc/ddos-controller/config.env` 权限 | `644` (任何用户可读) | `600 ddos:ddos` (仅 ddos 可读) |
| `SHARED_SECRET` 暴露面 | 全局可读 | 仅 ddos + root |
| systemd unit 文件权限 | `644` (任何用户可读 env vars) | `640` (root + ddos) |
| 安装目录权限 | `755 root:root` (任何用户可遍历) | `750 ddos:ddos` |
| `nobody` 用户读 `config.env` | 成功 → 泄漏密钥 | 拒绝 (Permission denied) |

---

## 6️⃣ 已知限制

1. **attacker (raw)** 仍以 `root` 运行 — 因为需要 `CAP_NET_RAW` 做 `syn_flood` 等攻击。`ddos` 用户需要额外 systemd capability 授权才能用 raw 类型 (本期未做, 待后续 `v1.4.0` 引入 capability bounding set)
2. **GHA release 未发**: 当前 master HEAD 是 v1.3.4 源码, 但 `releases/latest` 仍是 v1.3.3 binary。本测试使用 v1.3.3 binary + v1.3.4 install scripts, 已证明兼容
3. **测试中 controller 显示 version=1.3.3**: 因为 binary 是 v1.3.3, install scripts 是 v1.3.4。下次 GHA release 会构建真正的 v1.3.4 binary

---

## 7️⃣ 改动文件清单

| 文件 | 改动类型 | 内容 |
|------|----------|------|
| `deploy/controller-install.sh` | 修改 | 加 `useradd ddos` + chown + chmod 600/640/750 + `chown` config.env |
| `deploy/node-install.sh` | 修改 | 同上 + 修复 `--cacert` 分支逻辑 (F2-3) |
| `controller/app/main.py` | 修改 | `PLATFORM_VERSION = "1.3.4"` |
| `attacker/app/main.py` | 修改 | `PLATFORM_VERSION = "1.3.4"` |
| `docs/API_REFERENCE.md` | 修改 | `/metrics` 端点归属澄清 + heartbeat 完整 schema + 版本历史 |
| `README.md` | 修改 | badge + v1.3.4 changelog |
| `controller/tests/test_install_hardening.py` | 新增 | 13 个静态检查 |

---

## ✅ 结论

**v1.3.4 修复完成, 27/27 验证通过, 全部 6 项发现 (含 F1 误报说明 + F2-2/F2-3 额外 bug) 闭环**。可作为下一个 GHA release 的 source-of-truth 推送。
