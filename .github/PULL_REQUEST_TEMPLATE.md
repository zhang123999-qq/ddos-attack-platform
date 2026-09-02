<!-- .github/PULL_REQUEST_TEMPLATE.md -->

## 变更类型

- [ ] feat: 新功能
- [ ] fix: bug 修复
- [ ] docs: 文档
- [ ] refactor: 重构
- [ ] test: 测试
- [ ] perf: 性能
- [ ] chore: 杂项
- [ ] hotfix: 紧急修复

## 变更摘要

（一句话描述）

## 变更原因

（背景 / issue 链接 / 业务诉求）

## 关联

Refs: TD-7 / REG-1 / Issue #123
Closes: #123

## 影响范围

- [ ] API 路径 / 参数 / 响应变更
- [ ] config.env 字段变更
- [ ] 数据模型 / 数据库 schema 变更
- [ ] 部署脚本 / Docker 镜像变更
- [ ] 文档需要同步更新
- [ ] breaking change: 说明

## 高敏模块

- [ ] 涉及 `controller/app/auth.py`
- [ ] 涉及 `controller/app/audit.py`
- [ ] 涉及 `controller/app/registry.py`
- [ ] 涉及 `controller/app/orchestrator.py`
- [ ] 涉及 `controller/app/node_commander.py`
- [ ] 涉及 `attacker/app/crypto.py`
- [ ] 涉及 `attacker/app/attacks/base.py`
- [ ] 涉及 `.github/workflows/**`
- [ ] 涉及 `deploy/**`

> 勾选上述任一项，本 PR 必须有 **2 名 reviewer**，其中至少 1 名为安全/架构 owner。

## 测试

- [ ] 单元测试: X/X PASS (新增 N 个)
- [ ] E2E: 套件 X/X PASS
- [ ] bandit: 0 high/medium
- [ ] gitleaks: clean
- [ ] pip-audit: 0 high vuln

## 文档同步

- [ ] `README.md`
- [ ] `CHANGELOG.md`
- [ ] `API_REFERENCE.md` (如 API 变)
- [ ] `ARCHITECTURE.md` (如架构变)
- [ ] `DEEP_EVALUATION_v*.md` (如关 TD/REG)
- [ ] `SAFETY_RULES.md` / `SECURITY.md` (如策略变)

## Checklist

- [ ] 我已本地跑通 `ruff format && ruff check && pytest`
- [ ] 我已阅读 [`CODE_REVIEW_STANDARDS.md`](../docs/CODE_REVIEW_STANDARDS.md)
- [ ] 我已阅读 [`REVIEW_PROCESS.md`](../docs/REVIEW_PROCESS.md)
- [ ] 高敏模块改动已通知安全 owner
- [ ] 无敏感信息 (密钥/真实 IP/真实公司名) 出现在 diff/注释/示例中