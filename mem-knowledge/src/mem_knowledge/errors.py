"""Service-local errors independent of API BizCode."""

from __future__ import annotations


class KnowledgeError(Exception):
    """Stable internal error with HTTP and retry semantics."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int,
        retryable: bool,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
