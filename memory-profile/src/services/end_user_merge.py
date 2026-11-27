"""End-user merge executed by memory-profile.

The API's ``EndUserService.merge_end_users`` split into stages:

1. validate source/target rows and the workspace boundary while holding the
   per-user ``memory_write:<id>`` Redis locks;
2. commit the soft-delete, the ``end_user_merge`` intent, the rebuilt target
   profile and — for a write-triggered merge — the source's identity stamp in
   ONE transaction (the old id then routes to the target, and a failed merge
   never leaves a source "confirmed" without the matching edge);
3. migrate ownership: graph nodes via Neo4j or messages via RAG;
4. move the PG reference rows;
5. publish ``app.tasks.do_layer2_reflection`` to the API worker (graph only).

The graph/RAG stages are injected callables: until they are ported this service
refuses to start (``MergeNotReady``) rather than commit a half-merge.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from task_bridge.metadata_migration import metadata_write_column, new_metadata

from src.common.timing import utcnow_naive
from src.config import ProfileSettings
from src.domain.metadata_merge import merge_user_meta_data
from src.infrastructure.api_tasks import dispatch_layer2_reflection
from src.infrastructure.memory_write_lock import (
    acquire_memory_write_locks,
    release_memory_write_locks,
)
from src.infrastructure.postgres import PostgresManager
from src.models.references.end_user_model import EndUser
from src.models.references.memory_generation_model import EndUserInfo
from src.models.references.workspace_model import Workspace
from src.repositories.end_user_merge import EndUserMergeRepository

logger = logging.getLogger(__name__)


async def missing_end_users(
    postgres: PostgresManager,
    workspace_id: uuid.UUID,
    end_user_ids: set[uuid.UUID],
) -> set[uuid.UUID]:
    """Ids absent from the workspace, regardless of status (API controller check)."""

    if not end_user_ids:
        return set()
    async with postgres.session() as session:
        existing = await EndUserMergeRepository.existing_ids_any_status(
            session, end_user_ids, workspace_id
        )
    return set(end_user_ids) - existing

GraphMerge = Callable[[list[str], str], Awaitable[Any]]
RagMerge = Callable[[list[str], str], Awaitable[Any]]
MemoryCount = Callable[[str], Awaitable[int]]
ConfigResolver = Callable[[uuid.UUID], Awaitable[uuid.UUID | None]]
#: ``(end_user_id, legacy meta_data) -> migrated meta_data_new`` (LLM-backed).
MigrateMetadata = Callable[[uuid.UUID, object], Awaitable[dict[str, object]]]
#: ``(workspace_id, scene names) -> {scene name: scene_type_id}``.
SceneNameResolver = Callable[[uuid.UUID, list[str]], Awaitable[dict[str, str]]]
RefreshForgetCandidates = Callable[[uuid.UUID], Awaitable[None]]

_RAG_MIGRATION_MAX_ATTEMPTS = 3
_RAG_MIGRATION_RETRY_DELAY_SECONDS = 1.0

_RESET_PROFILE_FIELDS: tuple[str, ...] = (
    "memory_tags",
    "memory_tags_source_fingerprint",
    "memory_tags_updated_at",
    "memory_insight",
    "memory_insight_updated_at",
    "key_findings",
    "behavior_pattern",
    "growth_trajectory",
    "user_summary",
    "user_summary_updated_at",
    "personality_traits",
    "core_values",
    "one_sentence_summary",
)


class MergeRejected(Exception):
    """The host's ``fail(code, msg)`` shape for an input/ownership problem."""

    def __init__(self, message: str, *, reason: str = "invalid") -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason


class MergeNotReady(RuntimeError):
    """Graph/RAG adapters are not wired yet; a half-merge must not run."""


@dataclass(frozen=True, slots=True)
class MergeOutcome:
    end_user_id: str


@dataclass(frozen=True, slots=True)
class IdentityStamp:
    """The source row's identity values for a write-triggered merge.

    The API writes these inside the merge-intent transaction (``flush`` before
    ``_merge_end_users_locked``), so a failed merge rolls them back and the
    next write retries the merge. Carrying them here keeps that transaction
    boundary instead of committing the stamp in a separate one.
    """

    workspace_id: uuid.UUID
    end_user_id: uuid.UUID
    identity_features: str
    identity_status: str = "confirmed"


