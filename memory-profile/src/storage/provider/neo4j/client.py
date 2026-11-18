"""Neo4j read client (ported from the host storage provider, writes stay on jobs).

Read surface only: fulltext / cosine-embedding recall, ``get_node``,
``scan_nodes``, relationship traversal, ``health`` and the generic
``execute_query`` used by custom read queries. Every write method of the host
client (node/edge save-update-delete, graph write commands, embedding update and
the managed write transactions) is intentionally absent: writes are owned by the
memory-jobs side.

Infrastructure seam: the host builds and owns a sync/async driver per client; the
profile owns one process-wide async driver behind
:class:`src.infrastructure.neo4j.Neo4jManager`, injected with
:func:`configure_neo4j_manager`. Cypher text is unchanged from the host so the
read behaviour (filters, projections, sort, scan cursors, cosine ranking) matches
line for line.
"""

import asyncio
import heapq
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Self

import numpy as np
from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncSession
from neo4j.graph import Node, Relationship

from src.storage.enums import (
    BackendType,
    MemoryNodeLabel,
    MemoryRelationshipType,
)
from src.storage.exceptions import UnsupportedQueryError
from src.storage.models import (
    NodeFilter,
    NodeProjection,
    NodeSort,
    RelationshipFilter,
    RelationshipPattern,
    RelationshipProjection,
    RelationshipSort,
    StorageReadResult,
)
from src.storage.provider.base import (
    BaseClient,
    MAX_SCAN_PAGE_SIZE,
    SCAN_PAGE_SIZE,
)
from src.storage.provider.neo4j.compiler.filter_compiler import (
    compile_neo4j_filter,
    compile_neo4j_relationship_filter,
)
from src.storage.provider.neo4j.compiler.projection_compiler import (
    compile_neo4j_projection,
    compile_neo4j_relationship_projection,
)
from src.storage.provider.neo4j.compiler.scan_compiler import (
    ScanCursor,
    compile_neo4j_scan_page,
    compile_neo4j_scan_preflight,
    decode_scan_cursor,
    encode_scan_cursor,
    resolve_scan_native_cursor,
)
from src.storage.provider.neo4j.compiler.sort_compiler import (
    compile_neo4j_relationship_sort,
    compile_neo4j_sort,
)
from src.storage.provider.neo4j.index.definitions import (
    EMBEDDING_FIELDS,
    FULLTEXT_ANNC,
)
from src.storage.utils.similarity import compute_cosine_similarity

try:  # the config builder is only the no-manager fallback
    from src.storage.provider.neo4j.config import build_neo4j_driver_config
except Exception:  # pragma: no cover - config is optional in tests
    build_neo4j_driver_config = None  # type: ignore[assignment]

#: Rows fetched per embedding scan page (host literal ``1000``, kept as a constant).
EMBEDDING_SCAN_BATCH = 1000

#: Process-wide manager injected by the profile composition (``configure_...``).
_manager: Any = None


def configure_neo4j_manager(manager: Any) -> None:
    """Bind the profile :class:`~src.infrastructure.neo4j.Neo4jManager`.

    Kept as a module-level hook (like the index builder shim) so the ported
    ``BackendFactory.create()`` call shape stays identical to the host.
    """

    global _manager
    _manager = manager


def _to_native(value: Any) -> Any:
    if value is None:
        return value
    if isinstance(value, (Node, Relationship)):
        return {key: _to_native(item) for key, item in value.items()}
    if hasattr(value, "to_native"):
        return value.to_native()
    if isinstance(value, dict):
        return {key: _to_native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_native(item) for item in value]
    return value


def _validate_scan_limit(limit: int) -> None:
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ValueError("scan limit must be an integer")
    if not 1 <= limit <= MAX_SCAN_PAGE_SIZE:
        raise ValueError(
            f"scan limit must be between 1 and {MAX_SCAN_PAGE_SIZE}"
        )


