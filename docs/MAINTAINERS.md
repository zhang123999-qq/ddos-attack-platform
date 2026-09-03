# MAINTAINERS

> **文档版本**: v1.0
> **首次生效**: 2026-08-31
> **最近更新**: 2026-09-02
> **owner**: 项目创始人 + Maintainer 团队

本文件定义**谁有权合并 PR、谁对哪些模块负责、谁对安全把关**。参考 [`CODE_REVIEW_STANDARDS.md`](CODE_REVIEW_STANDARDS.md) §6 高敏模块必审清单使用。

---

## 1. 角色定义

| 角色 | 权限 | 责任 | 数量 |
|------|------|------|------|
| **Owner** | 全权（含破坏性变更、release） | 项目愿景、最终决策、争议终裁 | 1-2 |
| **Maintainer** | 合并 PR、批准 release tag、推动 issue | 模块代码质量、SLA 响应、回答 issue | 2-4 |
| **Module Owner** | 标 ✅ 的模块 PR 必审、approve 该模块 | 模块架构、设计决策、向后兼容 | 每个核心模块 1-2 |
| **Security Owner** | 安全 PR 必审、可阻止合并 | mTLS/Token/审计/白名单基线 | 1-2（与 Maintainer 重叠） |
| **Reviewer**（轮值） | 普通 approve/reject | 24h 内审完分配 PR | 任意多人 |

> **当前阶段**：项目早期，Maintainer 与 Module Owner 可由同一人承担，但每模块至少 1 名 owner。

---

## 2. 当前 Maintainers

> ⚠️ **草稿**：以下名字均为占位 (`TBD-*`)，请实际维护者替换或删行。

### 2.1 Owner

| Name | GitLab/GitHub | Contact | 任期 |
|------|--------------|---------|------|
| `TBD-OWNER-1` (项目创始人) | @TBD-OWNER-1 | maintainers@<your-company>.internal | 2024-01 起 |

### 2.2 Maintainer（合并权 + 路由 PR）

| Name | 负责模块 | Contact | 任期 |
|------|---------|---------|------|
| `TBD-MAINTAINER-1` | controller 全栈 + auth/registry/orchestrator | maintainers@ | TBD |
| `TBD-MAINTAINER-2` | attacker 全栈 + crypto/attacks | maintainers@ | TBD |
| `TBD-MAINTAINER-3` | deploy 脚本 + CI/CD | maintainers@ | TBD |

### 2.3 Module Owner（必审权）

| 模块 | 文件 | Owner | 备选 Owner |
|------|------|-------|-----------|
| **auth** ⭐ | `controller/app/auth.py` | TBD-MAINTAINER-1 | TBD-OWNER-1 |
| **audit** ⭐ | `controller/app/audit.py` | TBD-MAINTAINER-1 | TBD-OWNER-1 |
| **registry** ⭐ | `controller/app/registry.py` | TBD-MAINTAINER-1 | TBD-OWNER-2 |
| **orchestrator** ⭐ | `controller/app/orchestrator.py` | TBD-MAINTAINER-1 | TBD-OWNER-1 |
| **node_commander** ⭐ | `controller/app/node_commander.py` | TBD-MAINTAINER-1 | TBD-MAINTAINER-2 |
| **ratelimit** | `controller/app/ratelimit.py` | TBD-MAINTAINER-1 | — |
| **scenario** | `controller/app/scenario.py` | TBD-MAINTAINER-2 | — |
| **websocket** | `controller/app/websocket.py` | TBD-MAINTAINER-2 | — |
| **crypto** ⭐ | `attacker/app/crypto.py` | TBD-MAINTAINER-2 | TBD-OWNER-1 |
| **attacks base** ⭐ | `attacker/app/attacks/base.py` | TBD-MAINTAINER-2 | TBD-OWNER-1 |
| **health/main** | `attacker/app/*.py`（除上） | TBD-MAINTAINER-2 | — |
| **deploy** ⭐ | `deploy/*.sh` | TBD-MAINTAINER-3 | TBD-OWNER-1 |
| **CI/CD** ⭐ | `.github/workflows/*` | TBD-MAINTAINER-3 | — |
| **docs** | `docs/**` | 任意 Maintainer | — |
| **build** | `build/build.py` | TBD-MAINTAINER-3 | — |

