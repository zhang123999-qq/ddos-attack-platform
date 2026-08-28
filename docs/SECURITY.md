# Security Policy

> **本平台为内部红方攻击演练工具, 严禁外传。**  
> **本文件描述漏洞披露流程和支持的版本范围。**

---

## 支持的版本

| 版本 | 支持状态 | EOL |
|------|---------|-----|
| v1.4.1-hotfix6 (master) | ✅ 积极支持 | TBD |
| v1.4.1-hotfix1~5 | ⚠️ 安全修复 backport | 2025-12-31 |
| v1.4.0 | ⚠️ 关键安全修复 | 2025-12-31 |
| v1.3.4 | 🟡 仅关键 CVE | 2025-09-30 |
| v1.3.3 及更早 | ❌ EOL | 已 EOL |

> 内部工具, 升级由 ddos-controller update 一键完成, 强烈建议保持最新。

---

## 漏洞披露流程

### 1. 报告渠道

**仅接受**以下任一渠道提交漏洞报告:

| 渠道 | 联系方式 | 优先级 |
|------|---------|--------|
| **内部 GitLab Issues** | (填入内网地址) | 🟡 标准 |
| **邮件** | `security@<your-company>.internal` (PGP 加密) | 🟢 加密 |
| **企业微信 / 钉钉** | 找 "安全应急响应" 群 → 私聊 oncall | 🟢 加密 |
| **紧急热线** | (填入内网电话) | 🔴 紧急 |

> **❌ 不接受** GitHub Issues / 公开 Issue Tracker / 社交媒体 / 邮件列表
> (因本平台代码不公开, 公开渠道易导致敏感信息外泄)

### 2. 报告应包含

```
1. 漏洞标题 (一句话)
2. 严重度自评 (Critical / High / Medium / Low)
3. 复现步骤 (PoC, 命令, 截图)
4. 影响范围 (哪些版本, 哪些配置, 哪些环境)
5. 可能的修复方向 (可选)
6. 您的联系方式 (期望 TLP 等级, 期望 24h 内确认接收)
```

### 3. 响应时间 (SLA)

| 严重度 | 首次确认 | 临时缓解 | 修复发布 |
|--------|---------|---------|----------|
| **Critical** | 4 小时内 | 24 小时内 | 72 小时内 |
| **High** | 1 工作日内 | 1 周内 | 2 周内 |
| **Medium** | 3 工作日内 | 下个 minor | 1 月内 |
| **Low** | 1 周内 | 评估 | 下次 release |

### 4. 协调披露 (Coordinated Disclosure)

