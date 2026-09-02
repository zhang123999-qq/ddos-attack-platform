# 代码审查标准 (Code Review Standards)

> **文档版本**: v1.0
> **适用平台版本**: v1.4.1+
> **最近更新**: 2026-08-31
> **owner**: CodeReviewExpert + Maintainers

本标准是 PR 通过/打回的唯一硬性依据。审查者按此打分，**任何 🔴 Blocker 必须修复才能合并**。

---

## 0. 审查哲学

1. **审查代码，不是审查人** — 评论聚焦行为与原因，避免"我觉得你..."
2. **每个评论都教学** — 解释 *why*，让人下次写得更对，不是这次妥协
3. **分级、明确、可验证** — 🔴 必须修、🟡 应该修、💭 可讨论
4. **机器能做的别用人** — 格式/lint/类型/扫描交工具，留给人做架构/业务判断
5. **一次到位** — review 完整闭环，不要拆三轮拖时间

---

## 1. 审查优先级标记 (Severity)

| 标记 | 含义 | 合并要求 |
|------|------|----------|
| 🔴 **Blocker** | 安全/数据/合规风险 | 必须修复，**不能 resolve with justification**（除授权豁免） |
| 🟡 **Major** | 正确性、性能、可维护性问题 | 必须修复或显式 defer（带 issue 编号） |
| 🟠 **Minor** | 命名、抽象、文档缺失 | 建议修复，作者决定 |
| 💭 **Nit** | 风格、可读性偏好 | 可不改，下个 PR 处理也行 |
| 🌟 **Praise** | 优秀做法 | 仅留作正向反馈 |

---

## 2. 🔴 Blocker — 必须修到 0 才能合并

### 2.1 安全红线 (Security)

任何触及下列项的 PR **必须**由具备安全评审资格的 reviewer 之一审查。

- [ ] **认证 / 鉴权绕过**
  - 新增 endpoint 默认无认证 → 🔴（必须加 `Depends(verify_controller_token)` / `verify_node_token`）
  - Token 校验逻辑被修改（`auth.py`/`crypto.py` 中 `verify_*` / `generate_*`）→ 🔴
  - 关闭 mTLS / 弱化 cipher 套件 → 🔴
  - 例：v1.4.1-hotfix6 加固的 `REQUIRE_SHARED_SECRET` 不能被任何 PR 削弱

- [ ] **密钥 / 凭据处理**
  - 硬编码 `SHARED_SECRET`、私钥、证书内容、token → 🔴（即便注释里也算）
  - 日志/异常 traceback 打印 secret/token/cookie → 🔴
  - 弱哈希（MD5/SHA1 用于密码/凭据）→ 🔴

- [ ] **注入类漏洞**
  - 命令注入：`os.system(f"ping {target}")` / `subprocess.run(f"...{user_input}...")` → 🔴
  - SQL 注入：拼接 SQL 字符串（项目目前无 SQL，但若引入必须参数化）→ 🔴
  - SSRF：让 server 主动访问用户提供的 URL 且未校验内网/元数据 IP → 🔴
  - 路径穿越：`open(f"/certs/{filename}")` 未校验 `..` → 🔴

- [ ] **越权 / 提权**
  - 节点侧能调用的 endpoint 列表被扩大（无新增白名单校验） → 🔴
  - 任何绕过 `SafeAttackBase.pre_flight_check` 的攻击启动路径 → 🔴
  - 修改 `TargetValidator` / `pre_flight_check` 逻辑的 PR → 🔴

- [ ] **拒绝服务放大**
  - 攻击节点允许的 RPS/PPS/并发上限被调高 → 🔴（必须配套 audit + 红线评估）
  - 全局限流 `GLOBAL_MAX_RPS` / `GLOBAL_MAX_PPS` 被调低/移除 → 🔴
  - `emergency_stop()` 调用路径被延迟或异步化 → 🔴（必须保持同步 <100ms）

### 2.2 数据完整性

