#!/bin/sh
# 靶机容器启动时生成测试资源 (官方 nginx 镜像自动执行 /docker-entrypoint.d/*.sh)
# 生成 10MB 测试文件供 /large 端点做带宽消耗演练
set -e

HTML_DIR="/usr/share/nginx/html"

if [ ! -f "${HTML_DIR}/large.bin" ]; then
    dd if=/dev/zero of="${HTML_DIR}/large.bin" bs=1M count=10 2>/dev/null || \
        dd if=/dev/zero of="${HTML_DIR}/large.bin" bs=1024 count=10240
    echo "generated ${HTML_DIR}/large.bin (10MB)"
fi

if [ ! -f "${HTML_DIR}/slow.html" ]; then
    printf '<html><body>slow response</body></html>\n' > "${HTML_DIR}/slow.html"
fi

exit 0
