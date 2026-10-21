"""Transport-neutral knowledge call contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class KnowledgeRetrievalSource(StrEnum):
    """Business source that initiated a knowledge retrieval."""

    GENERAL = "general"
    EXTERNAL_API = "ex_api"
    MANAGER_API = "in_api"
    AGENT = "agent"
    WORKFLOW = "workflow"
    DRAFT = "draft"
    SANDBOX = "sandbox"
    SHARED_CHAT = "shared_chat"


@dataclass(frozen=True)
class KnowledgePrincipal:
    """Minimal resource-authorization identity asserted by the API service."""

    actor_id: UUID
    actor_name: str | None
    tenant_id: UUID
    workspace_id: UUID


@dataclass(frozen=True)
class KnowledgeCallContext:
    """Identity, source, and trace metadata for one knowledge call."""

    principal: KnowledgePrincipal | None
    source: KnowledgeRetrievalSource
    trace_id: str


class KnowledgeContextError(ValueError):
    """The API could not construct a valid knowledge call context."""


class KnowledgeConfigurationError(ValueError):
    """The remote knowledge integration configuration is invalid."""