class Neo4jClient(BaseClient):
    name = BackendType.NEO4J

    def __init__(self):
        self.client: AsyncDriver | None = None

    @classmethod
    async def create(cls) -> Self:
        self = cls()
        self.client = await self.connect()
        return self

    async def health(self):
        async with self._session() as session:
            stmt = await session.run("return 1")
            await stmt.consume()

    async def connect(self) -> AsyncDriver:
        if _manager is not None:
            # The profile owns one driver per process; the client only borrows it.
            return await _manager.driver()
        if build_neo4j_driver_config is None:  # pragma: no cover - defensive
            raise RuntimeError("Neo4j client requires a configured manager")
        return AsyncGraphDatabase.driver(**build_neo4j_driver_config())

    def _database(self) -> str | None:
        if _manager is None:
            return None
        return getattr(_manager, "database", None)

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        """Session against the configured database (host used the default one)."""
        if self.client is None:
            raise RuntimeError("Neo4jClient is not initialized")
        database = self._database()
        kwargs = {"database": database} if database else {}
        async with self.client.session(**kwargs) as session:
            yield session

    async def close(self):
        if _manager is not None:
            # Borrowed driver: the process runtime closes it.
            return
        if self.client:
            await self.client.close()

    async def execute_query(
            self,
            cypher: str,
            **parameters: Any,
    ) -> list[dict[str, Any]]:
        """Execute provider-specific Cypher for storage custom queries."""
        async with self._session() as session:
            statement = await session.run(cypher, **parameters)
            records = await statement.data()
        return [
            {key: _to_native(value) for key, value in record.items()}
            for record in records
        ]

    async def get_relationship(
        self,
        pattern: RelationshipPattern,
        rel_filter: RelationshipFilter,
        projection: RelationshipProjection | None = None,
        sort: RelationshipSort | None = None,
    ) -> StorageReadResult:
        """Traverse relationships and return the connected (target) nodes."""
        relationship_type = pattern.relationship_type
        if relationship_type is not None and not isinstance(
                relationship_type, MemoryRelationshipType
        ):
            raise KeyError(
                f"relationship type - {relationship_type} not supported"
            )
        if pattern.source_label is not None:
            self.verify_label(pattern.source_label)
        if pattern.target_label is not None:
            self.verify_label(pattern.target_label)

        if relationship_type is not None:
            escaped_type = relationship_type.value.replace("`", "``")
            relationship_pattern = f"[r:`{escaped_type}`]"
        else:
            relationship_pattern = "[r]"
        edge = (
            f"-{relationship_pattern}->"
            if pattern.directed
            else f"-{relationship_pattern}-"
        )
        source_pattern = (
            f"(source:{pattern.source_label.value})"
            if pattern.source_label is not None
            else "(source)"
        )
        target_pattern = (
            f"(target:{pattern.target_label.value})"
            if pattern.target_label is not None
            else "(target)"
        )

        predicate, parameters = compile_neo4j_relationship_filter(rel_filter)
        return_expression, projection_parameters = (
            compile_neo4j_relationship_projection(projection)
        )
        order_by, sort_parameters = compile_neo4j_relationship_sort(sort)
        sort_clause = (
            f"WITH source, r, target\n        {order_by}"
            if order_by
            else ""
        )
        cypher = f"""
        MATCH {source_pattern}{edge}{target_pattern}
        WHERE {predicate}
        {sort_clause}
        RETURN {return_expression}
        """
        parameters.update(sort_parameters)
        parameters.update(projection_parameters)

        result_key = "item" if projection is not None else "target"
        async with self._session() as session:
            stmt = await session.run(cypher, **parameters)
            records = await stmt.data()

        items = [
            _to_native(record[result_key])
            for record in records
            if result_key in record
        ]
        return StorageReadResult.from_items(
            items,
            label=pattern.target_label,
            backend=self.name,
        )

    async def get_node(
            self,
            label: MemoryNodeLabel,
            node_filter: NodeFilter,
            projection: NodeProjection | None = None,
            node_sort: NodeSort | None = None,
    ) -> StorageReadResult:
        self.verify_label(label)
        predicate, filter_parameters = compile_neo4j_filter(node_filter)
        return_expression, projection_parameters = compile_neo4j_projection(
            projection
        )
        order_by, sort_parameters = compile_neo4j_sort(node_sort)
        sort_clause = f"WITH n\n        {order_by}" if order_by else ""
        query = f"""
        MATCH (n:{label.value})
        WHERE {predicate}
        {sort_clause}
        RETURN {return_expression}
        """
        parameters = {
            **filter_parameters,
            **sort_parameters,
            **projection_parameters,
        }
        async with self._session() as session:
            stmt = await session.run(query, **parameters)
            records = await stmt.data()
            items = [_to_native(record["n"]) for record in records]
        return StorageReadResult.from_items(items, label=label, backend=self.name)

    async def scan_nodes(
            self,
            label: MemoryNodeLabel,
            node_filter: NodeFilter,
            cursor: str | None = None,
            limit: int = SCAN_PAGE_SIZE,
            projection: NodeProjection | None = None,
    ) -> tuple[list[dict], str | None]:
        self.verify_label(label)
        _validate_scan_limit(limit)
        scan_cursor = decode_scan_cursor(cursor)
        native_cursor = scan_cursor.native
        if scan_cursor.value is None:
            # A fresh scan probes id typing once: string comparison can use the
            # id index, legacy mixed-type ids need the toString form.
            preflight, preflight_parameters = compile_neo4j_scan_preflight(
                label,
                node_filter,
            )
            async with self._session() as session:
                stmt = await session.run(preflight, **preflight_parameters)
                preflight_rows = await stmt.data()
            native_cursor = resolve_scan_native_cursor(
                label,
                preflight_rows[0] if preflight_rows else {},
            )

        query, parameters = compile_neo4j_scan_page(
            label,
            node_filter,
            cursor=ScanCursor(native=native_cursor, value=scan_cursor.value),
            limit=limit,
            projection=projection,
        )
        async with self._session() as session:
            stmt = await session.run(query, **parameters)
            records = await stmt.data()

        items = [
            _to_native(record["n"])
            for record in records
            if record.get("n") is not None
        ]
        if len(records) < limit:
            return items, None
        last_cursor = records[-1].get("cursor")
        if last_cursor is None:
            raise RuntimeError(
                f"{label.value} scan returned a full page without a cursor"
            )
        return items, encode_scan_cursor(native_cursor, str(last_cursor))

    async def search_by_embedding(
            self,
            label: MemoryNodeLabel,
            node_filter: NodeFilter,
            embed: list[float],
            limit: int,
            projection: NodeProjection | None = None,
    ) -> StorageReadResult:
        self.verify_label(label)
        embeding_field = EMBEDDING_FIELDS.get(label)
        if embeding_field is None:
            raise UnsupportedQueryError(self.name, label, 'embedding')
        query_vec = np.array(embed, dtype=np.float64)
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            return StorageReadResult(backend=self.name)
        predicate, filter_parameters = compile_neo4j_filter(node_filter)
        query_vec = query_vec / query_norm
        batch_query = f"""
        MATCH (n:{label.value})
        WHERE {predicate} AND n.id > $last_id
        RETURN n.id AS id,
               n.{embeding_field} AS embedding
        ORDER BY n.id
        LIMIT {EMBEDDING_SCAN_BATCH}
        """

        top_heap: list[tuple[float, str]] = []
        last_id = ""
        while 1:
            try:
                async with self._session() as session:
                    stmt = await session.run(
                        batch_query,
                        **filter_parameters,
                        last_id=last_id
                    )
                    records = await stmt.data()
                    batch = [_to_native(record) for record in records]
            except Exception:
                traceback.print_exc()
                break
            if not batch:
                break

            batch_vectors = []
            batch_ids = []
            for record in batch:
                emb = record.get("embedding")
                if emb is not None:
                    batch_vectors.append(emb)
                    batch_ids.append(record["id"])

            if batch_vectors:
                sims = await asyncio.to_thread(
                    compute_cosine_similarity, batch_vectors, query_vec,
                )
                for node_id, sim in zip(batch_ids, sims):
                    sim_f = float(sim)
                    if len(top_heap) < limit:
                        heapq.heappush(top_heap, (sim_f, node_id))
                    elif sim_f > top_heap[0][0]:
                        heapq.heapreplace(top_heap, (sim_f, node_id))

            if len(batch) < EMBEDDING_SCAN_BATCH:
                break
            last_id = batch[-1]["id"]

        if not top_heap:
            return StorageReadResult(backend=self.name)

        top_heap.sort(key=lambda x: x[0], reverse=True)
        top_ids = [node_id for _, node_id in top_heap]

        sim_map = {node_id: sim for sim, node_id in top_heap}
        try:
            return_expression, projection_parameters = compile_neo4j_projection(
                projection,
                virtual_fields={"score": "0"},
            )
            score_fields = {
                field.alias or field.field
                for field in projection.fields
                if not isinstance(field, str) and field.field == "score"
            } if projection is not None else set()
            include_score = (
                    projection is not None
                    and any(
                field == "score" if isinstance(field, str) else field.field == "score"
                for field in projection.fields
            )
            )
            if projection is not None and "score" in {
                field for field in projection.fields if isinstance(field, str)
            }:
                score_fields.add("score")
            async with self._session() as session:
                node_query = f"""
                MATCH (n: {label.value})
                WHERE n.id IN $ids
                RETURN {return_expression}, n.id AS __search_id
                """
                stmt = await session.run(
                    node_query,
                    ids=top_ids,
                    **projection_parameters
                )
                records = await stmt.data()
                projected = [
                    (_to_native(record["n"]), record["__search_id"])
                    for record in records
                ]
                rank_map = {node_id: index for index, node_id in enumerate(top_ids)}
                projected.sort(key=lambda item: rank_map.get(item[1], limit))
                res = []
                for node, node_id in projected:
                    if include_score:
                        for field_name in score_fields:
                            node[field_name] = sim_map.get(node_id, 0)
                    res.append(node)
        except Exception:
            res = []
        return StorageReadResult.from_items(res, label=label, backend=self.name)

    async def search_by_fulltext(
            self,
            label: MemoryNodeLabel,
            node_filter: NodeFilter,
            text: str,
            limit: int,
            projection: NodeProjection | None = None,
    ) -> StorageReadResult:
        self.verify_label(label)
        fulltext_idx = FULLTEXT_ANNC.get(label)
        if fulltext_idx is None:
            raise UnsupportedQueryError(self.name, label, 'fulltext')
        if not isinstance(text, str):
            raise ValueError("fulltext query must be a string")
        normalized_text = text.strip()
        if not normalized_text:
            return StorageReadResult(backend=self.name)
        predicate, filter_parameters = compile_neo4j_filter(node_filter)
        return_expression, projection_parameters = compile_neo4j_projection(
            projection,
            virtual_fields={"score": "score"},
        )
        query = f"""
        CALL db.index.fulltext.queryNodes($idx, $text) YIELD node as n, score
        WHERE {predicate}
        ORDER BY score DESC
        RETURN {return_expression}
        LIMIT $limit
        """
        async with self._session() as session:
            stmt = await session.run(
                query,
                idx=fulltext_idx,
                limit=limit,
                text=normalized_text,
                **filter_parameters,
                **projection_parameters,
            )
            records = await stmt.data()
            items = [_to_native(record["n"]) for record in records]
        return StorageReadResult.from_items(items, label=label, backend=self.name)


__all__ = [
    "EMBEDDING_SCAN_BATCH",
    "Neo4jClient",
    "configure_neo4j_manager",
]
