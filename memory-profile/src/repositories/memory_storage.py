"""Graph queries for the memory-storage module."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from sqlalchemy import select

from src.storage.outbox.exceptions import OutboxEnqueueError
from src.storage.enums import MemoryNodeType
from src.storage.outbox.types import OutboxEventInput, OutboxOperation
from src.storage.outbox.producer import enqueue_events
from src.storage.custom.end_user_delete import delete_end_user_memory_nodes
from src.infrastructure.neo4j import Neo4jManager
from src.models.references.file_metadata_model import FileMetadata

logger = logging.getLogger(__name__)

SEARCH_FOR_ALL_BATCH = """
    MATCH (n) WHERE n.end_user_id IN $end_user_ids AND n.delete_at is NULL
    RETURN 
        n.end_user_id as user_id,
        count(n) as total
    ORDER BY user_id
    """

FORGET_COUNT_ACTIVE_NODES = """
    MATCH (n {end_user_id: $end_user_id})
    WHERE n.delete_at IS NULL
      AND (n:Statement OR n:Chunk OR n:ExtractedEntity)
    RETURN count(n) AS cnt
"""

Memory_Timeline_Entity_Events = """
MATCH (e:ExtractedEntity)
WHERE elementId(e) = $id
  AND e.delete_at IS NULL
RETURN e.name AS entity_name,
       e.entity_type AS entity_type,
       e.description_summary AS description_summary,
       e.event_timeline AS event_timeline
"""

Memory_Timeline_ExtractedEntity = """
MATCH (n)-[r1]-(e)-[r2]-(ms)
WHERE elementId(n) = $id
  AND (ms:ExtractedEntity OR ms:MemorySummary)
  AND n.delete_at IS NULL
  AND e.delete_at IS NULL
  AND ms.delete_at IS NULL
  AND e.end_user_id = n.end_user_id
  AND ms.end_user_id = n.end_user_id

RETURN
  collect(
    DISTINCT
    CASE
      WHEN ms:ExtractedEntity THEN {
        text: ms.name,
        created_at: ms.created_at,
     type: "情景记忆"
      }
    END
  ) AS ExtractedEntity,

  collect(
    DISTINCT
    CASE
      WHEN ms:MemorySummary THEN {
        text: ms.content,
        created_at: ms.created_at,
       type: "长期沉淀"
      }
    END
  ) AS MemorySummary,

  collect(
    DISTINCT {
      text: e.statement,
      created_at: e.created_at,
      type: "情绪记忆"
    }
  ) AS statement;


"""

Memory_Timeline_MemorySummary = """
MATCH (n)-[r1]-(e)-[r2]-(ms)
WHERE elementId(n) =$id
  AND (ms:MemorySummary OR ms:ExtractedEntity)
  AND n.delete_at IS NULL
  AND e.delete_at IS NULL
  AND ms.delete_at IS NULL
RETURN
  collect(
    DISTINCT
    CASE
      WHEN ms:ExtractedEntity THEN {
        text: ms.name,
        created_at: ms.created_at,
        type: "情景记忆"
      }
    END
  ) AS ExtractedEntity,

  collect(
    DISTINCT
    CASE
      WHEN n:MemorySummary THEN {
        text: n.content,
        created_at: n.created_at,
        type: "长期沉淀"
      }
    END
  ) AS MemorySummary,

  collect(
    DISTINCT {
      text: e.statement,
      created_at: e.created_at,
      type: "情绪记忆"
    }
  ) AS statement;
"""

Memory_Timeline_Statement = """
MATCH (n)
WHERE elementId(n) = $id
  AND n.delete_at IS NULL

CALL (n) {
  WITH n
  MATCH (n)-[]-(m:ExtractedEntity)
  WHERE NOT m:MemorySummary AND NOT m:Chunk
    AND m.delete_at IS NULL
  RETURN collect(
    DISTINCT {
      text: m.name,
      created_at: m.created_at,
      type: "情景记忆"
    }
  ) AS ExtractedEntity
}

CALL (n) {
  WITH n
  MATCH (n)-[]-(m:MemorySummary)
  WHERE NOT m:Chunk
    AND m.delete_at IS NULL
  RETURN collect(
    DISTINCT {
      text: m.content,
      created_at: m.created_at,
       type: "长期沉淀"
    }
  ) AS MemorySummary
}

RETURN
  ExtractedEntity,
  MemorySummary,
  {
    text: n.statement,
    created_at: n.created_at,
     type: "情绪记忆"
  } AS statement;


