# DDoS Attack Platform - 教学大纲与实验手册

> 适用对象: 网络安全团队内部红蓝对抗教学、新人入职培训、防御能力评估

---

## 📚 课程体系

### 模块 1: 基础认知 (2 课时)
| 课时 | 内容 | 实验场景 |
|------|------|----------|
| 1 | DDoS 攻击分类、OSI 七层对应、常见协议特征 | 环境部署、平台架构讲解 |
| 2 | 攻击原理: 容量耗尽、状态耗尽、应用耗尽 | 观察预设场景流量特征 |

### 模块 2: 应用层攻击与防御 (4 课时)
| 课时 | 攻击类型 | 核心知识点 | 实验场景 | 防御验证点 |
|------|----------|------------|----------|------------|
| 3 | HTTP Flood (CC) | 并发连接、请求频率、资源消耗 | `cc_attack` | 限流算法、IP信誉、WAF规则、验证码 |
| 4 | Slowloris | 低带宽、连接池耗尽、Header 慢发 | `slowloris` | 超时配置、连接数限制、反向代理缓冲 |
| 5 | HTTPS Flood | TLS 握手开销、证书验证、CPU 耗尽 | `cc_attack` (HTTPS) | SSL 卸载、会话复用、硬件加速 |
| 6 | API 滥用 | 业务逻辑漏洞、参数遍历、爬虫 | 自定义场景 | 签名验证、频控、风控模型 |

### 模块 3: 传输/网络层攻击与防御 (4 课时)
| 课时 | 攻击类型 | 核心知识点 | 实验场景 | 防御验证点 |
|------|----------|------------|----------|------------|
| 7 | SYN Flood | 三次握手、半连接队列、SYN Cookie | `syn_flood` | SYN Cookie、队列扩容、防火墙状态检测 |
| 8 | UDP Flood | 无状态、反射放大、源端口随机 | `udp_flood` | UDP 限速、端口隔离、BGP FlowSpec |
| 9 | UDP 反射放大 | NTP/DNS/Memcached/SSDP 放大倍数 | `udp_reflection` | 反射源过滤、响应包限速、Anycast 清洗 |
| 10 | 混合波攻击 | 多层叠加、防御联动、资源争抢 | `mixed_wave` | 多层联动、流量清洗编排、应急响应 |

### 模块 4: 进阶实战 (4 课时)
| 课时 | 主题 | 内容 |
|------|------|------|
| 11 | 渐进式压测与容量规划 | `ramp_up` 场景、性能拐点识别、扩容阈值 |
| 12 | 红蓝对抗实战 | 红方自由攻击、蓝方实时调优、评分规则 |
| 13 | 攻击溯源与取证 | 流量镜像分析、攻击指纹提取、证据链固定 |
| 14 | 防御体系评估报告 | 编写评估报告、整改建议、复测验证 |

---

## 🧪 实验场景详细步骤

### 实验 1: CC 攻击基础防御
**目标**: 验证应用层限流、WAF、IP 信誉防御效果

**环境准备**:
```
Target: 10.100.10.10:80 (部署被测 WAF/应用)
Attacker: attacker-http-01 (10.100.1.20)
Controller: 10.100.1.10
```

**步骤**:
1. 启动场景: `POST /api/v1/scenarios/cc_attack/run` (默认 2000 RPS, 60s)
2. 观察 Grafana: 请求成功率、延迟 P99、WAF 拦截率
3. 调整攻击强度: `PATCH /api/v1/attacks/{id} {"rps": 5000}`
4. 观察防御熔断点、误封正常用户情况
5. 触发熔断: `POST /api/v1/emergency_stop`
6. 记录数据: 填写实验记录表

**实验记录表**:
| 指标 | 基线 | 2000 RPS | 5000 RPS | 10000 RPS | 熔断后恢复 |
|------|------|----------|----------|-----------|------------|
| 正常请求成功率 | | | | | |
| 平均延迟 (ms) | | | | | |
| P99 延迟 (ms) | | | | | |
| WAF 拦截率 | | | | | |
| 误封正常 IP 数 | | | | | |
| CPU/内存使用率 | | | | | |

---

### 实验 2: SYN Flood 与 SYN Cookie
**目标**: 验证内核 TCP 栈防御、防火墙状态检测

**步骤**:
1. 目标机开启/关闭 `net.ipv4.tcp_syncookies=1` 对比
2. 启动 `syn_flood` 场景 (10000 pps, 60s)
3. 观察: 半连接数 (`ss -s`)、新建连接成功率
4. 测试防火墙 `connlimit`、`hashlimit` 效果
5. 记录不同配置下的防御效果

---

### 实验 3: Slowloris 连接耗尽
**目标**: 验证 Web 服务器/反向代理连接超时配置

**步骤**:
1. 目标: Nginx/Apache 默认配置 vs 硬化配置
2. 启动 `slowloris` (300 并发, 120s)
3. 观察: 活跃连接数、Worker 进程数、内存增长
4. 调整 `client_header_timeout`、`client_body_timeout`、连接数限制
5. 验证反向代理 `proxy_read_timeout`、`proxy_send_timeout` 效果

---

### 实验 4: UDP 反射放大
**目标**: 理解放大攻击原理、验证边界过滤

