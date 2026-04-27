"""
Memory Pipelines — 记忆模块流水线编排层

每条 Pipeline 定义一个完整的业务流程，按顺序编排多个 Engine 的调用。
Pipeline 不包含业务逻辑实现，只做步骤编排和数据传递。
"""


def __getattr__(name):
    """延迟导入，避免循环依赖"""
    if name in ("WritePipeline", "ExtractionResult", "WriteResult"):
        from app.core.memory.pipelines.write_pipeline import (
            ExtractionResult,
            WritePipeline,
            WriteResult,
        )

        _exports = {
            "WritePipeline": WritePipeline,
            "ExtractionResult": ExtractionResult,
            "WriteResult": WriteResult,
        }
        return _exports[name]
    if name in ("PilotWritePipeline", "PilotWriteResult"):
        from app.core.memory.pipelines.pilot_write_pipeline import (
            PilotWritePipeline,
            PilotWriteResult,
        )

        _exports = {
            "PilotWritePipeline": PilotWritePipeline,
            "PilotWriteResult": PilotWriteResult,
        }
        return _exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "WritePipeline",
    "ExtractionResult",
    "WriteResult",
    "PilotWritePipeline",
    "PilotWriteResult",
]
