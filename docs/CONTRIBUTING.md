# Contributing Guide

> **本平台为内部红方攻击演练工具, 贡献者必须**:
> 1. 已完整阅读并签署 [SAFETY_RULES.md](SAFETY_RULES.md)
> 2. 拥有内部 commit 权限
> 3. 接受以下 commit / PR / release 流程

---

## 1. 开发环境

### 1.1 系统要求

- **OS**: Linux (推荐 Ubuntu 22.04+) / WSL2 / macOS
- **Python**: 3.11+ (推荐 3.13)
- **Git**: 2.30+
- **Docker**: 24.0+ (可选, 用于 E2E 测试)
- **工具**: `openssl`, `curl`, `systemd` (WSL 需 systemd 支持)

### 1.2 克隆与初始化

```bash
git clone <internal-gitlab-url>/security/ddos-attack-platform.git
cd ddos-attack-platform

# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate
pip install -r controller/requirements.txt
pip install -r attacker/requirements.txt
pip install -r build/requirements-build.txt
pip install -r requirements-dev.txt   # pytest, ruff, mypy
```

### 1.3 跑测试

```bash
# 单元测试
cd controller && python -m pytest tests/ -v
cd ../attacker && python -m pytest tests/ -v

# E2E (需要 WSL/Docker)
bash deploy/v141-verify-controller.sh
bash deploy/v141-verify-node.sh
bash deploy/v141-verify-attack.sh
```

---

## 2. 提交规范 (Commit Convention)

### 2.1 Conventional Commits 格式

```
<type>(<scope>): <subject>

<body>

<footer>
```

### 2.2 Type

| Type | 用途 | 示例 |
|------|------|------|
| `feat` | 新功能 | `feat(controller): add /metrics Prometheus endpoint` |
| `fix` | bug 修复 | `fix(registry): heartbeat race condition (BUG-4)` |
| `docs` | 仅文档 | `docs: update ARCHITECTURE.md to v1.4.1` |
| `style` | 格式 (无逻辑变更) | `style(attacker): black formatting` |
| `refactor` | 重构 (无功能变更) | `refactor(audit): extract ring buffer` |
| `perf` | 性能优化 | `perf(registry): O(1) node lookup` |
| `test` | 测试 | `test: add REG-1 upgrade path regression tests` |
| `chore` | 杂项 | `chore: bump requirements.txt` |
| `revert` | 回滚 | `revert: feat(metrics)` |

### 2.3 Scope

可选, 表示影响范围:
- `controller` / `attacker` / `deploy` / `docs` / `tests` / `gha`
- 模块: `auth` / `audit` / `registry` / `ratelimit` / `orchestrator` / `node_commander` / `main`

### 2.4 Subject

- 50 字符以内
- 动词原形开头 (add, fix, update, remove, refactor)
- 首字母小写
- 末尾无句号

### 2.5 Body

- 72 字符换行
- 说明 **what** 和 **why**, 不是 **how**
- 引用 issue / TD / REG 编号

### 2.6 Footer

- Breaking Changes: `BREAKING CHANGE: <description>`
- Issue 引用: `Closes #123`, `Refs TD-7`, `Refs REG-1`

### 2.7 示例

```
fix(install): v1.4.1-hotfix6 — REG-6 (清理 config.env 残留)

完整卸载重装测试发现升级路径仍残留旧 wrapper 硬编码的
NODE_TLS_CA_FILE=/opt/.../ca-cert.pem 绝对路径, 与新设计
(空字符串) 冲突, NodeCommander 会切回 https 模式, 攻击指令
"Failed to deliver command to any node"。

- do_update() 末尾新增 sed 替换: 残留的 NODE_TLS_CA_FILE=/... → NODE_TLS_CA_FILE=
- 同样处理 NODE_TLS_CERT_FILE / NODE_TLS_KEY_FILE

Refs: REG-6, DEEP_EVALUATION_v3.md §2.1
```

---

## 3. 分支策略 (Branching)

### 3.1 主分支

- `master`: 稳定, 每次 tag 都从 master 出
- `develop` (规划中): 集成开发, 暂未启用

### 3.2 功能分支

- `feat/<scope>-<short-desc>` — 新功能
- `fix/<scope>-<issue>` — bug 修复
- `refactor/<scope>` — 重构
- `docs/<topic>` — 文档

### 3.3 提交流程

```bash
git checkout -b feat/node-mtls-enrollment
# ... 开发 + 写测试 ...
git add -A
git commit -m "feat(node-install): issue node cert during enrollment"
git push origin feat/node-mtls-enrollment
# 创建 MR (合并请求) 到 master
```