**前置**: 部署反射器 (NTP/DNS/Memcached) 于 `10.100.200.0/24`

**步骤**:
1. 配置场景 `reflector_list` 指向反射器 IP
2. 启动 `udp_reflection` (5000 pps)
3. 在目标侧抓包分析: 放大倍数、源端口分布
4. 验证边界防火墙: `iptables -A INPUT -p udp --dport 123 -m limit --limit 100/s -j ACCEPT`
5. 测试 BGP FlowSpec 下发效果 (如有设备)

---

### 实验 5: 红蓝对抗评分赛
**规则**:
- 时长: 30 分钟
- 红方: 3 人，可使用所有攻击类型，总带宽 ≤ 500 Mbps
- 蓝方: 3 人，实时调整防御策略，不可重启核心业务
- 业务 SLA: 可用性 ≥ 99.9%，P99 延迟 ≤ 500ms

**评分**:
| 维度 | 权重 | 计分规则 |
|------|------|----------|
| 业务可用性 | 40% | 每降 0.1% 扣 2 分 |
| 平均延迟 | 20% | 超基线 2 倍扣 5 分 |
| 防御响应时间 | 20% | 从攻击开始到生效拦截 |
| 误封率 | 10% | 误封 1 个正常 IP 扣 3 分 |
| 创新战术 | 10% | 新规则/新策略加分 |

---

## 📊 评估报告模板

### DDoS 防御能力评估报告

**基本信息**:
- 评估日期: 
- 评估环境: 
- 参与人员: 红方 / 蓝方
- 被测系统: 

**测试场景汇总**:
| 场景 | 攻击峰值 | 持续时间 | 业务影响 | 防御生效时间 | 拦截率 | 备注 |
|------|----------|----------|----------|--------------|--------|------|

**发现问题**:
| 编号 | 问题描述 | 严重级 | 影响范围 | 根因分析 | 整改建议 | 负责人 | 完成时间 |
|------|----------|--------|----------|----------|----------|--------|----------|

**优化建议**:
1. 架构层面: 
2. 配置层面: 
3. 流程层面: 
4. 工具层面: 

**复测计划**:
- 复测时间: 
- 复测场景: 
- 验收标准: 

---

## 🎯 进阶练习题

### 练习 1: 自定义攻击场景编写
编写 YAML 场景实现: "先 SYN Flood 30s 消耗连接表，再 HTTP Flood 60s 穿透 WAF，最后 Slowloris 维持 60s 耗尽连接池"

### 练习 2: 攻击指纹识别
给定 5 个 PCAP 文件，识别攻击类型、工具特征、伪造程度

### 练习 3: 防御规则调优
给定某 WAF 误封正常用户案例，分析规则过严原因，编写白名单/调整阈值

### 练习 4: 应急响应演练
模拟: "02:00 收到监控告警，业务延迟飙升。请在 10 分钟内完成: 识别攻击类型 → 启用紧急策略 → 确认业务恢复 → 留存取证证据"

---

## 📖 参考资料

### 标准与规范
- RFC 4987: TCP SYN Flooding Attacks and Common Mitigations
- RFC 7413: TCP Fast Open (TFO) 安全考量
- NIST SP 800-61: Computer Security Incident Handling Guide
- GB/T 39786-2021: 网络安全等级保护测评要求

### 工具与平台
- 本平台: DDoS Attack Platform v1.0
- 流量分析: Wireshark, Zeek, Suricata
- 压测对比: Locust, k6, wrk, hey
- 监控: Prometheus + Grafana, ELK Stack

### 学习资源
- Cloudflare DDoS Protection 白皮书
- Akamai State of the Internet Security Report
- DDoS 攻击与防御技术演进 (清华大学出版社)
- 网络空间安全攻防演练指南 (工信部)

---

## 📝 附录: 常用命令速查

```bash
# Controller 常用 API
curl -k -H "Authorization: Bearer $TOKEN" https://10.100.1.10:8443/api/v1/nodes
curl -k -H "Authorization: Bearer $TOKEN" -X POST https://10.100.1.10:8443/api/v1/scenarios/cc_attack/run
curl -k -H "Authorization: Bearer $TOKEN" -X POST https://10.100.1.10:8443/api/v1/emergency_stop -d '{"reason":"training end","issued_by":"instructor"}'

# WebSocket 实时指标
wscat -c "wss://10.100.1.10:8443/ws/metrics?token=$TOKEN"

# 节点健康检查
curl http://10.100.1.20:8080/health
curl http://10.100.1.20:8080/metrics

# 证书生成
cd deploy && ./generate_certs.sh

# 部署
cd deploy && ./install.sh --role controller
cd deploy && ./install.sh --role attacker-http --host 10.100.1.20
cd deploy && ./install.sh --role attacker-raw --host 10.100.1.21

# 日志查看
docker-compose -f controller/docker-compose.yml logs -f controller
docker-compose -f attacker/docker-compose.yml logs -f attacker-http
tail -f /var/log/ddos-audit/audit.jsonl | jq .
```

---

**文档版本**: v1.1  
**适用平台版本**: DDoS Attack Platform v1.1  
**更新日期**: 2024-12-19  
**维护人**: 网络安全红队