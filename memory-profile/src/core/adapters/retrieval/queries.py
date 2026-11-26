"""Read-only custom lookups used by the retrieval adapter.

Ported from the read subset of ``api/app/core/memory/storage/custom`` (node and
relationship queries) plus the two PostgreSQL facts the read path needs
(``end_user_info.meta_data`` for the L0 profile, ``conversations`` /
``memory_messages`` for RECENT). Everything is read-only: graph reads go through
:class:`ReadStorage`, SQL reads through ``PostgresManager.session``.

Known gaps (documented for the migration report):

* ``get_user_metadata`` is cached for 60s in the host (Redis); this port reads
  through on every call so a stale profile cannot leak across requests.
* scene names resolve through memory-enterprise (the host's
  ``memory.ontology_query`` plugin equivalent) and fall back to the read-only
  ``ontology_scene`` table; either failure degrades to ``{}``, which keeps the
  stored keys exactly like the host does without a plugin.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy import select

from src.core.adapters.retrieval.constants import EXCLUDED_APP_ID, USER_ENTITY_TYPE
from src.core.adapters.retrieval.metadata import (
    LEGACY_METADATA_FIELDS,
    NEW_METADATA_FIELDS,
    is_scene_type_id,
)
from src.core.adapters.storage import (
    FilterCondition,
    FilterOperator,
    InCondition,
    NodeFilter,
    ReadStorage,
    RelationshipFilter,
    RelationshipPattern,
    RelationshipProjectionField,
    projection_of,
)
from src.core.domain.enums import MemorySource
from src.models.references import (
    Conversation,
    EndUserInfo,
    MemoryMessage,
    OntologySceneRow,
)

logger = logging.getLogger(__name__)

#: API-owned tables are read through ``sa.table`` so this adapter needs no
#: additional ORM mapping (and never touches the write-side models).
_ENTITY_LOOKUP_PROJECTION = projection_of("id", "name", "description")
_USER_FIELDS_PROJECTION = projection_of("description", "aliases", "event_timeline", "id")
_USER_ENTITY_ID_PROJECTION = projection_of("id")


class RetrievalQueries:
    """The custom read helpers the retrieval sessions call."""

    def __init__(
        self,
        *,
        storage: ReadStorage,
        postgres: Any,
        enterprise: Any | None = None,
    ) -> None:
        self._storage = storage
        self._postgres = postgres
        self._enterprise = enterprise

    # ── graph lookups ───────────────────────────────────────────

    async def node_by_id(
        self,
        label: str,
        node_id: str,
        projection: tuple[Any, ...] | None = None,
    ) -> dict | None:
        result = await self._storage.get_node(
            label,
            NodeFilter.all_of(FilterCondition(field="id", value=node_id)),
            projection,
        )
        return result.items[0].data if result.items else None

    async def user_entity_id(self, end_user_id: str) -> str | None:
        """The active profile entity of one end user (``entity_type=用户``)."""

        result = await self._storage.get_node(
            MemorySource.EXTRACTED_ENTITY.value,
            NodeFilter.all_of(
                FilterCondition(field="end_user_id", value=end_user_id),
                FilterCondition(field="entity_type", value=USER_ENTITY_TYPE),
                FilterCondition(
                    field="delete_at", operator=FilterOperator.NOT_EXISTS
                ),
            ),
            _USER_ENTITY_ID_PROJECTION,
        )
        if not result.items:
            return None
        node_id = result.items[0].data.get("id")
        return str(node_id) if node_id is not None else None

    async def active_entities_by_ids(
        self, end_user_id: str, entity_ids: list[str]
    ) -> list[dict]:
        if not entity_ids:
            return []
        result = await self._storage.get_node(
            MemorySource.EXTRACTED_ENTITY.value,
            NodeFilter.all_of(
                FilterCondition(field="end_user_id", value=end_user_id),
                InCondition(field="id", values=tuple(entity_ids)),
                FilterCondition(
                    field="delete_at", operator=FilterOperator.NOT_EXISTS
                ),
            ),
            _ENTITY_LOOKUP_PROJECTION,
        )
        return [item.data for item in result.items]

    async def search_entities_by_name(
        self, end_user_id: str, name: str, limit: int = 10
    ) -> list[dict]:
        result = await self._storage.search_by_fulltext(
            node_filters={
                MemorySource.EXTRACTED_ENTITY.value: NodeFilter.all_of(
                    FilterCondition(field="end_user_id", value=end_user_id),
                    FilterCondition(
                        field="delete_at", operator=FilterOperator.NOT_EXISTS
                    ),
                )
            },
            text=name,
            pre_limit=limit,
            projections={
                MemorySource.EXTRACTED_ENTITY.value: projection_of(
                    "id", "name", "entity_type"
                )
            },
        )
        return [item.data for item in result.items]

    async def search_related_entities(
        self,
        end_user_id: str,
        source_id: str,
        predicate_ids: list[int],
    ) -> list[dict]:
        if not predicate_ids:
            return []
        pattern = RelationshipPattern(
            source_label=MemorySource.EXTRACTED_ENTITY.value,
            target_label=MemorySource.EXTRACTED_ENTITY.value,
            relationship_type="EXTRACTED_RELATIONSHIP",
            directed=False,
        )
        # ``predicate_id IN (...)`` is not part of the core filter vocabulary;
        # it rides along as an edge-scoped ``InCondition``.
        rel_filter = RelationshipFilter(
            source=NodeFilter.all_of(
                FilterCondition(field="end_user_id", value=end_user_id),
                FilterCondition(field="id", value=source_id),
                FilterCondition(
                    field="delete_at", operator=FilterOperator.NOT_EXISTS
                ),
            ),
            target=NodeFilter.all_of(
                FilterCondition(field="end_user_id", value=end_user_id),
                FilterCondition(
                    field="delete_at", operator=FilterOperator.NOT_EXISTS
                ),
            ),
            relationship=NodeFilter.all_of(
                InCondition(field="predicate_id", values=tuple(predicate_ids))
            ),
        )
        projection = (
            RelationshipProjectionField(field="id", scope="target"),
            RelationshipProjectionField(
                field="name", scope="source", alias="source_name"
            ),
            RelationshipProjectionField(
                field="predicate", scope="relationship", alias="relation_predicate"
            ),
            RelationshipProjectionField(
                field="name", scope="target", alias="target_name"
            ),
        )
        result = await self._storage.search_relationships(
            pattern, rel_filter, projection
        )
        return [item.data for item in result.items]

    async def user_sources_for_entities(
        self, end_user_id: str, entity_ids: list[str]
    ) -> list[dict]:
        if not entity_ids:
            return []
        pattern = RelationshipPattern(
            source_label=MemorySource.USER_SOURCE.value,
            target_label=MemorySource.EXTRACTED_ENTITY.value,
            relationship_type="HAS_ORIGINAL_CONTENT",
            directed=True,
        )
        rel_filter = RelationshipFilter(
            source=NodeFilter.all_of(
                FilterCondition(field="end_user_id", value=end_user_id)
            ),
            target=NodeFilter.all_of(
                InCondition(field="id", values=tuple(entity_ids))
            ),
        )
        projection = (
            RelationshipProjectionField(field="id", scope="target", alias="entity_id"),
            RelationshipProjectionField(field="original_text", scope="source"),
        )
        result = await self._storage.search_relationships(pattern, rel_filter, projection)
        return [
            {
                "entity_id": item.data["entity_id"],
                "original_text": item.data["original_text"],
            }
            for item in result.items
            if item.data.get("entity_id") and item.data.get("original_text")
        ]

    async def entity_pair_relations(
        self, end_user_id: str, pairs: list[dict[str, str]]
    ) -> list[dict]:
        valid_pairs = [
            pair for pair in pairs if pair.get("source_id") and pair.get("target_id")
        ]
        if not valid_pairs:
            return []
        pattern = RelationshipPattern(
            source_label=MemorySource.EXTRACTED_ENTITY.value,
            target_label=MemorySource.EXTRACTED_ENTITY.value,
            relationship_type=None,
            directed=False,
        )
        projection = (
            RelationshipProjectionField(field="id", scope="source", alias="source_id"),
            RelationshipProjectionField(
                field="name", scope="source", alias="source_name"
            ),
            RelationshipProjectionField(
                field="predicate", scope="relationship", alias="relation_predicate"
            ),
            RelationshipProjectionField(field="id", scope="target", alias="target_id"),
            RelationshipProjectionField(
                field="name", scope="target", alias="target_name"
            ),
        )
        results = await asyncio.gather(
            *(
                self._storage.search_relationships(
                    pattern,
                    RelationshipFilter(
                        source=NodeFilter.all_of(
                            FilterCondition(field="end_user_id", value=end_user_id),
                            FilterCondition(field="id", value=pair["source_id"]),
                            FilterCondition(
                                field="delete_at",
                                operator=FilterOperator.NOT_EXISTS,
                            ),
                        ),
                        target=NodeFilter.all_of(
                            FilterCondition(field="end_user_id", value=end_user_id),
                            FilterCondition(field="id", value=pair["target_id"]),
                            FilterCondition(
                                field="delete_at",
                                operator=FilterOperator.NOT_EXISTS,
                            ),
                        ),
                    ),
                    projection,
                )
                for pair in valid_pairs
            )
        )
        return [item.data for result in results for item in result.items]

    # ── PostgreSQL lookups ──────────────────────────────────────

    async def user_metadata(self, end_user_id: str) -> dict:
        """The fields used to build the L0 user profile (host ``get_user_metadata``)."""

        node_result, meta_data = await asyncio.gather(
            self._storage.get_node(
                MemorySource.EXTRACTED_ENTITY.value,
                NodeFilter.all_of(
                    FilterCondition(field="end_user_id", value=end_user_id),
                    FilterCondition(field="entity_type", value=USER_ENTITY_TYPE),
                    FilterCondition(
                        field="delete_at", operator=FilterOperator.NOT_EXISTS
                    ),
                ),
                _USER_FIELDS_PROJECTION,
            ),
            self._read_pg_meta_data(end_user_id),
        )
        node_data = node_result.items[0].data if node_result.items else {}
        metadata = {
            field: meta_data[field]
            for field in (*LEGACY_METADATA_FIELDS, *NEW_METADATA_FIELDS)
            if field in meta_data
        }
        # The graph node wins on overlapping keys, exactly like the host merge.
        return {**metadata, **node_data}

    async def _read_pg_meta_data(self, end_user_id: str) -> dict:
        try:
            user_uuid = uuid.UUID(str(end_user_id))
        except (ValueError, TypeError, AttributeError):
            return {}
        statement = (
            select(EndUserInfo.meta_data)
            .where(EndUserInfo.end_user_id == user_uuid)
            .limit(1)
        )
        async with self._postgres.session() as session:
            meta_data = (await session.execute(statement)).scalars().first()
        return meta_data if isinstance(meta_data, dict) else {}

    async def scene_names(
        self, record: dict, workspace_id: Any, tenant_id: Any | None = None
    ) -> dict[str, str]:
        """Map scene type ids in ``domain_specific_info`` to scene names.

        The enterprise build resolves names through the ``memory.ontology_query``
        plugin, whose data lives in the enterprise-owned ontology tables.
        memory-profile reaches the same capability through memory-enterprise's
        tenant-scoped scene-names endpoint; the read-only ``ontology_scene``
        table stays as the fallback so a deployment without the service (or a
        failed call) keeps the stored keys instead of losing the profile.
        """

        domains = record.get("domain_specific_info") if isinstance(record, dict) else None
        if not isinstance(domains, dict):
            return {}
        scene_ids: list[uuid.UUID] = []
        for key in domains:
            if is_scene_type_id(key):
                scene_ids.append(uuid.UUID(str(key)))
        if not scene_ids:
            return {}
        unique_ids = list(dict.fromkeys(scene_ids))

        enterprise = self._enterprise
        if (
            enterprise is not None
            and tenant_id is not None
            and getattr(enterprise, "enabled", False)
        ):
            try:
                return dict(
                    await enterprise.scene_names(
                        tenant_id=tenant_id,
                        scene_ids=[str(scene_id) for scene_id in unique_ids],
                    )
                )
            except Exception:
                logger.warning(
                    "[Retrieval] enterprise scene name lookup failed; "
                    "falling back to the read-only table",
                    exc_info=True,
                )

        statement = select(
            OntologySceneRow.scene_id, OntologySceneRow.scene_name
        ).where(
            OntologySceneRow.scene_id.in_(unique_ids),
            OntologySceneRow.workspace_id == workspace_id,
        )
        try:
            async with self._postgres.session() as session:
                rows = (await session.execute(statement)).all()
        except Exception:
            logger.warning(
                "[Retrieval] scene name lookup failed; keeping stored keys",
                exc_info=True,
            )
            return {}
        return {
            str(scene_id): scene_name
            for scene_id, scene_name in rows
            if scene_name
        }

    async def conversation_history(
        self, *, end_user_id: str, conversation_id: str | None
    ) -> dict | None:
        """The most recent conversation (other than the current one) for RECENT.

        Returns ``{"id": str, "messages": [{"role", "content", "files"}]}`` or
        ``None`` when the user has no eligible conversation/messages.
        """

        excluded_id: uuid.UUID | None = None
        if conversation_id:
            try:
                excluded_id = uuid.UUID(str(conversation_id))
            except (ValueError, TypeError, AttributeError):
                excluded_id = None
        conditions = [
            Conversation.user_id == end_user_id,
            Conversation.app_id != uuid.UUID(EXCLUDED_APP_ID),
        ]
        if excluded_id is not None:
            conditions.append(Conversation.id != excluded_id)
        conversation_statement = (
            select(
                Conversation.id,
                Conversation.write_cursor,
            )
            .where(*conditions)
            .order_by(Conversation.updated_at.desc())
            .limit(1)
        )
        async with self._postgres.session() as session:
            conversation = (await session.execute(conversation_statement)).first()
            if conversation is None:
                return None
            conversation_uuid, cursor = conversation
            messages_statement = (
                select(
                    MemoryMessage.role,
                    MemoryMessage.content,
                    MemoryMessage.files,
                )
                .where(
                    MemoryMessage.conversation_id == conversation_uuid,
                    MemoryMessage.message_seq > int(cursor or 0),
                )
                .order_by(MemoryMessage.message_seq)
            )
            messages = (await session.execute(messages_statement)).all()
        if not messages:
            return None
        return {
            "id": str(conversation_uuid),
            "messages": [
                {"role": role, "content": content, "files": files}
                for role, content, files in messages
            ],
        }

    async def user_sources_map(
        self, end_user_id: str, entity_ids: set[str]
    ) -> dict[str, str]:
        """Merge ``HAS_ORIGINAL_CONTENT`` texts per entity (host behaviour)."""

        records = await self.user_sources_for_entities(end_user_id, list(entity_ids))
        grouped: dict[str, list[str]] = {}
        for record in records:
            entity_id = record.get("entity_id", "")
            text = record.get("original_text", "")
            if not entity_id or not text:
                continue
            grouped.setdefault(entity_id, []).append(text)
        return {
            entity_id: "\n---\n".join(texts) for entity_id, texts in grouped.items()
        }


__all__ = ["RetrievalQueries"]


async def _resolve_metadata_scope(
    self: RetrievalQueries,
    end_user_id: str,
    app_id: uuid.UUID | None = None,
    *,
    tenant_id: uuid.UUID | None = None,
    workspace_id: uuid.UUID | None = None,
) -> dict[str, Any] | None:
    """Resolve the caller app's ontology boundary through memory-enterprise.

    The read wire contract does not carry a caller app yet, so a read without
    one uses the documented ``default_only`` projection. The call acts for the
    read's tenant, which the enterprise client signs into its internal JWT.
    Any failure (service disabled, transport error, no binding) degrades to
    ``default_only``.
    """

    if (
        app_id is None
        or tenant_id is None
        or self._enterprise is None
        or not self._enterprise.enabled
    ):
        return None
    try:
        user_uuid = uuid.UUID(str(end_user_id))
    except (TypeError, ValueError):
        return None
    try:
        return await self._enterprise.metadata_scope(
            tenant_id=tenant_id,
            app_id=app_id,
            end_user_id=user_uuid,
            workspace_id=workspace_id,
        )
    except Exception:
        logger.warning(
            "metadata scope lookup failed; falling back to default_only", exc_info=True
        )
        return None


RetrievalQueries.metadata_scope = _resolve_metadata_scope