- [ ] 审计事件丢失/被过滤（`audit.py` 中 `record_event` 调用被条件跳过）→ 🔴
- [ ] 节点注册白名单被绕过（`registry.py` enroll 流程改动）→ 🔴
- [ ] 数据库/状态机无锁访问（新增共享状态未加 `asyncio.Lock`）→ 🔴

### 2.3 合规与法律

- [ ] 删除或削弱 `SAFETY_RULES.md` 约束条款 → 🔴
- [ ] 注释/文档中包含真实授权 IP/真实公司名/真实公网 IP → 🔴
- [ ] LICENSE 检查：依赖引入未审计 license（GPL 等 copyleft 进入内网专有项目）→ 🔴

### 2.4 引入破坏性变更未声明

- [ ] 改 API 路径/参数/响应字段，CHANGELOG.md 未写 `BREAKING CHANGE` → 🔴
- [ ] config.env 字段重命名/移除未在 CHANGELOG 标注 → 🔴
- [ ] docker-compose 端口/卷挂载变更未声明 → 🔴

---

## 3. 🟡 Major — 必须修或带 issue defer

### 3.1 正确性

- 异常被 `except: pass` 或 `except Exception: pass` 吞掉
- 异步代码里出现同步 IO（`requests.get` / `time.sleep`）阻塞事件循环
- `asyncio.create_task` 创建的 task 未持有引用或未 `await`/gather，可能丢失异常
- `await lock.acquire()` 未在 `finally` 中释放（应改用 `async with lock`）
- 时间比较用 `==` 比较 `datetime` 而非容差

### 3.2 错误处理

- 公开 API 返回 500 但 traceback 未落到日志
- 重试逻辑无指数退避或无最大重试次数（参考 `test_error_backoff.py` 已规范）
- 熔断/限流失败回退到"无限流"（fail-open）→ 必须是 `allow-closed`（除非明确开关）

### 3.3 性能

- N+1 查询/调用（在循环里发 HTTP/RPC，未聚合）
- 循环里 `json.dumps`/`yaml.safe_load` 大对象
- `re.search` 编译的正则未缓存到模块级
- `list.append` + 末尾循环，考虑是否可换 `list comprehension` 或预分配

### 3.4 并发安全

- 共享字典/列表在并发上下文读写未加锁
- `dict[k] = v` 和 `k in dict` 之间存在 race（应合并为单操作或加锁）
- asyncio `gather` 捕获子任务异常不全，导致 `ExceptionGroup` 静默吞掉

### 3.5 可观测性

- 关键路径（攻击启动/熔断/认证失败）无 structlog 日志
- 日志级别混乱：用 `info` 打印每条心跳 → 应 `debug`
- 异常带 context：应 `logger.exception("x_failed", target=...)` 而非 `logger.error(str(e))`

### 3.6 可测试性

- 新增核心逻辑无对应单元测试（参考 `DEEP_EVALUATION_v3.md` NEW-2 教训）
- 测试断言只测"不抛异常" 而不测业务结果
- 测试用真实网络/真实文件/真实时间（应 mock）

### 3.7 抽象与命名

- 函数 > 80 行（应拆）
- 模块 > 600 行（应拆，按职责）
- 函数/变量名误导（如 `count` 实际返回 `list`）
- 模块导入 `from app.foo import *`

---

## 4. 🟠 Minor — 建议修

- 命名风格不统一（局部变量用驼峰、混入下划线）
- docstring 缺失（公开 API）
- 复杂表达式未提常量（如 `range(3600)` 应命名 `ONE_HOUR_SECONDS`）
- 类型注解不全（v1.5.0 启用 mypy strict 前是 minor；启用后升 major）
- 测试覆盖率 < 80% 的新增模块（参考 `docs/COVERAGE.md` 待补）

---

## 5. 💭 Nit — 偏好，可不改

- 字符串引号风格（除非 ruff 强制）
- 字典字面量换行偏好
- import 分组顺序（交给 isort/ruff）
- docstring 段落空行偏好

---

