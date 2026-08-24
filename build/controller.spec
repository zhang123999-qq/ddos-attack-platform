#!/usr/bin/env python3
# PyInstaller 打包规格文件 — Controller
# 用法: pyinstaller controller.spec

import os
import sys
import importlib.util

# 确保项目根和 controller/app 在 Python 路径中
block_cipher = None

# 收集所有依赖
hidden_imports = [
    'uvicorn',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',
    'fastapi',
    'fastapi.middleware',
    'fastapi.middleware.cors',
    'structlog',
    'structlog.processors',
    'structlog.dev',
    'pydantic',
    'pydantic.deprecated',
    'pydantic.deprecated.decorator',
    'pydantic.deprecated.copy_internals',
    'yaml',
    'jinja2',
    'jinja2.ext',
    'httpx',
    'httpx._transports',
    'httpx._transports.asgi',
    'httpx._transports.default',
    'aiohttp',
    'aiohttp.client',
    'aiohttp.connector',
    'sniffio',
    'anyio',
    'anyio._backends',
    'anyio._backends._asyncio',
    'h11',
    'h2',
    'h2.config',
    'h2.connection',
    'h2.events',
    'h2.exceptions',
    'h2.frame_buffer',
    'h2.settings',
    'h2.stream',
    'h2.utilities',
    'h2.windows',
    'hpack',
    'hyperframe',
    'cryptography',
    'cryptography.hazmat',
    'cryptography.hazmat.backends',
    'cryptography.hazmat.backends.openssl',
    'cryptography.hazmat.primitives',
    'cryptography.hazmat.primitives.hashes',
    'cryptography.hazmat.primitives.kdf',
    'cryptography.hazmat.primitives.serialization',
    'cryptography.hazmat.primitives.asymmetric',
    'cryptography.hazmat.primitives.ciphers',
    'OpenSSL',
    'prometheus_client',
    'watchfiles',
    'websockets',
    'uuid',
    'hashlib',
    'hmac',
]

# 运行时数据文件 — 路径基于 spec 所在目录动态解析 (PyInstaller 注入 SPECPATH),
# 与工作目录/检出深度无关
_ROOT = os.path.dirname(SPECPATH)

datas = [
    (os.path.join(_ROOT, 'scenarios'), 'scenarios'),  # 预设场景 YAML
]

# Controller 入口
a = Analysis(
    [os.path.join(_ROOT, 'controller', 'app', 'main.py')],
    pathex=[os.path.join(_ROOT, 'controller')],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ddos-controller',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=['vcruntime140.dll', 'libcrypto*', 'libssl*', 'libffi*', 'python3*.dll', 'lib-python*'],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)