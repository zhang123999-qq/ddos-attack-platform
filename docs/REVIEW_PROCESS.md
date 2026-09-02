# 代码审查流程 (Code Review Process)

> **文档版本**: v1.0
> **适用平台版本**: v1.4.1+
> **最近更新**: 2026-08-31
> **owner**: CodeReviewExpert + Maintainers

本文定义 **从 PR 提交到合并的完整工作流**，含 SLA、自动门禁、Reviewer 角色、争议解决。

---

## 1. 流程总览

```
┌─────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌────────┐
│  本地   │ →  │ Push &   │ →  │ 自动门禁 │ →  │  人工    │ →  │  合并  │
│ 开发 +  │    │ 开 PR    │    │ (Bot)    │    │ Review   │    │        │
│ 自测    │    │          │    │          │    │          │    │        │
└─────────┘    └──────────┘    └──────────┘    └──────────┘    └────────┘
   ① Author       ② Author      ③ Bot/CI       ④ Reviewer     ⑤ Author
                                                                  /Bot
```

每个阶段的详细要求如下：

---

## 2. 阶段 ① — Author 本地准备 (提交前)

### 2.1 Author 自查清单

开发完成后，**先自己跑一遍**：

```bash
# 1. 格式 + lint
ruff format .
ruff check .

# 2. 类型 (v1.5.0 强制, 现阶段宽松)
mypy controller/app/ attacker/app/ --ignore-missing-imports

# 3. 安全扫描
bandit -r controller/app attacker/app -lll
gitleaks detect --source . --no-banner

# 4. 单元测试
cd controller && pytest tests/ -v --cov=app --cov-report=term-missing
cd ../attacker && pytest tests/ -v --cov=app --cov-report=term-missing

# 5. E2E (修改了关键路径)
bash deploy/v141-verify-controller.sh
bash deploy/v141-verify-node.sh
bash deploy/v141-verify-attack.sh
```

### 2.2 Pre-commit Hook (强烈推荐)

项目根放 `.pre-commit-config.yaml`，提交时自动跑：

- `ruff format --check`
- `ruff check`
- `bandit -r controller/app attacker/app`
- `gitleaks protect --staged`
- `conventional-pre-commit` 检查 commit message
- 大文件/调试代码检测（`print` / `breakpoint()` / `TODO`）

### 2.3 Author 开 PR

使用模板（`.gitlab/merge_request_templates/default.md` 或 `.github/PULL_REQUEST_TEMPLATE.md`）：

```markdown
## 变更类型
- [ ] feat: 新功能
- [ ] fix: bug 修复
- [ ] docs: 文档
- [ ] refactor: 重构
- [ ] test: 测试
- [ ] chore: 杂项
- [ ] perf: 性能

## 变更摘要
（一句话）

## 变更原因
（背景/issue 链接）

## 影响范围
- [ ] API 路径/参数变更
- [ ] config.env 变更
- [ ] 数据模型/数据库 schema 变更
- [ ] 部署脚本/镜像变更
- [ ] 文档需要同步更新

## 测试
- [ ] 单元测试: X/X PASS (新增 N 个)
- [ ] E2E: 套件 X/X PASS
- [ ] bandit: 0 high/medium
- [ ] gitleaks: clean

## 关联
Refs: TD-7 / REG-1 / Issue #123
Closes: #123

## Checklist
- [ ] 我已本地跑通 ruff + pytest
- [ ] 我已阅读本项目的 CODE_REVIEW_STANDARDS.md
- [ ] 高敏模块改动已通知安全 owner
```

---

## 3. 阶段 ② — 自动门禁 (Bot/CI)

PR 一开/每次 push，**机器先审一遍**。所有 gate 必须绿。

### 3.1 GitHub Actions / GitLab CI 配置

文件：`.github/workflows/pr-gate.yml`（GitLab 用 `.gitlab-ci.yml`）

| Gate | 工具 | 失败动作 |
|------|------|----------|
| 格式 | `ruff format --check` | 🔴 阻止合并 |
| Lint | `ruff check .` | 🔴 阻止合并 |
| 类型 | `mypy --ignore-missing-imports` | 🟡 warn（v1.5.0 后 🔴） |
| 安全 | `bandit -r controller/app attacker/app -lll` | 🔴 阻止合并（high/medium） |
| 密钥 | `gitleaks detect --source .` | 🔴 阻止合并 |
| 依赖漏洞 | `pip-audit -r controller/requirements.txt -r attacker/requirements.txt` | 🟡 warn（high vuln 🔴） |
| 单元测试 | `pytest tests/ --cov-fail-under=70` | 🔴 阻止合并 |
| 构建镜像 | `docker build controller/ attacker/` | 🟡 warn |
| Commit msg | `conventional-pre-commit` | 🔴 阻止合并 |
| PR 模板 | 模板字段全填 | 🟡 warn |