---

## 4. 合并请求 (MR) 流程

### 4.1 创建 MR 前检查

- [ ] 代码已格式化 (`ruff format`, `black`)
- [ ] Lint 通过 (`ruff check`)
- [ ] 类型检查通过 (`mypy controller/ attacker/`) *(暂未启用)*
- [ ] 单元测试通过 (`pytest tests/ -v`)
- [ ] E2E 验证通过 (WSL 端到端)
- [ ] CHANGELOG.md 已更新 (如适用)
- [ ] DEEP_EVALUATION_v*.md 已更新 (如关闭/新增 TD/REG)
- [ ] Commit message 符合 Conventional Commits
- [ ] 文档已更新 (README, ARCHITECTURE, API_REFERENCE)

### 4.2 MR 模板

```markdown
## 变更类型
- [ ] feat: 新功能
- [ ] fix: bug 修复
- [ ] docs: 文档
- [ ] refactor: 重构
- [ ] test: 测试
- [ ] chore: 杂项

## 描述
(简述变更内容)

## 关联
- Refs: TD-7 / REG-1 / Issue #123
- Closes: #123

## 测试
- 单元测试: 72/72 PASS (含新增 N 测试)
- E2E: 5/5 套件 PASS

## 影响范围
- [ ] breaking change: (描述)
- [ ] 需要新 config 项: (env 变量名)
- [ ] 需要 migration: (步骤)
```

### 4.3 评审要求

- 至少 1 名 reviewer 批准
- 复杂变更需 2 名 (1 安全 + 1 架构)
- 24 小时内首次响应
- GHA 必须绿 (test gate)

---

## 5. 发布流程 (Release)

### 5.1 版本号规则

- **MAJOR** (X.0.0): 破坏性架构变更
- **MINOR** (0.X.0): 新功能, 向后兼容
- **PATCH** (0.0.X): bug 修复, 向后兼容
- **hotfix** (0.0.X-hotfixY): 紧急安全修复, 跨多个 patch 号

### 5.2 Tag 流程

```bash
# 1. 确认 master 最新
git checkout master && git pull

# 2. 更新版本号
# - controller/app/main.py: PLATFORM_VERSION
# - attacker/app/main.py: PLATFORM_VERSION
# - README.md badge
# - docs/CHANGELOG.md

# 3. 提交
git add -A
git commit -m "chore: bump to v1.4.2"
git tag v1.4.2
git tag v1.4.2-hotfix  # 如是 hotfix

# 4. 推送 (触发 GHA binary-release.yml)
git push origin master --tags

# 5. 等待 GHA 通过
# 6. 在 GitHub Releases 页面编辑 release notes (可基于 GHA auto-generated)
```

### 5.3 GHA 自动产物

`binary-release.yml` (push `v*` tag 触发):
1. 跑单元测试 (test gate)
2. 编译 controller + attacker (matrix: linux-x86_64)
3. 打包 tar.gz
4. 上传 artifact
5. 创建 GitHub Release (auto-generated notes)

### 5.4 部署验证

```bash
# 1. 在 staging WSL 测试
wsl -e bash -c "ddos-controller update"
# 验证 /health 返回新版本

# 2. 跑 E2E
bash deploy/v141-verify-controller.sh
bash deploy/v141-verify-node.sh
bash deploy/v141-verify-attack.sh

# 3. 通知使用方
# 邮件 / 群消息
```

---

## 6. 代码规范

### 6.1 Python (PEP 8 + 项目补充)

- **格式化**: `black` + `ruff format`
- **Lint**: `ruff check` (替代 flake8/pylint)
- **类型**: `mypy --strict` (v1.5.0 启用, 现阶段宽松模式)
- **导入**: 绝对导入 (不要 `from ..module import`)
- **行宽**: 100 字符
- **字符串**: 双引号 (除非含双引号)
- **命名**:
  - 类: `PascalCase`
  - 函数/变量: `snake_case`
  - 常量: `UPPER_SNAKE_CASE`
  - 私有: `_leading_underscore`

### 6.2 Shell (controller-install.sh / node-install.sh)

- 始终 `set -e` (除非显式需要容忍)
- 函数定义 `function_name() { ... }` (无 function 关键字)
- 局部变量用 `local`
- 引用变量 `"$var"` (双引号)
- 错误处理: `command || handle_error`
- 文档注释: 文件顶部含 `# 用法:` + `set -euo pipefail` 兼容
- 幂等性: 关键操作 (ensure_env_var) 必须是幂等的

### 6.3 注释规范

