# app/plugins/__init__.py
"""
插件系统 - 支持开源核心 + 闭源增值模块

使用方式：
1. 开源版（community）：基础功能
2. 商业版（enterprise）：加载 premium 包中的高级实现
"""
import os
from typing import Dict, Any, Optional
from app.core.logging_config import get_logger

logger = get_logger(__name__)

# 版本标识
EDITION = os.environ.get("EDITION", "community")
IS_ENTERPRISE = EDITION == "enterprise"

# 插件注册表
_plugins: Dict[str, Any] = {}

# 路由注册表（用于动态注册闭源模块的路由）
_routers: list = []


def is_enterprise() -> bool:
    """是否为商业版"""
    return IS_ENTERPRISE


def list_plugins() -> list:
    """列出所有已注册插件"""
    return list(_plugins.keys())


def register_plugin(name: str, instance: Any):
    """注册插件"""
    _plugins[name] = instance
    logger.info(f"插件已注册: {name}")


def get_plugin(name: str) -> Optional[Any]:
    """获取插件实例"""
    return _plugins.get(name)


def register_router(router, prefix: str = "", tags: list = None):
    """注册路由（供闭源模块使用）"""
    _routers.append({
        "router": router,
        "prefix": prefix,
        "tags": tags or []
    })
    logger.info(f"路由已注册: {prefix}")


def get_registered_routers() -> list:
    """获取所有注册的路由"""
    return _routers


def register_premium_routers(app):
    """
    注册 premium 模块的路由到 FastAPI app
    
    在商业版 main.py 中调用
    """
    for router_info in _routers:
        app.include_router(
            router_info["router"],
            prefix=f"/api{router_info['prefix']}",
            tags=router_info["tags"]
        )
        logger.info(f"Premium 路由已挂载: /api{router_info['prefix']}")
