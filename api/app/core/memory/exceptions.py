"""记忆萃取主链路的稳定业务异常定义。"""
from __future__ import annotations

from enum import Enum
from typing import Optional


class MemoryExtractionErrorCode(str, Enum):
    MODEL_CALL_FAILED = "MODEL_CALL_FAILED"
    STRUCTURED_RESULT_PARSE_FAILED = "STRUCTURED_RESULT_PARSE_FAILED"


class MemoryExtractionStage(str, Enum):
    MODEL_CALL = "model_call"
    STRUCTURED_RESULT_PARSE = "structured_result_parse"


class MemoryExtractionImpact(str, Enum):
    MEMORY_NOT_RELIABLY_FORMED = "memory_not_reliably_formed"
    MEMORY_VECTOR_INCOMPLETE = "memory_vector_incomplete"


class MemoryModelType(str, Enum):
    """通知中允许展示的模型类别，不包含具体模型配置。"""

    LLM = "llm"
    EMBEDDING = "embedding"
    RERANK = "rerank"


class MemoryExtractionBusinessError(Exception):
    """表示应在 Normal Write 失败或结果降级边界上报的稳定业务异常。"""

    def __init__(
        self,
        *,
        code: MemoryExtractionErrorCode,
        stage: MemoryExtractionStage,
        impact: MemoryExtractionImpact,
        retryable: bool,
        model_type: Optional[MemoryModelType] = None,
        cause: Optional[Exception] = None,
    ) -> None:
        self.code = code.value
        self.stage = stage.value
        self.impact = impact.value
        self.retryable = retryable
        self.model_type = model_type.value if model_type is not None else None
        self.cause = cause
        super().__init__(f"{self.code} at {self.stage}")

    @classmethod
    def model_call_failed(
        cls,
        cause: Exception,
        model_type: MemoryModelType = MemoryModelType.LLM,
    ) -> "MemoryExtractionBusinessError":
        return cls(
            code=MemoryExtractionErrorCode.MODEL_CALL_FAILED,
            stage=MemoryExtractionStage.MODEL_CALL,
            impact=MemoryExtractionImpact.MEMORY_NOT_RELIABLY_FORMED,
            retryable=True,
            model_type=model_type,
            cause=cause,
        )

    @classmethod
    def structured_result_parse_failed(
        cls, cause: Exception
    ) -> "MemoryExtractionBusinessError":
        return cls(
            code=MemoryExtractionErrorCode.STRUCTURED_RESULT_PARSE_FAILED,
            stage=MemoryExtractionStage.STRUCTURED_RESULT_PARSE,
            impact=MemoryExtractionImpact.MEMORY_NOT_RELIABLY_FORMED,
            retryable=True,
            model_type=MemoryModelType.LLM,
            cause=cause,
        )

    @classmethod
    def embedding_generation_failed(
        cls, cause: Exception
    ) -> "MemoryExtractionBusinessError":
        return cls(
            code=MemoryExtractionErrorCode.MODEL_CALL_FAILED,
            stage=MemoryExtractionStage.MODEL_CALL,
            impact=MemoryExtractionImpact.MEMORY_VECTOR_INCOMPLETE,
            retryable=True,
            model_type=MemoryModelType.EMBEDDING,
            cause=cause,
        )