- 行内注释: `# 解释 why, 不是 what`
- 函数 docstring (Python):
  ```python
  def my_function(arg1: str, arg2: int) -> bool:
      """一句话功能描述

      详细说明 (可选)

      Args:
          arg1: 参数1 描述
          arg2: 参数2 描述

      Returns:
          返回值描述

      Raises:
          ValueError: 何时抛
      """
  ```
- Trace 注释: 修复时引用 `BUG-x` / `TD-x` / `REG-x` / `CRIT-x` 编号

### 6.4 测试规范

- 单元测试: pytest, 1 test = 1 行为
- E2E: bash, 真实环境, 命名为 `v<X.Y>-verify-<scope>.sh`
- 命名:
  - 单元: `test_<module>.py` 或 `test_<module>_<aspect>.py`
  - 函数: `test_<behavior_description>`
  - E2E: `verify-<scope>.sh`, `PASS:` / `FAIL:` 标记
- Fixture: 优先 pytest fixture, 避免全局 setUp

---

## 7. 文档规范

### 7.1 必须更新的文档 (按变更类型)

| 变更 | README | CHANGELOG | API_REFERENCE | ARCHITECTURE | DEEP_EVALUATION | SAFETY_RULES |
|------|--------|-----------|---------------|--------------|-----------------|--------------|
| bug 修复 | ✓ | ✓ | (如 API 变) | | ✓ (如关 TD) | |
| 新功能 | ✓ | ✓ | ✓ | ✓ | | |
| 安全策略 | ✓ | ✓ | | ✓ | ✓ | ✓ |
| 破坏性变更 | ✓ | ✓ (Breaking) | ✓ | ✓ | | |
| 文档 typo | | | | | | |

### 7.2 文档版本

每个文档头部含:
```markdown
> **文档版本**: v1.4.1
> **适用平台版本**: v1.4.1-hotfix6+
> **最近更新**: YYYY-MM-DD
```

---

## 8. 评审清单 (Reviewer Checklist)

### 8.1 代码评审

- [ ] 符合 commit 规范
- [ ] 无 console.log / print 调试代码
- [ ] 无敏感信息 (密钥, 真实 IP, 内部 URL)
- [ ] 错误处理: 不吞异常, 不静默失败
- [ ] 并发安全: lock/event/queue 使用正确
- [ ] 资源清理: `try/finally` 或 `async with` 关闭资源
- [ ] 性能: 无 O(n²) 循环 / 无同步 IO 阻塞
- [ ] 安全: 无 SQL 注入 / XSS / SSRF / 命令注入 / 路径穿越
- [ ] 可观测: 关键路径有 structlog 日志

### 8.2 测试评审

- [ ] 新功能有对应测试
- [ ] 边界条件 (空/最大/异常) 有测试
- [ ] 测试运行时间 < 5s (单元) / < 5min (E2E)
- [ ] 测试独立: 不依赖其他测试的执行顺序 (除显式 fixture)
- [ ] 测试可重复: 重复运行结果一致

### 8.3 文档评审

- [ ] 文档与代码一致 (无虚构 API)
- [ ] 示例代码可运行
- [ ] 中文/英文术语一致
- [ ] 链接有效

---

## 9. 工具链

### 9.1 必备

- `git` 2.30+
- `python` 3.11+
- `pytest` 7.0+
- `black` 23.0+
- `ruff` 0.1+
- `mypy` 1.0+ (规划中)
- `bandit` 1.7+ (规划中)
- `pip-audit` 2.0+ (规划中)
- `trivy` 0.40+ (规划中)
- `cosign` 2.0+ (规划中)
- `pre-commit` 3.0+ (推荐)

### 9.2 推荐 IDE 配置

#### VSCode

```json
// .vscode/settings.json
{
  "python.linting.ruffEnabled": true,
  "python.formatting.provider": "black",
  "python.testing.pytestEnabled": true,
  "[python]": {
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
      "source.fixAll.ruff": true
    }
  }
}
```

#### PyCharm

- Settings → Tools → Black → "On save"
- Settings → Tools → Ruff → "Run ruff when saving"
- Settings → Tools → pytest → Enable

---

## 10. 应急联系

| 类型 | 渠道 |
|------|------|
| 代码问题 | 创建 GitLab Issue (内部) |
| 安全漏洞 | 见 [SECURITY.md](SECURITY.md) |
| 紧急 PR | 找 oncall reviewer |
| 流程问题 | 邮件 maintainers@<your-company>.internal |

---

**版本**: 1.0
**生效日期**: 2025-08-28
**下次评审**: v1.5.0 发布前
