FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖 (scapy 需要 raw socket 支持)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    openssl \
    iproute2 \
    net-tools \
    procps \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY attacker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY attacker/app/ ./app/

# 创建非 root 用户 (RAW 攻击需要特权，见 docker-compose)
RUN useradd -m -u 1000 appuser && \
    mkdir -p /certs /var/log/ddos-attacker && \
    chown -R appuser:appuser /app /certs /var/log/ddos-attacker

# 默认非 root，RAW 类型在 docker-compose 中用 privileged 覆盖
USER appuser

EXPOSE 8080

# M-3 修复: 容器级自愈探针
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8080/health', timeout=5)"

CMD ["python", "-m", "app.main"]