"""

Memory_Space_Emotion_Statement = """
MATCH (n)
WHERE elementId(n) = $id
  AND n.delete_at IS NULL
RETURN
  n.emotion_intensity AS emotion_intensity,
  n.created_at        AS created_at,
  n.emotion_type      AS emotion_type,
  n.statement         AS statement;

"""

Memory_Space_Emotion_MemorySummary = """
MATCH (n)-[]-(e)
WHERE elementId(n) = $id
  AND n.delete_at IS NULL
  AND e.delete_at IS NULL
  AND EXISTS {
    MATCH (e)-[]-(ms)
    WHERE (ms:MemorySummary OR ms:ExtractedEntity)
      AND ms.delete_at IS NULL
  }
RETURN DISTINCT
  e.emotion_intensity AS emotion_intensity,
  e.created_at        AS created_at,
  e.emotion_type      AS emotion_type,
  e.statement         AS statement;
"""

Memory_Space_Emotion_ExtractedEntity = """
MATCH (n)-[]-(e)
WHERE elementId(n) = $id
  AND n.delete_at IS NULL
  AND e.delete_at IS NULL
  AND EXISTS {
    MATCH (e)-[]-(ms:ExtractedEntity)
    WHERE ms.delete_at IS NULL
  }
RETURN DISTINCT
  e.emotion_intensity AS emotion_intensity,
  e.created_at        AS created_at,
  e.emotion_type      AS emotion_type,
  e.statement         AS statement;
"""

Memory_Space_User = """
MATCH (n)-[r]->(m)
WHERE n.end_user_id = $end_user_id  AND m.name="用户"
  AND n.delete_at IS NULL
  AND m.delete_at IS NULL
return DISTINCT elementId(m) as id
"""

Memory_Space_Entity = """
MATCH (n)-[]-(m)
WHERE elementId(m) = $id AND  m.entity_type = "Person"
  AND n.delete_at IS NULL
  AND m.delete_at IS NULL
RETURN
DISTINCT m.name as name,m.end_user_id as end_user_id
"""

Memory_Space_Associative = """
MATCH (u)-[]-(x)-[]-(h)
WHERE elementId(u) = $user_id
  AND elementId(h) = $id
  AND u.delete_at IS NULL
  AND x.delete_at IS NULL
  AND h.delete_at IS NULL
RETURN DISTINCT
 x.statement as statement,x.created_at as created_at
