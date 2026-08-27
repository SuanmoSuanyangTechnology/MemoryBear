import asyncio
import heapq
import traceback
from typing import Any, Self

import numpy as np
from neo4j import AsyncGraphDatabase, AsyncDriver
from neo4j.graph import Node, Relationship

from app.core.memory.storage.enums import (
    BackendType,
    MemoryNodeLabel,
    MemoryRelationshipType,
)
from app.core.memory.storage.exceptions import UnsupportedQueryError
from app.core.memory.storage.models import (
    NodeFilter,
    NodeProjection,
    NodeSort,
    StorageReadResult,
    StorageWriteResult,
)
from app.core.memory.storage.provider.base import BaseClient
from app.core.memory.storage.provider.neo4j.compiler.filter_compiler import compile_neo4j_filter
from app.core.memory.storage.provider.neo4j.compiler.projection_compiler import (
    compile_neo4j_projection,
)
from app.core.memory.storage.provider.neo4j.compiler.sort_compiler import compile_neo4j_sort
from app.core.memory.storage.provider.neo4j.config import build_neo4j_driver_config
from app.core.memory.storage.provider.neo4j.index.definitions import FULLTEXT_ANNC, EMBEDDING_FIELDS
from app.core.memory.storage.utils.similarity import compute_cosine_similarity


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
        async with self.client.session() as session:
            stmt = await session.run("return 1")
            await stmt.consume()

    async def connect(self) -> AsyncDriver:
        return AsyncGraphDatabase.driver(**build_neo4j_driver_config())

    async def close(self):
        if self.client:
            await self.client.close()

    async def save_node(
            self,
            label: MemoryNodeLabel,
            data: dict,
    ) -> StorageWriteResult:
        node_id = self.verify_input(label, data)
        async with self.client.session() as session:
            query = f"""
            MERGE (n:{label.value} {{id:$id}})
            SET n = $properties
            RETURN n
            """
            stmt = await session.run(query, id=node_id, properties=data)
            records = await stmt.data()

        items = [
            _to_native(record["n"])
            for record in records
            if "n" in record
        ]
        return StorageWriteResult(
            backend=self.name,
            affected_count=len(items),
            ids=[str(node_id)] if items else [],
            data=items,
        )

    async def save_relationship(
            self,
            relationship_type: MemoryRelationshipType,
            source: str,
            target: str,
            data: dict,
    ) -> StorageWriteResult:
        if not isinstance(relationship_type, MemoryRelationshipType):
            raise KeyError(
                f"relationship type - {relationship_type} not supported"
            )

        relationship_id = data.get("id")
        if relationship_id is None:
            raise ValueError("Relationship id field is required")

        escaped_type = relationship_type.value.replace("`", "``")
        query = f"""
        MATCH (source {{id: $source}})
        MATCH (target {{id: $target}})
        MERGE (source)-[r:`{escaped_type}` {{id: $id}}]->(target)
        SET r = $properties
        RETURN r
        """

        async with self.client.session() as session:
            stmt = await session.run(
                query,
                id=relationship_id,
                source=source,
                target=target,
                properties=data,
            )
            records = await stmt.data()

        items = [
            _to_native(record["r"])
            for record in records
            if "r" in record
        ]
        return StorageWriteResult(
            backend=self.name,
            affected_count=len(items),
            ids=[str(relationship_id)] if items else [],
            data=items,
        )

    async def update_node(
            self,
            label: MemoryNodeLabel,
            data: dict,
            node_filter: NodeFilter,
    ) -> StorageWriteResult:
        self.verify_label(label)
        predicate, filter_parameters = compile_neo4j_filter(node_filter)
        query = f"""
        MATCH (n:{label.value})
        WHERE {predicate}
        SET n += $properties
        RETURN n
        """
        parameters = {"properties": data, **filter_parameters}

        async with self.client.session() as session:
            stmt = await session.run(query, **parameters)
            records = await stmt.data()

        items = [
            _to_native(record["n"])
            for record in records
            if "n" in record
        ]
        return StorageWriteResult(
            backend=self.name,
            affected_count=len(items),
            ids=[str(item["id"]) for item in items if "id" in item],
            data=items,
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
        async with self.client.session() as session:
            stmt = await session.run(query, **parameters)
            records = await stmt.data()
            items = [_to_native(record["n"]) for record in records]
        return StorageReadResult.from_items(items, backend=self.name)

    async def delete_node(
            self,
            label: MemoryNodeLabel,
            node_filter: NodeFilter,
            draft: bool = False,
    ) -> StorageWriteResult:
        self.verify_label(label)
        predicate, filter_parameters = compile_neo4j_filter(node_filter)
        if draft:
            query = f"""
            MATCH (n:{label.value})
            WHERE ({predicate}) AND n.delete_at IS NULL
            SET n.delete_at = datetime()
            RETURN count(n) AS deleted
            """
        else:
            query = f"""
            MATCH (n:{label.value})
            WHERE {predicate}
            DETACH DELETE n
            RETURN count(n) AS deleted
            """

        async with self.client.session() as session:
            stmt = await session.run(query, **filter_parameters)
            records = await stmt.data()

        affected_count = (
            int(records[0].get("deleted", 0))
            if records
            else 0
        )
        return StorageWriteResult(
            backend=self.name,
            affected_count=affected_count,
        )

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
        LIMIT 1000
        """

        top_heap: list[tuple[float, str]] = []
        last_id = ""
        while 1:
            try:
                async with self.client.session() as session:
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

                if len(batch) < 1000:
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
            async with self.client.session() as session:
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
        return StorageReadResult.from_items(res, backend=self.name)

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
        async with self.client.session() as session:
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
        return StorageReadResult.from_items(items, backend=self.name)


# async def dev():
#     from app.core.memory.storage.models import FilterCondition
#     client = await Neo4jClient.create()
#     res = await client.search_by_fulltext(
#         label=MemoryNodeType.EXTRACTED_ENTITY,
#         node_filter=NodeFilter(
#             conditions=(FilterCondition(
#                 field="name",
#                 operator=FilterOperator.EQ,
#                 value="用户"
#             ),),
#         ),
#         projection=NodeProjection.of("id", "end_user_id", 'score',CoalesceProjectionField(
#             fields=('time',),
#             alias="用户",
#             default=666,
#         )),
#         text="用户",
#         limit=10,
#     )
#     print(res)
#
#
# if __name__ == '__main__':
#     asyncio.run(dev())
