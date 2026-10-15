"""Transport-neutral knowledge client errors."""

from __future__ import annotations


class KnowledgeClientError(Exception):
    """Base class for knowledge integration failures."""


class KnowledgeServiceError(KnowledgeClientError):
    """The knowledge service returned an explicit business failure."""

    def __init__(self, status_code: int, code: int, message: str, trace_id: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.trace_id = trace_id


class KnowledgeUnavailableError(KnowledgeClientError):
    """The knowledge service could not be reached."""


class KnowledgeTimeoutError(KnowledgeClientError):
    """The knowledge service did not respond before the configured timeout."""


class KnowledgeProtocolError(KnowledgeClientError):
    """The knowledge service response did not match the internal contract."""
