"""Controller 项目 conftest: 把 controller/ 加入 sys.path, 让 tests/ 中可 import app.*

关键设计: 在 conftest 加载时立即把 controller/ 放到 sys.path 头部, 并把 attacker/
移出 sys.path (防止 controller 与 attacker 同台出现, 避免 'app' 包命名冲突)。
"""
from __future__ import annotations

import sys
from pathlib import Path

_CONTROLLER_DIR = str(Path(__file__).resolve().parent)
_ATTACKER_DIR = str(Path(__file__).resolve().parent.parent / "attacker")

# 把 controller/ 放到头部
if _CONTROLLER_DIR in sys.path:
    sys.path.remove(_CONTROLLER_DIR)
sys.path.insert(0, _CONTROLLER_DIR)

# 把 attacker/ 移走 (避免 controller 与 attacker 同台出现)
while _ATTACKER_DIR in sys.path:
    sys.path.remove(_ATTACKER_DIR)