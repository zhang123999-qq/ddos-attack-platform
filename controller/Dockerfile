FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    openssl \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码 (context = 仓库根)
COPY controller/app/ ./app/

# 节点一键安装脚本随镜像分发 (GET /install.sh 端点读取)
COPY deploy/node-install.sh /app/deploy/node-install.sh

# 制品目录: 挂载或构建时放入 ddos-attacker-linux-*.tar.gz 供节点就近下载
RUN mkdir -p /app/artifacts

# 创建非 root 用户
RUN useradd -m -u 1000 appuser && \
    mkdir -p /certs /var/log/ddos-audit && \
    chown -R appuser:appuser /app /certs /var/log/ddos-audit

USER appuser

EXPOSE 8443

# M-3 修复: 容器级自愈探针 (TLS 自签兼容; 与 compose 健康检查同语义)
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python -c "import ssl,urllib.request as u,os; c=ssl.create_default_context(); c.check_hostname=False; c.verify_mode=ssl.CERT_NONE; u.urlopen('https://localhost:'+os.getenv('CONTROLLER_PORT','8443')+'/health', context=c, timeout=5)"

CMD ["python", "-m", "app.main"]