我们遵循 [CERT/CC 90 天披露窗口](https://vuls.cert.org/confluence/display/CVD/Coordinated+Vulnerability+Disclosure) 原则:

- 接收 → 确认 (T+4h / 1d / 3d / 1w)
- 调查 + 修复 (T+72h / 2w / 1m / next)
- 内部测试 (T+5d / 3w / 6w / next)
- 联合发布 (T+90d 默认, 可协商)

> Critical 漏洞可申请加速披露 (T+30d)

### 5. 致谢

报告者可在 [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md) 留名 (匿名可选)。

---

## 安全机制 (v1.4.1-hotfix6)

### 1. 通信加密 (mTLS+HMAC)

| 链路 | 加密 | 认证 |
|------|------|------|
| Admin → Controller | HTTPS (自签/TLS 1.2+) | HMAC-SHA256 (Bearer Token) |
| Node → Controller | HTTPS (mTLS by node cert) | HMAC X-Node-Token |
| Controller → Node | HTTPS (mTLS by controller cert) **or** HTTP (opt-in) | HMAC X-Node-Token |

> **状态**: Controller→Node 在 v1.4.1-hotfix6 临时回退到 HTTP (REG-2/3 因 enroll 端点未签发 node-cert.pem)。
> v1.5.0 完整修复后将恢复 HTTPS+mTLS。详见 [DEEP_EVALUATION_v3.md §3](DEEP_EVALUATION_v3.md#3-技术债务-v3-状态复核)。

### 2. 启动校验

- `SHARED_SECRET` ≥ 32 字符 (REQUIRE_SHARED_SECRET=true 时强制)
- 弱密钥黑名单 (`changeme*`, `insecure-default*`)
- CA 证书 2 年有效期
- Node 证书 1 年有效期
- TLS 1.2+ 强制, ECDHE+AESGCM 优先

### 3. 进程隔离

- systemd hardening: `ProtectSystem=strict`, `NoNewPrivileges`, `CapabilityBoundingSet`
- 专用 `ddos` 用户 (uid 999, nologin, gid 986)
- config.env 权限 600 (chown ddos:ddos)
- systemd unit 权限 640

### 4. 审计

- structlog JSON 输出
- 500 条内存环形缓冲 (会话级)
- 可选 JSONL 落盘 (AUDIT_FILE_ENABLED=true, 100MB×10 rotation)
- WebSocket 实时推送 (5 频道: nodes/attacks/metrics/alerts/system)

### 5. 限流与熔断

- 全局限流 (TokenBucket)
- 节点级配额 (key=(attack_id, node_id))
- Worker 令牌桶
- 3 阶段紧急熔断: admin / 全局配额 / 节点离线 >50%

### 6. CI 安全门禁 (本版本状态)

| 检查 | 状态 | 备注 |
|------|------|------|
| 单元测试 | ✅ | 72/72 PASS |
| SAST (bandit) | ❌ | NEW-2 计划 v1.4.1.1 |
| SCA (pip-audit) | ❌ | NEW-2 计划 v1.4.1.1 |
| 容器扫描 (Trivy) | ❌ | NEW-2 计划 v1.4.1.1 |
| SBOM (cyclonedx) | ❌ | NEW-2 计划 v1.4.1.1 |
| 镜像签名 (cosign) | ❌ | NEW-2 计划 v1.4.1.1 |
| 依赖审查 (dependabot) | ❌ | 待评估 |
| secret 扫描 (gitleaks) | ❌ | 待评估 |

---

## 已知安全问题 (Tracked in DEEP_EVALUATION_v3)

### Medium (2 项, v1.5.0 重点)

| ID | 描述 | 影响 | 缓解 |
|----|------|------|------|
| S-NEW-1 | Node 端 mTLS 不强制 (`verify_node_token` 不查 cert) | 中间人可伪装 Node (需 SHARED_SECRET) | HTTPS + X-Node-Token 双因素 |
| S-NEW-2 | `emergency_stop` 无双人确认 | 误触/内部恶意可瞬间熔断全网 | 操作审计 + 短时间窗回滚 |

### Low (24 项, 见 v3 报告)

包含:
- WS token 在 URL (TD-7)
- Audit queue full 静默丢 (TD-8)
- Node 不验证 Controller issuer (S-NEW-6)
- Admin API 无限流
- systemd 缺 OOM 防护
- 等等

---

## 密钥与证书管理

### SHARED_SECRET

- **生成**: `openssl rand -hex 32` (64 hex 字符)
- **存储**: `/etc/ddos-controller/config.env` (chmod 600, chown ddos:ddos)
- **轮换**: 建议 90 天, 全节点同步更新
- **轮换流程**:
  1. 在新 SHARED_SECRET 准备就绪后, 先更新 controller
  2. 逐节点 `sudo ddos-node restart` (节点会 re-enroll 拿新 secret)
  3. 验证所有节点 health=healthy
  4. 删除旧版本备份

### CA 证书

- **位置**: `/opt/ddos-attack-platform/controller/certs/ca-cert.pem`
- **有效期**: 2 年 (DAYS_VALID_CA 可调)
- **轮换**: 滚动 (老 CA + 新 CA 并存, 节点证书双签后切换)
- **应急撤销**: 自签场景下直接删除节点证书文件 + 重新签发

### 节点证书

- **位置**: `/opt/ddos-attack-platform/attacker/certs/node-{cert,key}.pem`
- **有效期**: 1 年 (DAYS_VALID_NODE 可调)
- **轮换**: 重新跑 `node-install.sh` (会重新 enroll 拿新证书)

---

## 事件响应

### 怀疑被入侵时

1. **立即隔离**:
   ```bash
   sudo systemctl stop ddos-controller ddos-attacker
   ```
2. **保全证据** (不要重启):
   ```bash
   sudo cp -a /var/log/journal /tmp/journal-$(date +%s)
   sudo cp -a /etc/ddos-controller /etc/ddos-attacker /tmp/
   sudo cp -a /opt/ddos-attack-platform /tmp/
   ```
3. **通知安全组**: 见上方报告渠道 (紧急热线)
4. **审计日志**:
   ```bash
   journalctl -u ddos-controller -u ddos-attacker --since "1 month ago" > /tmp/audit.txt
   ```
5. **等待指示**: 不要自行清理或重装

### 私钥泄露

1. **立即**:
   ```bash
   sudo ddos-controller uninstall
   sudo ddos-node uninstall
   sudo userdel -r ddos
   ```
2. **生成新 SHARED_SECRET**: `openssl rand -hex 32`
3. **重新签发所有 CA/节点证书**
4. **逐节点重新部署**
5. **更新文档记录事件 + 时间 + 影响范围**

---

## 最佳实践

### 部署

1. ✅ 始终用 `controller-install.sh` (不要手动 copy 二进制)
2. ✅ SHARED_SECRET 至少 32 字符随机
3. ✅ REQUIRE_SHARED_SECRET=true (拒绝弱密钥)
4. ✅ AUDIT_FILE_ENABLED=true (满足 ≥90 天留存)
5. ✅ 限制 controller 管理端口 (8443) 仅管理员网段可达
6. ✅ 定期备份 /opt/ddos-attack-platform + /etc/ddos-{controller,attacker}
7. ✅ 配置监控 (Prometheus / Grafana) 跟踪异常

### 运行

1. ✅ 严格授权流程 (书面授权书 + 红线条款)
2. ✅ 演练前确认紧急熔断按钮可达
3. ✅ 演练后审计日志归档到 SIEM
4. ✅ 不要在公网或非授权网段运行
5. ✅ 流量镜像优先, 直接攻击次之

### 升级

1. ✅ 升级前备份 config.env
2. ✅ 升级后验证: `/health` 返回新版本, 节点重连成功
3. ✅ 紧急熔断功能测试 (在测试网段)
4. ✅ 关注 [CHANGELOG.md](CHANGELOG.md) 了解 breaking changes

---

## 法律

> 本平台开发者、维护者、贡献者不对非授权使用承担任何法律责任。
> 详见 [README.md §免责声明](../README.md#-法律免责声明) 和 [SAFETY_RULES.md](SAFETY_RULES.md)。
