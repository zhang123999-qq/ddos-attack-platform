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

# 复制应用代码
COPY app/ ./app/

# 创建非 root 用户
RUN useradd -m -u 1000 appuser && \
    mkdir -p /certs /var/log/ddos-audit && \
    chown -R appuser:appuser /app /certs /var/log/ddos-audit

USER appuser

EXPOSE 8443

CMD ["python", "-m", "app.main"]