"""


class MemoryStorageRepository:
    def __init__(self, neo4j: Neo4jManager, postgres=None, elasticsearch=None):
        self.neo4j = neo4j
        self.postgres = postgres
        self.elasticsearch = elasticsearch

    async def owner(self, element_id):
        rows = await self.neo4j.read_query(
            "MATCH (n) WHERE elementId(n) = $id AND n.delete_at IS NULL "
            "RETURN n.end_user_id AS end_user_id",
            id=element_id,
        )
        return rows[0]["end_user_id"] if rows else None

    async def events(self, element_id):
        return await self.neo4j.read_query(Memory_Timeline_Entity_Events, id=element_id)

    async def timeline(self, element_id, label):
        # 根据表类型选择查询
        if   label == 'Statement':
            # Statement只需要输入ID，使用简化查询
            results = await self.neo4j.read_query(Memory_Timeline_Statement, id=element_id)
        elif  label == 'ExtractedEntity':
            # ExtractedEntity类型查询
            results = await self.neo4j.read_query(Memory_Timeline_ExtractedEntity, id=element_id)
        else:
            # MemorySummary类型查询
            results = await self.neo4j.read_query(Memory_Timeline_MemorySummary, id=element_id)
        return results

    async def user_entities(self, end_user_id):
        return await self.neo4j.read_query(
            "MATCH (e:ExtractedEntity {end_user_id: $end_user_id}) "
            "WHERE e.delete_at IS NULL AND (e.entity_type = '用户' OR toLower(e.name) IN ['用户', '我', 'user', 'i']) "
            "RETURN elementId(e) AS element_id LIMIT 2",
            end_user_id=end_user_id,
        )

    async def emotions(self, element_id, label):
        query = {
            "Statement": Memory_Space_Emotion_Statement,
            "ExtractedEntity": Memory_Space_Emotion_ExtractedEntity,
        }.get(label, Memory_Space_Emotion_MemorySummary)
        return await self.neo4j.read_query(query, id=element_id)

    async def interaction_entity(self, element_id):
        return await self.neo4j.read_query(Memory_Space_Entity, id=element_id)

    async def interaction_user(self, end_user_id):
        return await self.neo4j.read_query(Memory_Space_User, end_user_id=end_user_id)

    async def interaction_associative(self, element_id, user_id):
        return await self.neo4j.read_query(Memory_Space_Associative, id=element_id, user_id=user_id)

    async def execute_query(self, query, **params):
        driver = await self.neo4j.driver()
        async with driver.session(database=self.neo4j.database) as session:
            result = await session.run(query, **params)
            return [record.data() async for record in result]

    async def execute_validated_write_query(self, query, validator, **params):
        driver = await self.neo4j.driver()
        async with driver.session(database=self.neo4j.database) as session:

            async def execute(tx):
                result = await tx.run(query, **params)
                rows = [record.data() async for record in result]
                return validator(rows)

            return await session.execute_write(execute)

    async def enqueue_many(self, events):
        import sqlalchemy as sa
        from sqlalchemy.dialects.postgresql import insert

        from src.common.datetime_utils import utcnow_naive

        table = sa.table(
            "memory_storage_outbox_events",
            sa.column("id", sa.UUID),
            sa.column("label"),
            sa.column("node_id"),
            sa.column("operation"),
            sa.column("created_at"),
            sa.column("updated_at"),
        )
        now = utcnow_naive()
        async with self.postgres.session() as session:
            for offset in range(0, len(events), 1000):
                chunk = events[offset : offset + 1000]
                await session.execute(
                    insert(table)
                    .values(
                        [
                            dict(
                                id=e.id,
                                label=e.label.value,
                                node_id=e.node_id,
                                operation=e.operation.value,
                                created_at=now,
                                updated_at=now,
                            )
                            for e in chunk
                        ]
                    )
                    .on_conflict_do_nothing(index_elements=[table.c.id])
                )
                rows = (
                    (
                        await session.execute(
                            sa.select(
                                table.c.id, table.c.label, table.c.node_id, table.c.operation
                            ).where(table.c.id.in_([e.id for e in chunk]))
                        )
                    )
                    .mappings()
                    .all()
                )
                expected = {e.id: (e.label.value, e.node_id, e.operation.value) for e in chunk}
                if len(rows) != len(expected) or any(
                    expected[row["id"]] != (row["label"], row["node_id"], row["operation"])
                    for row in rows
                ):
                    raise RuntimeError("Outbox idempotency verification failed")
            await session.commit()
        return [e.id for e in events]

    async def delete_user_graph(self, end_user_id):
        return await delete_end_user_memory_nodes(end_user_id, client=self, outbox_repository=self)

    async def delete_user_rag(self, end_user_id):
        client = await self.elasticsearch.client()
        result = await client.delete_by_query(
            index="rag_memory_current",
            query={"term": {"end_user_id": end_user_id}},
            conflicts="proceed",
            refresh=True,
            slices="auto",
        )
        if (
            result.get("timed_out")
            or result.get("failures")
            or int(result.get("deleted", 0)) < int(result.get("total", 0))
        ):
            raise RuntimeError("RAG deletion incomplete")
        return int(result.get("deleted", 0))

    async def deletion_workspace(self, end_user_id):
        from src.models.references import EndUser, Workspace

        async with self.postgres.session() as session:
            return (await session.execute(
                select(Workspace.id, Workspace.storage_type)
                .join(EndUser, EndUser.workspace_id == Workspace.id)
                .where(EndUser.id == end_user_id)
            )).first()

    async def finalize_user_delete(self, user_id, workspace_id):
        import sqlalchemy as sa

        from src.models.references import EndUser

        async with self.postgres.session() as session:
            result = await session.execute(
                sa.update(EndUser)
                .where(EndUser.id == user_id, EndUser.workspace_id == workspace_id)
                .values(memory_count=0, is_active=False)
            )
            await session.commit()
            return bool(result.rowcount)

    async def manual_delete(self, element_id, end_user_id, operator):
        from uuid import UUID

        rows = await self.execute_query(
            "MATCH (n) WHERE elementId(n) = $id AND n.end_user_id = $owner "
            "AND any(label IN labels(n) WHERE label IN $labels) "
            "RETURN n.id AS node_id, labels(n) AS labels, "
            "n.content AS content, n.statement AS statement, n.text AS text, "
            "n.name AS name, n.preference_text_all AS preference_text_all",
            id=element_id,
            owner=end_user_id,
            labels=[label.value for label in MemoryNodeType],
        )
        if not rows:
            return False
        if len(rows) != 1:
            raise ValueError("elementId resolved to multiple memory nodes")
        target = rows[0]
        labels = [
            label
            for label in target["labels"]
            if label in {label.value for label in MemoryNodeType}
        ]
        if len(labels) != 1:
            raise ValueError("memory node must have exactly one supported storage label")
        if target["node_id"] is None or not str(target["node_id"]).strip():
            raise ValueError("memory node is missing its business id")
        label = MemoryNodeType(labels[0])
        node_id = str(target["node_id"])
        content = str(
            target.get("content")
            or target.get("statement")
            or target.get("text")
            or target.get("name")
            or target.get("preference_text_all")
            or ""
        )

        def validate(rows):
            if len(rows) > 1 or (rows and rows[0]["node_id"] != node_id):
                raise RuntimeError("Manual deletion returned unexpected identity")
            return rows

        deleted = await self.execute_validated_write_query(
            f"MATCH (n:`{label.value}`) WHERE n.id = $node_id AND n.end_user_id = $owner "
            "WITH n, n.id AS node_id DETACH DELETE n RETURN node_id",
            validate,
            node_id=node_id,
            owner=end_user_id,
        )
        if not deleted:
            return False
        event = OutboxEventInput(label=label, node_id=node_id, operation=OutboxOperation.DELETE)
        try:
            await enqueue_events([event], repository=self)
        except OutboxEnqueueError:
            try:
                await self.write_delete_audit(
                    element_id, UUID(end_user_id), label.value, content, operator
                )
            except Exception:
                logger.exception(
                    "Audit persistence failed after committed deletion and Outbox failure"
                )
            raise
        await self.write_delete_audit(element_id, UUID(end_user_id), label.value, content, operator)
        return True

    async def write_delete_audit(self, element_id, user_id, label, content, operator):
        from uuid import uuid4

        import sqlalchemy as sa

        from src.common.datetime_utils import utcnow_naive

        table = sa.table(
            "memory_forget_log",
            sa.column("id", sa.UUID),
            sa.column("end_user_id", sa.UUID),
            sa.column("node_id"),
            sa.column("node_type"),
            sa.column("content"),
            sa.column("trigger"),
            sa.column("reason"),
            sa.column("recoverable"),
            sa.column("operator", sa.UUID),
            sa.column("delete_at"),
            sa.column("is_recovered"),
        )
        async with self.postgres.session() as session:
            await session.execute(
                sa.insert(table).values(
                    id=uuid4(),
                    end_user_id=user_id,
                    node_id=element_id,
                    node_type=label,
                    content=content,
                    trigger=1,
                    reason="manual",
                    recoverable=False,
                    operator=operator,
                    delete_at=utcnow_naive(),
                    is_recovered=False,
                )
            )
            await session.commit()

    async def sync_count(self, user_id):
        import sqlalchemy as sa

        from src.models.references import EndUser

        result = await self.neo4j.read_query(
            SEARCH_FOR_ALL_BATCH, end_user_ids=[str(user_id)]
        )
        node_count = int(result[0]["total"]) if result else 0
        result = await self.neo4j.read_query(
            FORGET_COUNT_ACTIVE_NODES, end_user_id=str(user_id)
        )
        memory_node_count = result[0]["cnt"] if result else 0
        async with self.postgres.session() as session:
            await session.execute(
                sa.update(EndUser).where(EndUser.id == user_id).values(memory_count=node_count)
            )
            await session.commit()
        return node_count, memory_node_count

    async def metadata(self, file_id, *, workspace_id=None, tenant_id=None, completed_only=True):
        if file_id is None:
            return None
        statement = select(*FileMetadata.__table__.c).where(FileMetadata.id == file_id)
        if completed_only:
            statement = statement.where(FileMetadata.status == "completed")
        if workspace_id is not None:
            statement = statement.where(FileMetadata.workspace_id == workspace_id)
        if tenant_id is not None:
            statement = statement.where(FileMetadata.tenant_id == tenant_id)
        async with self.postgres.session() as session:
            row = (await session.execute(statement)).mappings().one_or_none()
            return SimpleNamespace(**row) if row is not None else None

    async def assistant_aliases(self, end_user_id, names):
        try:
            rows = await self.neo4j.read_query(
                "MATCH (e:ExtractedEntity) WHERE e.end_user_id = $end_user_id AND e.name IN $names RETURN e.aliases AS aliases",
                end_user_id=end_user_id,
                names=names,
            )
            return {alias.strip().lower() for row in rows for alias in (row.get("aliases") or [])}
        except Exception:
            logger.warning("Failed to load assistant aliases", exc_info=True)
            return set()
