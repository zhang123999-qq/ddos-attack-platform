# 代码审查体系 (Code Review System) — 总览

> **文档版本**: v1.0
> **生效日期**: 2026-08-31
> **owner**: CodeReviewExpert + Maintainers

本目录是项目代码审查体系的**单一入口**。

---

## 📚 文档地图

| 文档 | 面向谁 | 何时读 |
|------|--------|--------|
| [`CODE_REVIEW_STANDARDS.md`](CODE_REVIEW_STANDARDS.md) | Reviewer + Author | 写代码前 / review 前必读。定义"什么必须修、什么算 blocker" |
| [`REVIEW_PROCESS.md`](REVIEW_PROCESS.md) | Author + Reviewer + Maintainer | 开 PR 前 / 收到 review 时。定义"流程怎么走、SLA 多长、争议怎么解" |
| [`REVIEW_CHECKLIST.md`](REVIEW_CHECKLIST.md) | Reviewer | review 现场 5 分钟速查 |
| 本文件 | 所有人 | 总览入口 |

---

## 🚀 30 秒上手

### 1. 我要写代码 / 提 PR

1. 看 [`REVIEW_PROCESS.md` §2](../../REVIEW_PROCESS.md) — Author 阶段
2. 本地跑：
   ```bash
   pre-commit run --all-files
   cd controller && pytest tests/ --cov=app
   cd ../attacker && pytest tests/ --cov=app
   ```
3. 开 PR，按 `.github/PULL_REQUEST_TEMPLATE.md` 填

### 2. 我被指派 reviewer

1. 看 [`REVIEW_CHECKLIST.md`](REVIEW_CHECKLIST.md) — review 现场清单
2. 看 [`CODE_REVIEW_STANDARDS.md`](CODE_REVIEW_STANDARDS.md) — 判断 blocker
3. 写评论：用 [review comment 模板](../../REVIEW_CHECKLIST.md#review-comment-模板)
4. 24h 首响，48h 结论

### 3. 我是高敏模块 owner（auth/crypto/registry/orchestrator/...）

1. 收到 PR 时机器人会 `@` 你
2. PR 必须有 2 reviewer（你自己 + 另一位 owner/maintainer）
3. 按 [§6 关键模块清单](../../CODE_REVIEW_STANDARDS.md#6-高敏模块必审清单-critical-modules-policy) 重点检查

---

## 🔧 自动化门禁 (Bot/CI)

提交 PR 后**机器先审一遍**，所有 gate 必须绿才会被路由到 reviewer：

| Gate | 工具 | 状态 |
|------|------|------|
| 格式 | `ruff format --check` | 🔴 阻止合并 |
| Lint | `ruff check` | 🔴 阻止合并 |
| 类型 | `mypy` | 🟡 warning（v1.5.0 后 🔴） |
| 安全 | `bandit` + `gitleaks` | 🔴 阻止合并 |
| 依赖漏洞 | `pip-audit` | 🟡 warn（high vuln 🔴） |
| 单元测试 | `pytest --cov-fail-under=70` | 🔴 阻止合并 |
| Commit msg | Conventional Commits | 🔴 阻止合并 |
| PR 模板 | 模板字段全填 | 🟡 warn |
| Markdown | `markdownlint` | 🟡 warn |

配置位置：
- `.pre-commit-config.yaml` — 本地 hook
- `.github/workflows/pr-gate.yml` — PR 阶段 CI
- `.github/PULL_REQUEST_TEMPLATE.md` — PR 模板
- `.github/labeler.yml` — 自动标签

---

## 🎯 高敏模块一览

改动下列文件，**强制 2 reviewer**（含 1 名安全/架构 owner）：

```
controller/app/auth.py              # Token 派生 / 验证
controller/app/audit.py             # 审计
controller/app/registry.py          # 节点状态机
controller/app/orchestrator.py      # 全局编排 + 限流
controller/app/node_commander.py    # 指令下发
attacker/app/crypto.py              # mTLS / HMAC / 指纹
attacker/app/attacks/base.py        # 攻击基类 + 白名单
.github/workflows/**                # CI 权限与发布链路
deploy/install*.sh                  # 安装权限 / 路径
docs/SAFETY_RULES.md                # 安全守则
docs/SECURITY.md                    # 安全披露
```

---

## 📊 度量指标 (Owner 月度 review)

| 指标 | 目标 |
|------|------|
| PR 首次响应中位时间 | < 8h |
| PR 合并中位时间 | < 24h |
| Reviewer 评论到 Author 二次提交 | < 24h |
| 高敏模块 PR 双 reviewer 达成率 | 100% |
| 🔴 Blocker 被 defer 比例 | < 5% |

数据来源：GHA/GitLab API + 人工抽样。

---

## 🛠️ 工具链一览

| 类别 | 工具 | 引入版本 | 状态 |
|------|------|----------|------|
| 格式 | black + ruff format | v1.4 | ✅ |
| Lint | ruff | v1.4 | ✅ |
| 类型 | mypy | v1.5.0 | 🟡 warning |
| 安全 | bandit | v1.4 | ✅ |
| 密钥 | gitleaks | v1.4 | ✅ |
| 依赖 | pip-audit | v1.4 | 🟡 warning |
| 镜像 | trivy | v1.5.0 | 🔴 规划 |
| 签名 | cosign | v1.5.0 | 🔴 规划 |
| Hook | pre-commit | v1.4 | ✅ 推荐 |

---

## 📜 版本与变更

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-08-31 | 初版：建立审查标准 + 流程 + 速查清单 + 自动门禁 |

---

## 📮 反馈与改进

- 流程问题：开 issue 标 `area/process`
- 标准争议：开 issue 标 `area/standards` + 引用具体章节
- Maintainers 邮箱：maintainers@<your-company>.internal
- 季度 review：本目录文档本身也要 review

---

**生效前提**：本目录文档需 2 maintainer 批准后正式启用（参考 [`REVIEW_PROCESS.md` §10](../../REVIEW_PROCESS.md)）。