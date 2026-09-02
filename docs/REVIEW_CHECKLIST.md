# Reviewer 速查清单 (Quick Checklist)

> **文档版本**: v1.0
> **适用平台版本**: v1.4.1+
> **最近更新**: 2026-08-31
> **用途**: Reviewer 拿到 PR 后 5 分钟过一遍这份清单

> ⚡ **本清单不替代** [`CODE_REVIEW_STANDARDS.md`](CODE_REVIEW_STANDARDS.md) 和 [`REVIEW_PROCESS.md`](REVIEW_PROCESS.md)，
> 只是把它们的核心要点浓缩成 review 现场的速查清单。先看完这两份再 review。

---

## 🟢 0. 先看全局 (5 分钟)

- [ ] PR 标题是 Conventional Commits 格式？(`feat/fix/refactor/...`)
- [ ] PR 描述填了模板字段？特别是：变更类型、影响范围、关联 issue
- [ ] diff 行数 < 1000？（> 1000 必须拆）
- [ ] diff 触碰了高敏模块？（见 §5）→ 强制 2 reviewer
- [ ] CI 全绿？（format/lint/类型/bandit/gitleaks/测试）
- [ ] 读完 commit message 理解变更目的

---

## 🔴 1. 安全 (Security) — 🔴 必查

- [ ] **认证**：新 endpoint 都过 `Depends(verify_controller_token)` / `verify_node_token`？
- [ ] **Token/密钥**：无硬编码、无日志泄露、无回显到 traceback
- [ ] **TLS**：未关闭 mTLS / 未弱化 cipher / 未降低最低 TLS 版本
- [ ] **命令注入**：`subprocess` / `os.system` 调用是否全用列表形式 + 严格参数校验
- [ ] **路径穿越**：文件操作未校验 `..` 或绝对路径
- [ ] **SSRF**：未让 server 主动访问用户控制的 URL（当前项目未涉及，但若新增需警觉）
- [ ] **越权**：节点能调用的 endpoint 列表未扩大；攻击启动必经 `pre_flight_check`
- [ ] **DoS 放大**：未提高节点 RPS/PPS/并发上限；全局限流未降；emergency_stop 仍同步 <100ms

---

## 🔴 2. 数据完整性 (Data Integrity)

- [ ] 审计事件 (`audit.record_event`) 未被条件跳过
- [ ] 节点注册 enroll 流程未绕过验证
- [ ] 共享状态（registry/attack 状态机）读写有锁
- [ ] 数据库/SQL（若有）参数化（项目当前无 SQL，但若引入必查）
- [ ] JSON/YAML 解析未使用 `yaml.load`（应 `safe_load`）/ `pickle` 反序列化未受信输入

---

## 🟡 3. 正确性 (Correctness)

- [ ] 异常未吞掉（无 `except: pass`）
- [ ] 异步代码无同步 IO 阻塞（`requests` / `time.sleep` / 同步 `open`）
- [ ] `asyncio.create_task` 创建的 task 持有引用、异常会冒泡（`gather` 或保存 task 对象）
- [ ] `await lock.acquire()` 在 `finally` 释放（应改 `async with lock`）
- [ ] 时间比较用差值容差，不用 `==`
- [ ] 重试有最大次数 + 指数退避 + jitter
- [ ] 熔断/限流失败是 fail-closed（不是 fail-open，除非有显式开关）

---

## 🟡 4. 性能 (Performance)

- [ ] 无 N+1 调用（循环里发 HTTP/RPC）
- [ ] 无不必要的 `json.dumps`/`yaml.safe_load` 在热路径
- [ ] 复杂正则 `re.compile` 提到模块级
- [ ] 无同步 IO 在 async 函数里
- [ ] 大循环考虑用 `asyncio.gather` 或批量 API
- [ ] 引入第三方库前先看是否已有等价实现（避免 bloat）

---

## 🟡 5. 并发安全 (Concurrency)

- [ ] 共享字典/列表读写有锁（`asyncio.Lock` / `threading.Lock`）
- [ ] `dict[k] = v` 与 `k in dict` 之间有 race → 用 `dict.setdefault` / `Lock`
- [ ] WebSocket 推送并发订阅未泄漏句柄
- [ ] 后台 task 退出路径有清理（cancellation / shutdown hook）
- [ ] asyncio.Event/Semaphore 初始化与释放配对

---

## 🟡 6. 可观测性 (Observability)

- [ ] 关键路径有 structlog：`auth_failure` / `attack_start` / `emergency_stop` / `registry_update`
- [ ] 日志带 context（`target_ip=...` / `node_id=...` / `duration_ms=...`）
- [ ] 异常用 `logger.exception(...)` 而非 `logger.error(str(e))`
- [ ] 日志级别合理（心跳用 `debug`，状态变化用 `info`，错误用 `error/warning`）
- [ ] 公开 API 错误响应不带 stacktrace 给客户端

---

## 🟡 7. 可测试性 (Testability)

- [ ] 新功能有对应单元测试（200/4xx/5xx 各一）
- [ ] 边界条件覆盖：空输入 / 最大值 / 异常输入
- [ ] 测试用 mock，不依赖真实网络/时间/文件
- [ ] 测试独立：无执行顺序依赖（除显式 fixture）
- [ ] 测试可重复：重复运行结果一致
- [ ] 单元测试 < 5s（快速反馈）
- [ ] 高敏模块覆盖度 ≥ 85%（参考 coverage 报告）

---

## 🟠 8. 抽象与可维护性

