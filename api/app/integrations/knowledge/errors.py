"""Transport-neutral knowledge client errors."""

from __future__ import annotations

from app.core.exceptions import BusinessException


class KnowledgeClientError(BusinessException):
    """Base class for knowledge integration failures."""

    default_code = 9341
    status_code = 503

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message, code=self.default_code if code is None else code)


class KnowledgeServiceError(KnowledgeClientError):
    """The knowledge service returned an explicit business failure."""

    def __init__(
        self,
        status_code: int,
        code: int,
        message: str,
        trace_id: str,
        *,
        diagnostic: str = "",
        headers: dict[str, str] | None = None,
    ):
        super().__init__(diagnostic or message, code=code)
        self.user_message = message
        self.diagnostic = diagnostic
        self.headers = dict(headers or {})
        self.status_code = status_code
        self.code = code
        self.trace_id = trace_id


class KnowledgeUnavailableError(KnowledgeClientError):
    """The knowledge service could not be reached."""


class KnowledgeTimeoutError(KnowledgeClientError):
    """The knowledge service did not respond before the configured timeout."""

    default_code = 9340


class KnowledgeProtocolError(KnowledgeClientError):
    """The knowledge service response did not match the internal contract."""

    default_code = 9342
    status_code = 500
