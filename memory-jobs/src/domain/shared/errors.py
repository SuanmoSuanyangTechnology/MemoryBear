"""Domain error hierarchy.

Application services translate these into the result payloads their callers
already consume; infrastructure failures are deliberately not modelled here so
they keep propagating to Celery's retry policy.
"""

from __future__ import annotations

from src.domain.shared.ids import EndUserId, WorkspaceId


class DomainError(Exception):
    """Base class for every rule violation the domain itself decides."""


class InvalidIdentifier(DomainError):
    """A caller supplied an identifier that cannot be a domain identity."""


class InvalidValue(DomainError):
    """A value object or domain service rejected the value it was given.

    Every domain rule violation uses this hierarchy, never ``ValueError``;
    parsing and caller-contract checks at the boundary may still raise
    ``ValueError``. ``field`` names the rejected part when the caller can offer
    one, so an application can report a content problem separately from an
    infrastructure failure.
    """

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.field = field


class WorkspaceNotEligible(DomainError):
    """The workspace cannot generate profile caches.

    ``reason`` is part of the cross-service contract: the producer runs the same
    predicate before publishing, so the three values must not drift. ``detail``
    carries diagnostics (the storage type) as a separate field; ``str(error)``
    stays equal to ``reason`` so a caller that logs or matches the message never
    picks up the diagnostic suffix.
    """

    def __init__(
        self,
        workspace_id: WorkspaceId,
        reason: str,
        detail: str | None = None,
    ) -> None:
        super().__init__(reason)
        self.workspace_id = workspace_id
        self.reason = reason
        self.detail = detail


class UserNotFound(DomainError):
    """The user is absent, disabled, merged away or outside the workspace."""

    def __init__(self, end_user_id: EndUserId) -> None:
        super().__init__(str(end_user_id))
        self.end_user_id = end_user_id


class ModelConfigurationError(DomainError):
    """No usable model configuration exists for the workspace."""
