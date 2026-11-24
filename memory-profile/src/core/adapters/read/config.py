"""PostgreSQL adapters for the memory-read core's identity and configuration ports.

Three legacy read paths land here, and their behavior is pinned to the host:

* :class:`PostgresEndUserPort` — the merge-aware "is this end user addressable
  from this workspace?" check that guarded every read endpoint.
* :class:`PostgresReadConfigPort` — the workspace's active memory configuration
  plus the effective model slots, resolved exactly like the host:
  ``workspaces.memory_config`` → a default-config workspace follows the singleton
  ``WorkspaceDefaultModelPreset`` → each configured slot must be a tenant-visible,
  active ``model_configs`` row.
* the re-embedding degrade check (``should_degrade_semantic``), delegated to
  :class:`src.repositories.reembed.ReembedReadRepository`.

Model-slot failures keep the host's family labels so the streaming face can render
its ``{model}模型不可用`` message unchanged: ``embedding`` and ``llm`` keep their
own names, ``rerank`` stays ``rerank``, and the vision/audio/video slots report
``llm`` — the host validates them as the LLM family too
(``_MODEL_VALIDATION_SPECS``).

Adapters may import ``src.infrastructure`` / ``src.models`` / ``src.repositories``;
they never import ``src.modules`` or the API package.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any
from uuid import UUID

from src.core.domain.errors import ModelUnavailableError, ReadConfigUnavailableError
from src.core.domain.models import EndUserRef, ReadConfig
from src.repositories.end_user import EndUserRepository
from src.repositories.memory_config import MemoryConfigReadRepository
from src.repositories.model_config import ModelConfigRepository
from src.repositories.reembed import ReembedReadRepository
from src.repositories.workspace import WorkspaceRepository

if TYPE_CHECKING:  # pragma: no cover - type-only import
    from src.runtime import ProfileRuntime

logger = logging.getLogger(__name__)

#: The API's default when ``workspaces.storage_type`` is NULL (legacy rows).
DEFAULT_STORAGE_TYPE = "neo4j"

#: Host ``_MODEL_VALIDATION_SPECS``: slot → (error label, required).
#:
#: The slot names the workspace/preset column and the ``ReadConfig`` field prefix;
#: the label is the model family the host reports in its error, so vision/audio/
#: video say ``llm`` while embedding, llm and rerank keep their own name.
_MODEL_SLOTS: tuple[tuple[str, str, bool], ...] = (
    ("embedding", "embedding", True),
    ("llm", "llm", True),
    ("rerank", "rerank", False),
    ("vision", "llm", False),
    ("audio", "llm", False),
    ("video", "llm", False),
)


def _parse_uuid(value: Any) -> UUID | None:
    """Parse a stored id without trusting its type.

    The workspace and preset model columns are ``String`` while ``ReadConfig``
    carries ``UUID``; legacy rows may hold any historical spelling, so a value
    that does not parse is reported as unusable rather than crashing the read.
    """

    if isinstance(value, UUID):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return UUID(value.strip())
        except ValueError:
            return None
    return None


def _as_text(value: Any) -> str:
    """Render a stored model id for a payload without validating it."""

    if value is None:
        return ""
    text = str(value).strip()
    return "" if text in {"", "None"} else text


def _effective_model_ids(source: Mapping[str, Any]) -> dict[str, Any]:
    """The model ids a workspace should resolve at runtime.

    Port of the host's ``_effective_workspace_models``: a default-config
    workspace's own columns are only a copy of the singleton preset, so the
    preset is authoritative whenever it exists, and the workspace's own columns
    are used otherwise (non-default workspace, or preset row missing).
    """

    use_preset = bool(source["is_default_config"]) and source.get("preset_id") is not None
    return {
        slot: source[f"{slot}_model_config_id" if use_preset else f"workspace_{slot}"]
        for slot, _label, _required in _MODEL_SLOTS
    }


class PostgresEndUserPort:
    """Resolve a caller-supplied end user inside a workspace, routing merges."""

    def __init__(self, repository: EndUserRepository) -> None:
        self._repository = repository

    async def workspace_of(self, end_user_id: str) -> UUID | None:
        """The workspace the id belongs to (no merge routing), or ``None``."""

        parsed = _parse_uuid(end_user_id)
        if parsed is None:
            return None
        return await self._repository.workspace_of(parsed)

    async def write_target(self, end_user_id: str) -> dict[str, Any] | None:
        """The host's write target: active row, else newest merge target.

        Returns ``{"id", "workspace_id"}``; the caller applies the workspace
        check on the *resolved* user, exactly like ``write_server_async``.
        """

        parsed = _parse_uuid(end_user_id)
        if parsed is None:
            return None
        return await self._repository.write_target(parsed)

    async def resolve_in_workspace(self, end_user_id: str, workspace_id: UUID) -> EndUserRef | None:
        """Return the canonical (merge-routed) id, or ``None`` when unusable.

        A malformed id never reaches the database: the API failed the UUID parse
        before querying, and so does this adapter. The repository enforces the
        boundary itself — an active user of an active workspace, with the merge
        edge in the same workspace — so an id that is neither a user of this
        workspace nor a merge origin inside it resolves to ``None``.
        """

        parsed = _parse_uuid(end_user_id)
        if parsed is None:
            logger.info("memory-read: malformed end user id rejected before lookup")
            return None
        row = await self._repository.resolve_user_in_workspace(parsed, workspace_id)
        if row is None:
            return None
        return EndUserRef(end_user_id=str(row["id"]))


class PostgresReadConfigPort:
    """Load the memory configuration and the workspace facts a read needs."""

    def __init__(
        self,
        *,
        configs: MemoryConfigReadRepository,
        workspaces: WorkspaceRepository,
        models: ModelConfigRepository,
        reembeds: ReembedReadRepository,
    ) -> None:
        self._configs = configs
        self._workspaces = workspaces
        self._models = models
        self._reembeds = reembeds

    async def active_config_id(self, workspace_id: UUID) -> UUID | None:
        """The active workspace row's ``memory_config`` pointer."""

        return await self._configs.get_workspace_active_config_id(workspace_id)

    async def load_config(self, config_id: UUID) -> ReadConfig | None:
        """Load the configuration, resolving the six model slots like the host.

        ``None`` means "no usable configuration" — the row is gone, or its
        workspace is — and the use case turns that into a caller-visible config
        error. A configuration that exists but names a missing, inactive or
        unparseable model raises here instead, because no read can run without
        the slot (the host fails ``load_memory_config_async`` at the same point).
        """

        source = await self._configs.get_read_config_source(config_id)
        if source is None:
            return None
        slots = await self._resolve_slots(
            _effective_model_ids(source),
            tenant_id=source["tenant_id"],
        )
        return ReadConfig(
            config_id=source["config_id"],
            config_name=source["config_name"],
            workspace_id=source["workspace_id"],
            workspace_name=source["workspace_name"],
            tenant_id=source["tenant_id"],
            llm_model_id=slots["llm"][0],
            llm_model_name=slots["llm"][1],
            embedding_model_id=slots["embedding"][0],
            embedding_model_name=slots["embedding"][1],
            rerank_model_id=slots["rerank"][0],
            rerank_model_name=slots["rerank"][1],
            vision_model_id=slots["vision"][0],
            vision_model_name=slots["vision"][1],
            audio_model_id=slots["audio"][0],
            audio_model_name=slots["audio"][1],
            video_model_id=slots["video"][0],
            video_model_name=slots["video"][1],
        )

    async def workspace_storage_type(self, workspace_id: UUID) -> str | None:
        """The workspace's storage backend; a legacy NULL row reads as ``neo4j``.

        A missing workspace returns ``None`` ("unknown"), which the use case
        already treats as ``neo4j``; the endpoint's own user lookup reports such a
        workspace as a missing end user before this is ever asked.
        """

        row = await self._workspaces.workspace_row(workspace_id)
        if row is None:
            return None
        return row["storage_type"] or DEFAULT_STORAGE_TYPE

    async def write_model_ids(self, config_id: UUID) -> tuple[str, str, str]:
        """The ``(embedding, llm, tenant)`` facts a write unit carries.

        The monolith's write controller never loads the configuration: it hands
        the config id to the worker, which resolves (and validates) the model
        slots at execution time. This adapter therefore resolves the *effective*
        workspace/preset ids only — the same values the worker would read — and
        leaves a missing/unusable slot empty, so the unit fails where the
        monolith's task would fail instead of rejecting the request early.

        ``tenant`` is the workspace's tenant, because that is what the API's
        worker uses to resolve model-service credentials (not the caller's
        token tenant).
        """

        source = await self._configs.get_read_config_source(config_id)
        if source is None:
            return "", "", ""
        effective = _effective_model_ids(source)
        return (
            _as_text(effective.get("embedding")),
            _as_text(effective.get("llm")),
            _as_text(source.get("tenant_id")),
        )

    async def semantic_rebuild_pending(
        self,
        *,
        workspace_id: UUID,
        end_user_id: str,
        embedding_model_id: UUID,
    ) -> bool:
        """Whether stored vectors are still being rebuilt for the current model."""

        return await self._reembeds.semantic_rebuild_pending(
            workspace_id=workspace_id,
            end_user_id=end_user_id,
            embedding_model_id=embedding_model_id,
        )

    async def _resolve_slots(
        self,
        model_ids: Mapping[str, Any],
        *,
        tenant_id: UUID | None,
    ) -> dict[str, tuple[UUID | None, str | None]]:
        """Parse the slot ids, then validate the configured ones in one query.

        Port of the host's ``_validate_models_batch_async``:

        * an unconfigured optional slot resolves to ``(None, None)`` — the read
          degrades instead of failing;
        * an unconfigured *required* slot (embedding, llm) is a broken
          configuration;
        * a configured slot must resolve to a tenant-visible, active
          ``model_configs`` row whose name travels with the id (the community
          trace and the error messages both show it).

        The host distinguishes "belongs to another tenant" from "not found" in
        its message, but both are the same domain failure, so one lookup with the
        same tenant/public visibility rule covers both.
        """

        parsed: dict[str, UUID | None] = {}
        for slot, _label, required in _MODEL_SLOTS:
            raw = model_ids[slot]
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                if required:
                    raise ReadConfigUnavailableError(
                        f"memory configuration is missing the required {slot} model id",
                        reason="required_model_slot_missing",
                    )
                parsed[slot] = None
                continue
            model_id = _parse_uuid(raw)
            if model_id is None:
                # The host's ``_parse_model_id`` raises a configuration error for
                # an unparseable stored id, required slot or not.
                raise ReadConfigUnavailableError(
                    f"memory configuration has an invalid {slot} model id",
                    reason="invalid_model_id",
                )
            parsed[slot] = model_id

        found = await self._models.get_models(
            [model_id for model_id in parsed.values() if model_id is not None],
            tenant_id,
        )
        resolved: dict[str, tuple[UUID | None, str | None]] = {}
        for slot, label, _required in _MODEL_SLOTS:
            model_id = parsed[slot]
            if model_id is None:
                resolved[slot] = (None, None)
                continue
            row = found.get(model_id)
            if row is None or not row["is_active"]:
                raise ModelUnavailableError(label)
            resolved[slot] = (model_id, row["name"])
        return resolved


def build_end_user_port(runtime: ProfileRuntime) -> PostgresEndUserPort:
    """Composition entry point for the end-user port."""

    return PostgresEndUserPort(EndUserRepository(runtime.postgres))


def build_config_port(runtime: ProfileRuntime) -> PostgresReadConfigPort:
    """Composition entry point for the configuration port."""

    postgres = runtime.postgres
    return PostgresReadConfigPort(
        configs=MemoryConfigReadRepository(postgres),
        workspaces=WorkspaceRepository(postgres),
        models=ModelConfigRepository(postgres),
        reembeds=ReembedReadRepository(postgres),
    )


__all__ = [
    "DEFAULT_STORAGE_TYPE",
    "PostgresEndUserPort",
    "PostgresReadConfigPort",
    "build_config_port",
    "build_end_user_port",
]