### 3.2 Bot 自动评论

- ✅ **绿**：评论 `"✅ All checks passed. Ready for human review."`，并 `@` 相关 owner
- 🔴 **红**：评论 `"❌ <tool> failed: <summary>，请见 <run link>"`，**不允许 review**
- 🟡 **warn**：评论 `"⚠️ <tool> warning: <summary>"`，reviewer 自行决定

### 3.3 自动标签

| 标签 | 触发条件 | 用途 |
|------|----------|------|
| `area/controller` | diff 触碰 `controller/**` | 路由 |
| `area/attacker` | diff 触碰 `attacker/**` | 路由 |
| `area/security` | diff 触碰 `auth.py`/`crypto.py`/`SECURITY.md` | 强制 2 reviewer |
| `size/S` | < 100 行 | 流程加速 |
| `size/M` | 100-500 行 | 标准流程 |
| `size/L` | 500-1000 行 | 标准 + 1 maintainer |
| `size/XL` | > 1000 行 | **必须先拆 PR** |

> `size/XL` PR 机器人直接评论 `请拆分为 < 500 行的子 PR`。

---

## 4. 阶段 ③ — 人工 Review

### 4.1 Reviewer 路由

- 机器人按文件路径 `@` 负责模块的 owner（参考 `CODE_REVIEW_STANDARDS.md §6`）
- 默认 SLA：**24 小时内首次响应**（工作日），非工作日顺延
- 高敏模块 PR：48 小时内必须结案（approve / request-changes / withdraw）

### 4.2 Reviewer 数量门槛

| 变更类型 | Reviewer 数 | 备注 |
|----------|-------------|------|
| 文档/typo/格式 | 1 | 任意 reviewer |
| 一般功能/重构（size S/M） | 1 | 模块 owner |
| 复杂功能/重构（size L） | 2 | 1 模块 owner + 1 maintainer |
| **高敏模块**（auth/crypto/registry/orchestrator/audit/workflows/deploy） | 2 | 必须有 1 安全/架构 owner ⭐ |
| 紧急 hotfix | 1 (oncall) | 见 §6 |

### 4.3 Reviewer 反馈原则

参考 `CODE_REVIEW_STANDARDS.md` 优先级标记：

```
🔴 Blocker — 必须修才能合并
🟡 Major   — 必须修或带 issue defer
🟠 Minor   — 建议修
💭 Nit     — 偏好，可不改
🌟 Praise  — 表扬
```

**Reviewer 必须**：
- 评论针对代码，不针对人
- 给出**具体行号**（不要 "这里有问题"）
- **明确分类**优先级（不要全 🔴）
- 至少 **1 条 🌟 Praise**（如果完全没夸 → 重新 review，可能没认真看）
- 24h 内首响，48h 内提交结论

### 4.4 Author 响应反馈

- 🔴 Blocker：**必须修**，无理由不改
- 🟡 Major：要么修，要么创建 follow-up issue 并 PR 上写 `Refs: #issue`
- 🟠 Minor / 💭 Nit：可标记 `won't fix` 简短说明理由
- 不同意 reviewer 评论：见 §5 争议解决

### 4.5 重新提交

- Push 新 commit 后，CI 自动重跑门禁
- Reviewer 需 **重新 review 改动部分**，但不必全 diff 重看
- 之前已 approve 的 reviewer 在 diff 改动后 **approve 自动失效**（需重新 approve）

---

## 5. 争议解决 (Conflict Resolution)

当 Reviewer 与 Author 对评论有分歧：

### 5.1 三步升级

1. **讨论对齐**（作者 + reviewer 在 PR 评论里 sync，2 工作日内）
   - 作者给出技术依据，reviewer 给标准依据
   - 寻求折中方案

2. **拉第三方案**（任一方打 `?` emoji 或 comment 提及 `@maintainers`）
   - 任意 maintainer 给中立意见
   - 多数决（2 maintainer 一致即终局）

3. **架构 owner 终裁**（重大分歧，如安全/性能）
   - 项目 owner（当前默认：zhang123999-qq 或其指定）有最终决定权
   - 终裁结果登记到 `docs/REVIEW_DISPUTES.md`（待补），季度复盘

### 5.2 不可妥协事项

下列争议 **不接受作者"won't fix"**，必须升级：

- 🔴 Blocker 类别（参考标准 §2）
- 法律/合规相关
- 安全 owner 反对的安全变更

---

## 6. 紧急流程 (Hotfix & Emergency)

### 6.1 适用场景

