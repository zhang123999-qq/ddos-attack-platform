#!/usr/bin/env python3
# PyInstaller 打包规格文件 — Attacker Node
# 用法: pyinstaller attacker.spec

block_cipher = None

hidden_imports = [
    'uvicorn',
    'uvicorn.loops', 'uvicorn.loops.auto',
    'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan', 'uvicorn.lifespan.on',
    'fastapi',
    'structlog', 'structlog.processors',
    'pydantic', 'pydantic.deprecated.decorator',
    'httpx', 'httpx._transports.default',
    'aiohttp', 'aiohttp.client', 'aiohttp.connector',
    'scapy', 'scapy.all', 'scapy.layers', 'scapy.layers.inet',
    'scapy.layers.inet6', 'scapy.layers.l2', 'scapy.sendrecv',
    'scapy.route', 'scapy.utils',
    'psutil',
    'cryptography', 'cryptography.hazmat.backends.openssl',
    'OpenSSL',
    'anyio', 'anyio._backends._asyncio',
    'platform',
    'socket',
    'ssl',
    'hmac',
    'hashlib',
]

# 排除纯 RAW 节点不需要的大型依赖
excludes = [
    'tkinter', 'tcl',
    'matplotlib', 'numpy', 'pandas',
    'PIL', 'Pillow',
    'IPython', 'jupyter',
]

a = Analysis(
    ['../../../attacker/app/main.py'],
    pathex=['../../../attacker'],
    binaries=[],
    datas=[],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
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
    name='ddos-attacker',
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