⭐ = 高敏模块，必须由本列 owner 之一 + 另一位 Maintainer 批准才能合并（参见 [`CODE_REVIEW_STANDARDS.md`](CODE_REVIEW_STANDARDS.md) §6）

### 2.4 Security Owner ⭐

| Name | 角色 | 权限 |
|------|------|------|
| `TBD-OWNER-1` | 项目 Owner（兼任 Security Owner） | 可对任何 PR 行使否决权、要求修改；安全漏洞紧急响应 |
| `TBD-MAINTAINER-1` | Maintainer（兼任） | 共同审 ⭐ 模块 |

> **Security Owner 一票否决**适用于：
> - 任何 🔴 Blocker 类 PR（参见 [`CODE_REVIEW_STANDARDS.md`](CODE_REVIEW_STANDARDS.md) §2）
> - `auth.py` / `crypto.py` / `SAFETY_RULES.md` / `SECURITY.md` 的任何改动
> - 引入新依赖、新网络出口、新存储介质

### 2.5 Reviewer 轮值表

> 每周一轮值，下表为初始模板：

| 周次 | On-call | 备注 |
|------|---------|------|
| 2026-W36 | TBD-REVIEWER-1 | 试运行第 1 周 |
| 2026-W37 | TBD-REVIEWER-2 | 试运行第 2 周 |
| 2026-W38 | TBD-REVIEWER-3 | 试运行第 3 周 |

On-call 责任：
- 24h 内首响所有分配 PR
- 48h 内结论
- 紧急 hotfix 2h 内响应

---

## 3. 提名与退出

### 3.1 提名新 Maintainer

1. 现有 Maintainer 推荐候选
2. 候选需满足：
   - 至少 6 个月活跃贡献（merged PR ≥ 20）
   - 在其负责模块至少合并过 5 个 PR
   - 通过现有 Maintainer 2/3 投票
   - Security Owner 同意（如候选将兼任 Security Owner）
3. Owner 在此文件 §2 中添加条目

### 3.2 退出

- Maintainer 可随时主动退出（PR 修改本文件即可）
- 长期失联（> 3 个月无响应、无 PR 评审）由 Owner 移出本文件
- 重大违规（违反 `SAFETY_RULES.md` 红线、泄露密钥）立即移除并公告

---

## 4. 紧急联系 (Escalation Path)

| 优先级 | 场景 | 联系人 |
|--------|------|--------|
| 🔴 紧急 | 生产事故、安全漏洞、密钥泄露 | On-call Owner（24/7） |
| 🟡 高 | 高敏模块 PR、争议 PR | Security Owner + 任一 Maintainer |
| 🟢 中 | 一般 PR 阻塞 | 任一 Maintainer |
| ⚪ 低 | 文档/typo/重构 | 任意 Reviewer |

联系渠道（按顺序）：
1. 内部 IM (企业微信/TBD)
2. 邮件 `maintainers@<your-company>.internal`
3. 紧急电话（Owner 私有，On-call 周通知）

---

## 5. 与 CODE_REVIEW_STANDARDS / REVIEW_PROCESS 的关系

- **本文件**回答"谁负责"（人）
- [`CODE_REVIEW_STANDARDS.md`](CODE_REVIEW_STANDARDS.md) 回答"什么必须修"（标准）
- [`REVIEW_PROCESS.md`](REVIEW_PROCESS.md) 回答"怎么走流程"（流程）

三者配对使用，缺一不可。

---

## 6. 文件维护规则

- 修改本文件需 Owner 批准后 PR 合并
- 任期开始/结束需同步 CHANGELOG
- 紧急提名（临时 Security Owner）可口头授权 + 3 天内补 PR

---

**生效前提**：本文件 §2 中所有 `TBD-*` 占位必须替换为实际维护者后才能正式启用审查流程。
**预计生效日**：2026-09-07（下周一前老板点完人即生效）。