- 生产环境安全漏洞
- 节点大面积下线
- 紧急功能紧急修复

### 6.2 流程

1. Author 写 commit，message 前缀 `hotfix(vX.Y.Z):`
2. 开 PR 标题前缀 `[HOTFIX]`，自动标 `priority/critical`
3. **可跳过模板必填项**，但必须写：
   - 紧急原因（< 50 字）
   - 影响范围
   - 验证步骤
4. Reviewer SLA：**2 小时**（oncall 需立即响应）
5. Reviewer 数：**1 名 oncall maintainer**（够）
6. 合并后 **24 小时内**补提交：
   - 完整测试用例
   - DEEP_EVALUATION 关闭对应条目
   - CHANGELOG 详细说明

### 6.3 事后复盘

任何 hotfix 合并后 **7 天内**必须开复盘 issue，登记到 `docs/HOTFIX_POSTMORTEMS/`。

---

## 7. Reviewer 职责与权益

### 7.1 责任

- 保护主干质量
- 教学：评论要说 why
- 及时响应 SLA
- 严守标准，不放水

### 7.2 权益

- 被 reviewer 阻塞的 PR，Author 不得绕过（除非紧急豁免）
- Reviewer 评论可被 `won't fix`，但 Blocker 不可

### 7.3 Reviewer 资质

- **模块 owner**：在 `docs/MAINTAINERS.md`（待补）登记
- **临时 reviewer**：对某模块熟悉 3 个月以上，可申请

---

## 8. 度量与改进 (Metrics)

每月统计：

| 指标 | 目标 | 数据来源 |
|------|------|----------|
| PR 首次响应中位时间 | < 8h | GHA/GitLab API |
| PR 合并中位时间（不含等待 author） | < 24h | GHA/GitLab API |
| Reviewer 评论后 Author 二次提交时间 | < 24h | GHA/GitLab API |
| 高敏模块 PR 双 reviewer 达成率 | 100% | GitLab API |
| 🔴 Blocker 触发后被 defer 比例 | < 5% | 人工抽样 |
| 拒绝合并率（被 reviewer 打回） | 趋势监控 | GHA/GitLab API |

季度 review：
- 维护 `docs/REVIEW_DASHBOARD.md`
- 找出最长尾 PR，分析是流程问题还是 reviewer 问题
- 标准本身也要 review（§10）

---

## 9. 豁免与例外

| 例外 | 触发 | 申请人 | 批准人 |
|------|------|--------|--------|
| 跳过 size/XL 拆分 | 不可拆（如单文件迁移） | Author | 任一 maintainer |
| 单 reviewer 而非双 | size S 一般变更 | Author | 模块 owner |
| 跳过某个工具 gate | 工具误报 | Author | maintainer + 在 PR 写明 |
| 紧急合并跳过 review | hotfix | Author | oncall maintainer（事后补） |

所有豁免必须在 PR 描述里写明，**机器人自动标 `exemption/<reason>` 标签**。

---

## 10. 流程本身的 review

- **每月** Owner 回顾：
  - 是否有 reviewer 频繁被绕过？
  - 是否有 PR 类型反复出同样问题（→ 写进 CONTRIBUTING.md 顶部）
  - 工具链是否需要升级？
- **每版本**（v1.5.0 / v2.0.0）回访：
  - 标准条目是否仍贴合项目现状？
  - 流程瓶颈是否变化？

修改流程本文件需走 PR + 2 maintainer 批准。

---

## 附录：完整 PR 生命周期示例

```
Day 0 09:00  Author 提交 PR #123
Day 0 09:05  Bot: ✅ 格式/lint/测试/扫描通过，标 size/M + area/controller
Day 0 09:05  Bot: @module-owner 请审
Day 0 14:30  Reviewer A 留 3 评论（1 🔴 + 2 🟠），请求修改
Day 0 16:00  Author 修复 + push
Day 0 16:05  Bot: CI 重跑绿
Day 1 10:00  Reviewer A 重新 approve
Day 1 11:00  Reviewer B (maintainer) 留 1 💭，approve
Day 1 11:30  Author 回复 💭 "won't fix, will improve in next refactor"
Day 1 11:35  Author 点击 Merge (auto delete branch)
Day 1 11:35  Bot: 关闭关联 issue，更新 CHANGELOG draft
```

---

**附录 A**: 与 CONTRIBUTING.md §4 的关系
- 本文件是 §4 的**正式化扩展**，含具体工具、阈值、SLA
- §4 中与本文件冲突的条目，以本文件为准

**附录 B**: 与 CODE_REVIEW_STANDARDS.md 的关系
- STANDARDS.md 定义"什么必须修"
- 本文件定义"怎么修、谁修、多快修"
- 两者配对使用