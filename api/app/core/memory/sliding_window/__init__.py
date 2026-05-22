"""
Sliding Window Write — 滑动窗口延迟写入模块

将记忆写入方式从实时写入改为滑动窗口延迟写入：
每条 user 消息等待其后积累 ≥3 条 user 消息后，以 3 上 3 下的窗口上下文触发写入，
为知识萃取阶段提供更丰富的语义背景。
"""


def __getattr__(name):
    """延迟导入，避免循环依赖"""
    if name == "SlidingWindowScheduler":
        from app.core.memory.sliding_window.scheduler import SlidingWindowScheduler

        return SlidingWindowScheduler
    if name == "FlushTask":
        from app.core.memory.sliding_window.flush_task import FlushTask

        return FlushTask
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "SlidingWindowScheduler",
    "FlushTask",
]