## 6. 高敏模块必审清单 (Critical-Modules Policy)

下列模块的 PR **必须**有 2 个 reviewer 批准，其中至少 1 个标 ⭐ 为"安全/架构 owner"：

| 模块 | 路径 | 必审理由 |
|------|------|----------|
| **auth** | `controller/app/auth.py` | Token 派生/验证 |
| **crypto** | `attacker/app/crypto.py` | mTLS / HMAC / 指纹钉扎 |
| **registry** | `controller/app/registry.py` | 节点身份 & 状态机 |
| **orchestrator** | `controller/app/orchestrator.py` | 全局编排 & 限流 |
| **node_commander** | `controller/app/node_commander.py` | 指令下发通道 |
| **audit** | `controller/app/audit.py` | 审计不可丢 |
| **attack base** | `attacker/app/attacks/base.py` | 安全基类 |
| **deploy/install.sh** | `deploy/*.sh` | 安装权限/路径 |
| **workflows** | `.github/workflows/*` | CI 权限与发布链路 |

> **owner 名单**：见 `docs/MAINTAINERS.md`（待补）；当前默认所有 controller 核心模块须任意 2 maintainer 批准。

---

## 7. 测试覆盖度要求

| 变更类型 | 单元测试 | E2E |
|----------|---------|-----|
| 新增 endpoint | 必须覆盖 200/4xx/5xx 各一 | 路径覆盖 |
| 修改认证/限流 | 必须覆盖正常 + 异常 + 边界 | 必跑 attack E2E |
| 修改攻击算法 | 必须覆盖参数边界 + 异常目标 | 必跑 attack E2E |
| 修改 deploy 脚本 | 关键路径需单元 (bats) | 必跑 install/uninstall E2E |
| 纯文档/重构 | 已有测试仍通过即可 | 不强求 |

**覆盖度阈值**：
- 核心模块（auth/registry/orchestrator）≥ 85%
- 一般模块 ≥ 70%
- 工具脚本/UI ≥ 50%

---

## 8. 文档同步要求

| 变更 | 必须同步 |
|------|----------|
| 新增 API endpoint | `API_REFERENCE.md` |
| 修改环境变量 | `README.md` 配置章节 + `config.env.example` 注释 |
| 修改架构/数据流 | `ARCHITECTURE.md` |
| 修复技术债 | `DEEP_EVALUATION_v*.md` 关闭对应条目 |
| 安全策略变更 | `SECURITY.md` + `SAFETY_RULES.md` |
| 任何行为变更 | `CHANGELOG.md` |

---

## 9. 性能预算 (Performance Budget)

| 场景 | 预算 |
|------|------|
| Controller 普通 API P99 | < 50ms |
| Controller `emergency_stop` 全网生效 | < 100ms |
| 节点心跳处理 | < 20ms |
| 攻击指令下发（含 mTLS 握手复用） | < 200ms |
| WebSocket 推送吞吐 | ≥ 1000 msg/s |

> 任何 PR 引入性能回归 ≥ 20% 必须配 benchmark。

---

## 10. Reviewer 自查清单 (Reviewer Self-Check)

完成 review 前自问：

- [ ] 我看完了整个 PR 的 diff，没漏掉文件？
- [ ] 我读了 commit message，理解变更目的？
- [ ] 我对每条评论给出了具体行号？
- [ ] 我区分了 blocker / major / minor / nit？
- [ ] 我点出了至少一处做得好（如果完全没有 → 重新 review，可能没认真看）？
- [ ] 高敏模块我是否在 6 个 owner 中？（如不是，是否有 owner 标记 approve？）

---

**附录 A**: 与 CONTRIBUTING.md §8 的关系
- 本文件是 §8 的**正式化扩展**，优先级高于 §8
- §8 中与本文件冲突的条目，以本文件为准

**附录 B**: 本标准更新方式
- 修改需在 PR 中提交，开 review
- Maintainers 2 人批准后生效
- 主版本变更（新增 🔴 类别）需提前 1 个版本公告