- [ ] 函数 < 80 行
- [ ] 模块 < 600 行（否则建议拆）
- [ ] 命名一致且不误导（`count` 真返回 `count`，不是 `list`）
- [ ] 无 `from app.x import *`
- [ ] 无循环依赖（`a` 导入 `b`，`b` 又导入 `a`）
- [ ] 配置走 `config.env` / `os.getenv`，无硬编码常量
- [ ] 业务常量提常量（如 `MAX_CONCURRENT_PER_NODE = 5000`）
- [ ] 公开 API 有 docstring

---

## 🟠 9. 文档同步

- [ ] API 变 → `API_REFERENCE.md` 更新
- [ ] env 变 → `README.md` + `config.env.example` 注释
- [ ] 架构/数据流变 → `ARCHITECTURE.md` 更新
- [ ] 关技术债 → `DEEP_EVALUATION_v*.md` 关闭条目
- [ ] 安全策略变 → `SECURITY.md` + `SAFETY_RULES.md`
- [ ] 行为变更 → `CHANGELOG.md` 增条目

---

## 💭 10. Nit（可选）

- 字符串引号统一（项目用 `"`）
- import 分组（标准库/三方/本项目）
- `print` / `breakpoint()` 未残留（应被 hook 拦下）
- 调试注释 `TODO` / `FIXME` 关联 issue
- 文件末尾单换行

---

## ⭐ 11. 高敏模块专项 (Critical Modules)

| 模块 | 重点关注 |
|------|----------|
| `controller/app/auth.py` | 任何 `verify_*` / `generate_*` 改动必须检查全调用链 |
| `attacker/app/crypto.py` | HMAC message、hash 算法、random 来源 |
| `controller/app/registry.py` | 状态机迁移、TTL、并发锁 |
| `controller/app/orchestrator.py` | `emergency_stop` 同步路径、限流逻辑 |
| `controller/app/node_commander.py` | 指令下发鉴权、超时、重试 |
| `controller/app/audit.py` | 事件是否被过滤、落盘路径 |
| `attacker/app/attacks/base.py` | `pre_flight_check` 调用链、白名单校验 |
| `.github/workflows/*` | 权限 `permissions:`、secrets 引用、触发条件 |
| `deploy/install.sh` | `set -e`、幂等、权限、`ddos` 用户创建 |

---

## ✅ 12. Reviewer 自查

提交 review 前：

- [ ] 我看完了**所有**文件改动，没漏
- [ ] 我读了 commit message 和 PR 描述
- [ ] 每条评论都有**具体行号**
- [ ] 我用了分级标记（🔴🟡🟠💭🌟）
- [ ] 至少 **1 条 🌟 Praise**
- [ ] 高敏模块确认了 2 reviewer
- [ ] 我在 SLA 内（24h 首响 / 48h 结论）

---

## 📋 Review Comment 模板

reviewer 用这个模板写评论（Markdown 渲染友好）：

```markdown
🔴 **安全：Token 校验逻辑被旁路**
文件: `controller/app/auth.py:154`

**问题**：
```python
if not auth_config.verify_token(token):
    logger.warning(...)
    raise HTTPException(...)
```

在新增的 `/health-debug` endpoint 里调用了 `verify_token`，但用了 `verify_token_or_none`，
允许无 token 访问，绕过认证。

**为什么是 blocker**：
任何匿名调用者都能调 `/health-debug`，可能泄露内部状态。

**建议**：
- 保持原 `verify_token` 严格校验
- 如果 debug endpoint 是设计需要，加 `/api/v1/admin/health-debug` 并独立鉴权
- 参考 `CODE_REVIEW_STANDARDS.md §2.1`

**相关**：`auth.py:154-156`, `main.py:233`
```

---

## 🚫 13. 不该做的事 (Anti-patterns)

| ❌ 反模式 | ✅ 该做的 |
|----------|-----------|
| "这看起来不对" | "L42：`x` 在并发上下文未加锁，会和 L58 产生数据竞争，建议加 `asyncio.Lock`" |
| 全篇 🔴 Blocker 但实际只是 nit | 用分级标记 |
| "重写整个模块" | 给具体可执行的修改建议 |
| 长时间不响应 SLA | 至少先 `Request changes` 占位 + 留具体待办 |
| Reviewer 没 owner 资格就 approve 高敏模块 | 高敏模块必须有 owner 之一 |
| 把 PR 当聊天 | 评论针对代码，行号具体，可被解决 |

---

## 附录：常见 Blocker 速查

| 看到 | 立刻打 🔴 |
|------|-----------|
| `except: pass` | 异常吞掉 |
| `os.system(f"...")` | 命令注入 |
| `verify_*` 函数返回 True 时无 `hmac.compare_digest` | 时序攻击 |
| `pickle.loads(user_input)` | 反序列化漏洞 |
| `yaml.load(s)`（不是 safe_load） | YAML 反序列化漏洞 |
| `requests.get(user_url)` 在 server 端 | SSRF |
| `open(f"/certs/{filename}")` 未校验 `..` | 路径穿越 |
| `eval(user_input)` / `exec(...)` | 代码执行 |
| `subprocess.run(f"...{x}...")` | 命令注入 |
| `print(secret)` / `logger.info(f"token={token}")` | 密钥泄露 |
| `assert is_admin` 用于鉴权 | assert 可被禁用 |
| 修改 `SAFETY_RULES.md` 弱化条款 | 合规 blocker |

---

**用法**：review 前打印 / 拉到侧栏 / IDE TODO panel，每条勾掉。