class EndUserMergeService:
    def __init__(
        self,
        *,
        postgres: PostgresManager,
        settings: ProfileSettings,
        task_bridge: object,
        graph_merge: GraphMerge | None = None,
        rag_merge: RagMerge | None = None,
        graph_memory_count: MemoryCount | None = None,
        rag_memory_count: MemoryCount | None = None,
        config_resolver: ConfigResolver | None = None,
        migrate_metadata: MigrateMetadata | None = None,
        scene_names: SceneNameResolver | None = None,
        refresh_forget_candidates: RefreshForgetCandidates | None = None,
    ) -> None:
        self._postgres = postgres
        self._settings = settings
        self._task_bridge = task_bridge
        self._graph_merge = graph_merge
        self._rag_merge = rag_merge
        self._graph_memory_count = graph_memory_count
        self._rag_memory_count = rag_memory_count
        self._config_resolver = config_resolver
        self._migrate_metadata = migrate_metadata
        self._scene_names = scene_names
        self._refresh_forget_candidates = refresh_forget_candidates

    @property
    def ready(self) -> bool:
        return self._graph_merge is not None and self._rag_merge is not None

    async def merge(
        self,
        source: set[uuid.UUID],
        target: uuid.UUID,
        *,
        identity: IdentityStamp | None = None,
    ) -> MergeOutcome:
        if not source:
            raise MergeRejected("No source user provided.")
        if target in source:
            raise MergeRejected("Target user cannot be a source user.")
        if not self.ready:
            raise MergeNotReady(
                "graph/RAG merge adapters are not wired; refusing a half-merge"
            )

        locks: list[Any] = []
        try:
            if identity is not None:
                # The API's lock order for an identity merge: the advisory lock
                # first (so it can never deadlock against an API process that
                # holds it and waits for the write locks), then the per-user
                # write locks, then the FOR UPDATE re-read — all inside the
                # transaction that commits the intent and the identity stamp.
                async with self._postgres.session() as session:
                    await self._lock_identity(session, identity)
                    locks = await acquire_memory_write_locks(
                        self._settings, {target, *source}
                    )
                    storage_type, workspace_id = await self._commit_merge_intent(
                        source, target, locks, identity=identity, intent_session=session
                    )
            else:
                locks = await acquire_memory_write_locks(
                    self._settings, {target, *source}
                )
                storage_type, workspace_id = await self._commit_merge_intent(
                    source, target, locks
                )

            source_ids = sorted(str(value) for value in source)
            if storage_type == "rag":
                await self._rag_merge_with_retry(source_ids, str(target))
            else:
                await self._graph_merge(source_ids, str(target))  # type: ignore[misc]

            await self._migrate_references(source, target)
            await self._sync_memory_count(storage_type, target)
            await self._touch_write_time(target)
            await self._dispatch_reflection(
                storage_type, target, workspace_id, source
            )
        finally:
            if locks:
                await release_memory_write_locks(locks)

        logger.info(
            "[merge_end_users] 完成: source=%s target=%s storage=%s",
            source,
            target,
            storage_type,
        )
        return MergeOutcome(end_user_id=target.hex)

    @staticmethod
    async def _lock_identity(session: Any, identity: IdentityStamp) -> None:
        """Transaction-scoped identity lock, on the API's exact key.

        ``pg_advisory_xact_lock`` is re-entrant inside one transaction, so
        calling it again when the intent session is reused is harmless.
        """

        await session.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"eu_identity:{identity.workspace_id}|{identity.identity_features}"},
        )

    async def _commit_merge_intent(
        self,
        source: set[uuid.UUID],
        target: uuid.UUID,
        locks: list[Any],
        *,
        identity: IdentityStamp | None = None,
        intent_session: Any | None = None,
    ) -> tuple[str, uuid.UUID]:
        """Validate everything, then commit soft-delete + intent + profile."""

        repository = EndUserMergeRepository
        if intent_session is not None:
            manager: Any = nullcontext(intent_session)
        else:
            manager = self._postgres.session()
        async with manager as session:
            source_users: dict[uuid.UUID, EndUser] = {}
            for end_user_id in source:
                user = await repository.get_end_user_any(session, end_user_id)
                if user is not None:
                    source_users[end_user_id] = user

            missing = source - set(source_users)
            if missing:
                raise MergeRejected(
                f"Source users not found: {missing}.", reason="user_not_found"
            )

            target_user = await repository.get_end_user_any(session, target)
            if target_user is None or not target_user.is_active:
                raise MergeRejected(
                "Target user not found or inactive.", reason="user_not_found"
            )

            workspace_id = target_user.workspace_id
            mismatched = {
                source_id
                for source_id, user in source_users.items()
                if user.workspace_id != workspace_id
            }
            if mismatched:
                raise MergeRejected(
                    f"Cross-workspace merge is not allowed: {mismatched} "
                    f"not in workspace {workspace_id}."
                )

            all_infos: dict[uuid.UUID, EndUserInfo] = {}
            for end_user_id in {target, *source}:
                info = await repository.get_end_user_info(session, end_user_id)
                if info is None:
                    raise MergeRejected(
                        f"User info missing before merge: {end_user_id}"
                    )
                all_infos[end_user_id] = info

            existing_targets: dict[uuid.UUID, uuid.UUID | None] = {}
            for source_id, user in source_users.items():
                merge_target = await repository.get_merge_target_id(
                    session, source_id, workspace_id
                )
                existing_targets[source_id] = merge_target
                if merge_target is not None and merge_target != target:
                    raise MergeRejected(
                        f"Source user {source_id} is already merged to {merge_target}.",
                        reason="conflict",
                    )
                if not user.is_active and merge_target != target:
                    raise MergeRejected(
                        f"Inactive source user {source_id} has no matching merge intent.",
                        reason="conflict",
                    )

            if identity is not None:
                # Write-triggered merge: the candidate is re-read under a row
                # lock (the API's second guard) and the identity columns are
                # written in this transaction, so a later failure rolls them
                # back with the intent instead of leaving the source
                # "confirmed" forever.
                if identity.end_user_id not in source:
                    raise MergeRejected(
                        "Identity subject is not a merge source.", reason="conflict"
                    )
                if intent_session is None:
                    await self._lock_identity(session, identity)
                current = await repository.get_active_by_identity(
                    session,
                    workspace_id=workspace_id,
                    identity_features=identity.identity_features,
                    for_update=True,
                )
                if current is None or current.id != target:
                    raise MergeRejected(
                        "Identity merge target changed during lock acquisition.",
                        reason="conflict",
                    )
                await repository.stamp_identity(
                    session,
                    end_user_id=identity.end_user_id,
                    identity_features=identity.identity_features,
                    identity_status=identity.identity_status,
                )

            prepared = await self._prepare_metadata(
                session,
                users={target: target_user, **source_users},
                infos=all_infos,
                locks=locks,
            )
            scene_names = {
                key
                for metadata in prepared.values()
                if isinstance(metadata.get("domain_specific_info"), dict)
                for key in metadata["domain_specific_info"]
            }
            name_to_id_map: dict[str, str] = {}
            if self._scene_names is not None and scene_names:
                name_to_id_map = await self._scene_names(
                    workspace_id, sorted(scene_names)
                )
            merged_meta = merge_user_meta_data(
                prepared[target], {}, name_to_id_map
            )
            for source_id in sorted(source, key=str):
                merged_meta = merge_user_meta_data(
                    merged_meta, prepared[source_id], name_to_id_map
                )

            final_aliases = list(all_infos[target].aliases or [])
            seen = {alias.strip().lower() for alias in final_aliases}
            for source_id in sorted(source, key=str):
                for raw_alias in all_infos[source_id].aliases or []:
                    alias = raw_alias.strip()
                    if alias and alias.lower() not in seen:
                        final_aliases.append(alias)
                        seen.add(alias.lower())

            for field in _RESET_PROFILE_FIELDS:
                setattr(target_user, field, None)
            all_infos[target].aliases = final_aliases
            all_infos[target].meta_data_new = new_metadata(merged_meta)

            await repository.soft_delete_many(session, source)
            await repository.flatten_merge_chain(
                session, source, target, workspace_id
            )
            for source_id in source:
                if existing_targets.get(source_id) == target:
                    continue
                origin_other_id = source_users[source_id].other_id or str(source_id)
                repository.add_merge_record(
                    session,
                    origin_id=source_id,
                    origin_other_id=origin_other_id,
                    target_id=target,
                    workspace_id=workspace_id,
                )
            await session.commit()
            # Resolved after the atomic intent commit, like the API: an unknown
            # storage type fails the merge with the soft-delete/mapping already
            # durable, so a retry can converge.
            storage_type = await self._resolve_storage_type(session, workspace_id)

        return storage_type, workspace_id

    @staticmethod
    async def _resolve_storage_type(session: Any, workspace_id: uuid.UUID) -> str:
        value = (
            await session.execute(
                sa.select(Workspace.storage_type).where(Workspace.id == workspace_id)
            )
        ).scalar_one_or_none()
        normalized = str(value or "").strip().lower()
        if normalized not in ("rag", "neo4j"):
            raise MergeRejected(
                f"Unresolved workspace storage_type for merge: "
                f"workspace={workspace_id}, storage_type={value!r}"
            )
        return normalized

    async def _prepare_metadata(
        self,
        session: Any,
        *,
        users: dict[uuid.UUID, EndUser],
        infos: dict[uuid.UUID, EndUserInfo],
        locks: list[Any],
    ) -> dict[uuid.UUID, dict[str, Any]]:
        """Normalize existing ``meta_data_new`` or migrate legacy metadata first.

        Mirrors the API's ``_prepare_merge_metadata``: a user whose new columns are
        still NULL is migrated with the LLM while the per-user write lock is held,
        and the result is written with the same CAS predicate so a concurrent
        writer cannot be clobbered.
        """

        by_key = {lock.key: lock for lock in locks}
        prepared: dict[uuid.UUID, dict[str, Any]] = {}
        for end_user_id, user in users.items():
            info = infos.get(end_user_id)
            if info is None:
                raise MergeRejected(f"User info missing before merge: {end_user_id}")
            lock = by_key.get(f"memory_write:{end_user_id}")
            if lock is None:
                raise MergeRejected(f"User write lock missing: {end_user_id}")
            lock.ensure_valid()

            if info.meta_data_new is not None:
                prepared[end_user_id] = new_metadata(info.meta_data_new)
                continue
            if not user.is_active:
                # A softly deleted source is never migrated; pretending it merged
                # would attach metadata the user no longer owns.
                raise MergeRejected(
                    f"Inactive source has unmigrated metadata: {end_user_id}"
                )
            if metadata_write_column(user.created_at, None) == "meta_data_new":
                prepared[end_user_id] = {}
                continue
            if not self._settings.metadata_migration_enabled:
                raise MergeRejected(
                    "Metadata migration disabled; retry user merge after migration"
                )
            if self._migrate_metadata is None:
                raise MergeRejected(
                    "Legacy metadata migration is not configured; retry after "
                    f"the background migration: {end_user_id}"
                )

            original = info.meta_data
            if original is not None and not isinstance(original, dict):
                raise MergeRejected(
                    f"Invalid metadata before merge: {end_user_id}"
                )
            merged = await self._migrate_metadata(end_user_id, original)

            lock.ensure_valid()
            if await asyncio.to_thread(lock.redis.get, lock.key) != lock.value:
                raise MergeRejected(f"User write lock lost: {end_user_id}")
            baseline_matches = (
                EndUserInfo.meta_data.is_(None)
                if original is None
                else EndUserInfo.meta_data == original
            )
            result = await session.execute(
                sa.update(EndUserInfo)
                .where(
                    EndUserInfo.end_user_id == end_user_id,
                    EndUserInfo.meta_data_new.is_(None),
                    baseline_matches,
                    EndUserInfo.end_user_id.in_(
                        sa.select(EndUser.id).where(EndUser.is_active.is_(True))
                    ),
                )
                .values(meta_data_new=merged, updated_at=utcnow_naive())
            )
            if not result.rowcount:
                raise MergeRejected(
                    f"Metadata changed before merge: {end_user_id}; retry"
                )
            prepared[end_user_id] = merged
        return prepared

    async def _migrate_references(
        self, source: set[uuid.UUID], target: uuid.UUID
    ) -> None:
        async with self._postgres.session() as session:
            for source_id in sorted(source, key=str):
                await EndUserMergeRepository.migrate_references(
                    session, source_id, target
                )
            await session.commit()

    async def _sync_memory_count(self, storage_type: str, target: uuid.UUID) -> None:
        counter = (
            self._rag_memory_count if storage_type == "rag" else self._graph_memory_count
        )
        if counter is None:
            return
        if storage_type == "rag":
            try:
                await self._set_memory_count(target, int(await counter(str(target))))
            except Exception:
                # The merge itself is complete; a stale cache count is repaired by
                # the maintenance jobs, so do not fail the caller (API parity).
                logger.warning(
                    "[merge_end_users] memory_count 同步失败: target=%s",
                    target,
                    exc_info=True,
                )
            return

        # Graph mode: the API lets a count/candidate failure fail the merge so a
        # retry can converge; the same policy applies here.
        await self._set_memory_count(target, int(await counter(str(target))))
        if self._refresh_forget_candidates is not None:
            await self._refresh_forget_candidates(target)

    async def _set_memory_count(self, target: uuid.UUID, count: int) -> None:
        async with self._postgres.session() as session:
            await EndUserMergeRepository.set_memory_count(session, target, count)
            await session.commit()
        logger.info(
            "[merge_end_users] memory_count 同步完成: target=%s count=%s",
            target,
            count,
        )

    async def _touch_write_time(self, target: uuid.UUID) -> None:
        """Refresh the target's activity stamp before reflection (API parity)."""

        try:
            async with self._postgres.session() as session:
                await EndUserMergeRepository.touch_write_time(session, target)
                await session.commit()
        except Exception:
            logger.warning(
                "[merge_end_users] 刷新 target write_time 失败（不影响合并结果）: %s",
                target,
                exc_info=True,
            )

    async def _rag_merge_with_retry(
        self, source_ids: list[str], target_id: str
    ) -> None:
        for attempt in range(1, _RAG_MIGRATION_MAX_ATTEMPTS + 1):
            try:
                await self._rag_merge(source_ids, target_id)  # type: ignore[misc]
                return
            except Exception:
                if attempt >= _RAG_MIGRATION_MAX_ATTEMPTS:
                    logger.error(
                        "[merge_end_users] RAG 索引迁移失败，归并中止（可重试）: "
                        "sources=%s target=%s attempts=%s",
                        source_ids,
                        target_id,
                        attempt,
                        exc_info=True,
                    )
                    raise
                logger.warning(
                    "[merge_end_users] RAG 索引迁移失败，准备重试: sources=%s "
                    "target=%s attempt=%s",
                    source_ids,
                    target_id,
                    attempt,
                    exc_info=True,
                )
                await asyncio.sleep(
                    _RAG_MIGRATION_RETRY_DELAY_SECONDS * attempt
                )

    async def _dispatch_reflection(
        self,
        storage_type: str,
        target: uuid.UUID,
        workspace_id: uuid.UUID,
        source: set[uuid.UUID],
    ) -> None:
        if storage_type != "neo4j":
            logger.info(
                "[merge_end_users] RAG 空间跳过图侧反思: target=%s", target
            )
            return
        if self._config_resolver is None:
            logger.warning(
                "[merge_end_users] 无 config 解析器，跳过反思触发: target=%s "
                "source=%s",
                target,
                source,
            )
            return
        try:
            config_id = await self._config_resolver(target)
            if config_id is None:
                logger.warning(
                    "[merge_end_users] target 无可用的 memory_config，跳过反思: %s",
                    target,
                )
                return
            task_id = await dispatch_layer2_reflection(
                self._task_bridge,
                end_user_id=target,
                config_id=config_id,
                workspace_id=workspace_id,
            )
            logger.info(
                "[merge_end_users] 已直投反思任务: target=%s task=%s",
                target,
                task_id,
            )
        except Exception:
            logger.warning(
                "[merge_end_users] 派发反思任务失败（不影响合并结果）: target=%s",
                target,
                exc_info=True,
            )


__all__ = [
    "ConfigResolver",
    "EndUserMergeService",
    "GraphMerge",
    "IdentityStamp",
    "MergeNotReady",
    "MergeOutcome",
    "MergeRejected",
    "RagMerge",
    "missing_end_users",
]