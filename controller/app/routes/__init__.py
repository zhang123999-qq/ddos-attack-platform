"""v1.5.0 新增: 路由模块聚合 (按职责拆分 main.py)

每个子模块负责一类端点, 通过 register_routes(app) 挂载到主应用。
"""
from fastapi import FastAPI

from . import attacks, enroll, install, internal, nodes, scenarios, system


def register_all_routes(app: FastAPI) -> None:
    """一次性注册所有路由模块 — main.py 在 create_app 后调用"""
    attacks.register(app)
    nodes.register(app)
    scenarios.register(app)
    install.register(app)
    enroll.register(app)
    system.register(app)
    internal.register(app)


__all__ = ["register_all_routes", "attacks", "nodes", "scenarios",
           "install", "enroll", "system", "internal"]
