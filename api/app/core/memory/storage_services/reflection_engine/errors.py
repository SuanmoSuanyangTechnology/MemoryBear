"""Layer2 反思结构化失败契约。

反思流水线只向重试和可选扩展层暴露受控错误码，禁止把供应商响应、Prompt
或原始异常文本写入结构化失败状态。
"""

from enum import StrEnum


class ReflectionFailureReason(StrEnum):
    """需要结构化处理的反思模型错误。"""

    REFLECTION_MODEL_UNAVAILABLE = "REFLECTION_MODEL_UNAVAILABLE"
    MODEL_CALL_FAILED = "MODEL_CALL_FAILED"
    RESULT_PARSE_FAILED = "RESULT_PARSE_FAILED"


class ReflectionModelType(StrEnum):
    """模型类型维度，与失败阶段（reason_code）正交。"""

    LLM = "llm"
    EMBEDDING = "embedding"
    UNKNOWN = "unknown"


class ReflectionBusinessError(RuntimeError):
    """不携带供应商原始异常文本的受控业务异常。"""

    def __init__(
        self,
        reason_code: ReflectionFailureReason,
        failed_operation: str,
        model_type: ReflectionModelType | str = ReflectionModelType.UNKNOWN,
    ) -> None:
        self.reason_code = ReflectionFailureReason(reason_code)
        self.failed_operation = failed_operation
        self.model_type = ReflectionModelType(model_type)
        super().__init__(f"{self.reason_code.value}:{failed_operation}")


class ReflectionRetriesExhausted(RuntimeError):
    """受控模型失败达到重试阈值后抛出。"""

    def __init__(
        self,
        reason_code: ReflectionFailureReason,
    ) -> None:
        self.reason_code = ReflectionFailureReason(reason_code)
        super().__init__(self.reason_code.value)
