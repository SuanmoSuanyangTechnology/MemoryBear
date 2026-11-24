"""Domain errors of the memory-read core.

Errors are split in two families, mirroring the host:

* :class:`ReadError` — a caller-visible business failure. It carries a
  *business-code name* (resolved to the shared ``error_core.BizCode`` value by the
  transport layer, so this module stays free of the catalog package) and a
  machine-readable ``reason`` used in diagnostics.
* :class:`MemoryRetrievalBusinessError` — an internal diagnostic that marks a
  degraded retrieval branch. It never reaches the caller as-is; the retrieval
  pipeline aggregates it into the execution trace / alerting port.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ReadErrorCode(StrEnum):
    """Names of the shared business codes a read failure may use."""

    MEMORY_READ_FAILED = "MEMORY_READ_FAILED"
    INVALID_PARAMETER = "INVALID_PARAMETER"
    USER_NOT_FOUND = "USER_NOT_FOUND"
    END_USER_NOT_FOUND = "END_USER_NOT_FOUND"
    MEMORY_CONFIG_NOT_FOUND = "MEMORY_CONFIG_NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    NO_AVAILABLE_CHANNEL = "NO_AVAILABLE_CHANNEL"


class ReadError(Exception):
    """A business failure raised by the core and rendered by the transport layer.

    ``status_code=None`` means "derive it from the shared catalog" (the default
    for new endpoints); endpoints that keep the legacy HTTP 200 business-failure
    contract pin ``status_code=200`` explicitly.
    """

    code: ReadErrorCode = ReadErrorCode.MEMORY_READ_FAILED

    def __init__(
        self,
        message: str,
        *,
        code: ReadErrorCode | None = None,
        reason: str = "",
        detail: str = "",
        status_code: int | None = None,
        data: object = None,
    ) -> None:
        self.message = message
        self.code = code or type(self).code
        self.reason = reason
        self.detail = detail or reason
        self.status_code = status_code
        self.data = data
        super().__init__(message)

    def __str__(self) -> str:
        context = f", reason={self.reason}" if self.reason else ""
        return f"{self.code.value}: {self.message}{context}"


class ReadRequestError(ReadError):
    """The request itself is unusable (bad strategy, empty query, RAG+community)."""

    code = ReadErrorCode.INVALID_PARAMETER


class EndUserNotFoundError(ReadError):
    """The end user does not resolve inside the caller's workspace."""

    code = ReadErrorCode.USER_NOT_FOUND


class ReadConfigUnavailableError(ReadError):
    """The end user has no usable memory configuration."""

    code = ReadErrorCode.MEMORY_CONFIG_NOT_FOUND


class ModelUnavailableError(ReadError):
    """A required model slot is missing or inactive.

    The legacy streaming face renders this as ``{model_type}模型不可用``; the
    message is assembled by the caller so the wording stays with the transport
    contract, while ``model_type`` stays machine-readable here.
    """

    code = ReadErrorCode.MEMORY_READ_FAILED

    def __init__(self, model_type: str, *, reason: str = "model_unavailable") -> None:
        self.model_type = model_type
        super().__init__(
            f"{model_type} model unavailable",
            reason=reason,
        )


class ReadUnavailableError(ReadError):
    """The retrieval adapter is not wired yet (staged rollout) or is unreachable."""

    code = ReadErrorCode.SERVICE_UNAVAILABLE


# ────────────────────────────────────────────────────────────────
# Internal retrieval diagnostics
# ────────────────────────────────────────────────────────────────


class CommunityRetrievalError(RuntimeError):
    """Internal failure reason; the read pipeline supplies the public contract."""


class RetrievalErrorCode(StrEnum):
    MODEL_CALL_FAILED = "MODEL_CALL_FAILED"
    STRUCTURED_RESULT_PARSE_FAILED = "STRUCTURED_RESULT_PARSE_FAILED"


class RetrievalStage(StrEnum):
    QUERY_PROCESS = "query_process"
    VECTOR_SEARCH = "vector_search"
    KEYWORD_SEARCH = "keyword_search"
    RELATION_SEARCH = "relation_search"
    PERCEPTUAL_ANALYSIS = "perceptual_analysis"
    RERANK = "rerank"
    SUMMARY = "summary"


class RetrievalImpact(StrEnum):
    INCOMPLETE = "incomplete"
    ORDERING_DEGRADED = "ordering_degraded"
    UNAVAILABLE = "unavailable"


class RetrievalModelType(StrEnum):
    """Model families allowed in diagnostics (never a concrete model config)."""

    LLM = "llm"
    EMBEDDING = "embedding"
    RERANK = "rerank"


class MemoryRetrievalBusinessError(Exception):
    """A retrieval anomaly that degrades the result without carrying user input."""

    def __init__(
        self,
        *,
        code: RetrievalErrorCode,
        stage: RetrievalStage,
        impact: RetrievalImpact,
        model_type: RetrievalModelType,
        cause: BaseException | None = None,
    ) -> None:
        self.code = code.value
        self.stage = stage.value
        self.impact = impact.value
        self.model_type = model_type.value
        self.cause = cause
        super().__init__(f"{self.code} at {self.stage}")

    @classmethod
    def model_call_failed(
        cls,
        stage: RetrievalStage,
        cause: BaseException,
        *,
        model_type: RetrievalModelType,
        impact: RetrievalImpact = RetrievalImpact.INCOMPLETE,
    ) -> MemoryRetrievalBusinessError:
        return cls(
            code=RetrievalErrorCode.MODEL_CALL_FAILED,
            stage=stage,
            impact=impact,
            model_type=model_type,
            cause=cause,
        )

    @classmethod
    def structured_result_parse_failed(
        cls,
        stage: RetrievalStage,
        cause: BaseException,
        *,
        model_type: RetrievalModelType,
        impact: RetrievalImpact = RetrievalImpact.INCOMPLETE,
    ) -> MemoryRetrievalBusinessError:
        return cls(
            code=RetrievalErrorCode.STRUCTURED_RESULT_PARSE_FAILED,
            stage=stage,
            impact=impact,
            model_type=model_type,
            cause=cause,
        )

    def with_impact(self, impact: RetrievalImpact) -> MemoryRetrievalBusinessError:
        """Adjust coverage impact across a merge boundary without changing class."""

        return MemoryRetrievalBusinessError(
            code=RetrievalErrorCode(self.code),
            stage=RetrievalStage(self.stage),
            impact=impact,
            model_type=RetrievalModelType(self.model_type),
            cause=self.cause,
        )


class AnswerGenerationError(RuntimeError):
    """Final answer generation failed; the streaming face renders an error event."""

    def __init__(self, message: str, *, reason: str = "answer_failed") -> None:
        self.reason = reason
        self.message = message
        super().__init__(message)


def read_error_payload(error: ReadError) -> dict[str, Any]:
    """Diagnostic payload for logs (never user data)."""

    return {
        "code": error.code.value,
        "reason": error.reason,
        "detail": error.detail,
    }


__all__ = [
    "AnswerGenerationError",
    "CommunityRetrievalError",
    "EndUserNotFoundError",
    "MemoryRetrievalBusinessError",
    "ModelUnavailableError",
    "ReadConfigUnavailableError",
    "ReadError",
    "ReadErrorCode",
    "ReadRequestError",
    "ReadUnavailableError",
    "RetrievalErrorCode",
    "RetrievalImpact",
    "RetrievalModelType",
    "RetrievalStage",
    "read_error_payload",
]