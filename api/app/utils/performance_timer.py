"""
性能监控工具模块

提供代码块执行时间统计功能，用于接口性能分析。
如需再次启用性能监控，只需在 controller 中导入 from app.utils.performance_timer import timer 并添加 with timer(...) 包裹需要监控的代码块即可
"""

import time
from contextlib import asynccontextmanager, contextmanager
from app.core.logging_config import get_api_logger

# 获取API专用日志器
api_logger = get_api_logger()

# 同步的上下文管理器，使用@contextmanager修饰
@contextmanager
def timer(label: str, user_count: int = 0):
    """上下文管理器：用于测量代码块执行时间

    Args:
        label: 统计标签，用于标识被测量的代码块
        user_count: 用户数，可选参数，用于记录处理的用户数量

    Usage:
        with timer("获取用户列表"):
            users = get_users()

        with timer("批量处理", user_count=len(user_ids)):
            process_users(user_ids)
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = (time.perf_counter() - start) * 1000  # 转换为毫秒
        extra_info = f", 用户数: {user_count}" if user_count > 0 else ""
        api_logger.info(f"[性能统计] {label}: {elapsed:.2f}ms{extra_info}")

# 异步的上下文管理器，使用@asynccontextmanager装饰
@asynccontextmanager
async def async_timer(label: str, user_count: int = 0):
    """异步上下文管理器：用于测量包含 await 的异步代码块执行时间

    Args:
        label: 统计标签，用于标识被测量的代码块
        user_count: 用户数，可选参数，用于记录处理的用户数量

    Usage:
        async with async_timer("获取用户列表"):
            users = await get_users()

        async with async_timer("批量处理", user_count=len(user_ids)):
            await process_users(user_ids)
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = (time.perf_counter() - start) * 1000  # 转换为毫秒
        extra_info = f", 用户数: {user_count}" if user_count > 0 else ""
        api_logger.info(f"[性能统计] {label}: {elapsed:.2f}ms{extra_info}")
