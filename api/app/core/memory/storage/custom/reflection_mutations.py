"""Layer2 反思的 custom 写链：显式事务 + 精确的 Outbox 事件身份。

每个 mutation 提交一次 Cypher 事务，只为该事务实际改动的节点发布投影事件；
事件身份内联在查询的 ``RETURN`` 里（``affected_count`` + ``affected_nodes``），
在 commit 之前校验，物理删除的节点也能拿到正确的 DELETE 身份。

纯关系写走 ``_relationship_write``，提交但不发事件。

别名归并的三步（属性合并 / 边重定向 / 删别名节点）各自一个独立事务，
失败隔离由 ``deterministic/alias_merger.py`` 负责。
"""
from __future__ import annotations

import logging
from typing import Any

from app.core.memory.storage.enums import MemoryNodeType
from app.core.memory.storage.outbox.producer import enqueue_events
from app.core.memory.storage.outbox.repository import OutboxRepository
from app.core.memory.storage.outbox.types import OutboxEventInput, OutboxOperation
from app.core.memory.storage.provider.neo4j.client import Neo4jClient

logger = logging.getLogger(__name__)


def _events_from_rows(rows: list[dict[str, Any]]) -> list[OutboxEventInput]:
    affected = [node for row in rows for node in row["affected_nodes"]]
    count = sum(row["affected_count"] for row in rows)
    identities = {(node["label"], node["node_id"]) for node in affected}
    if count != len(affected) or count != len(identities):
        raise RuntimeError("Reflection mutation affected identity count mismatch")
    if any(not isinstance(node["node_id"], str) or not node["node_id"].strip() for node in affected):
        raise RuntimeError("Reflection mutation returned a missing node id")
    # Stable ordering includes batches whose Cypher aggregation has no row order.
    affected.sort(key=lambda node: (node["operation"] != "UPSERT", node["label"], node["node_id"]))
    return [OutboxEventInput(
        label=MemoryNodeType(node["label"]),
        node_id=node["node_id"],
        operation=OutboxOperation[node["operation"]],
    ) for node in affected]


async def _write(
    query: str,
    parameters: dict[str, Any],
    *,
    neo4j_client: Neo4jClient | None = None,
    outbox_repository: OutboxRepository | None = None,
) -> list[dict[str, Any]]:
    # 参数由 Layer2Inspector 从任务入参透传，此处不判空：租户参数为空时
    # {end_user_id: $end_user_id} 匹配不到任何节点，退化为 no-op。
    client = neo4j_client or await Neo4jClient.create()
    try:
        if client.client is None:
            raise RuntimeError("Neo4jClient is not initialized")
        # Validate while rollback is still possible. Never re-MATCH after commit.
        async with client.client.session() as session, await session.begin_transaction() as transaction:
            result = await transaction.run(query, **parameters)
            rows = await result.data()
            events = _events_from_rows(rows)
            await transaction.commit()
        # Publish before closing an owned client, so close failures cannot lose events.
        try:
            await enqueue_events(events, repository=outbox_repository)
        except Exception as exc:
            logger.error(
                f"反思写链 Neo4j 已提交但 Outbox 入队失败 "
                f"user={parameters.get('end_user_id', '(查询未按用户过滤，见 affected_nodes)')} "
                f"affected_nodes={[(e.label.value, e.node_id, e.operation.name) for e in events]} "
                f"reason={exc}",
                exc_info=True,
            )
        return rows
    finally:
        if neo4j_client is None:
            try:
                await client.close()
            except Exception:
                logger.warning("Closing reflection storage client failed", exc_info=True)


async def patch_entity_metadata(parameters: dict[str, Any], **dependencies) -> list[dict[str, Any]]:
    """原子 patch 八个 metadata 列表字段，按业务 ``entity_id`` 定位。

    ``parameters`` 由 ``extract_metadata_service._build_patch_params`` 构造，含
    ``entity_id`` 与每个字段的 ``_add`` / ``_delete`` / ``_update`` 三组操作。
    全部操作为空时直接返回，不建连接、不发无变化的 UPSERT 事件。
    """
    if not any(value for key, value in parameters.items() if key.endswith(("_add", "_delete", "_update"))):
        return []
    return await _write(PATCH_METADATA, parameters, **dependencies)


async def merge_entities(end_user_id: str, keeper_id: str, loser_id: str,
                         merged_name: str, merged_aliases: list[str], **dependencies) -> bool:
    """一个事务内合并 keeper 与 loser 的属性、迁移 loser 的边、物理删除 loser。

    返回 keeper 的 UPSERT 与 loser 的 DELETE 两条事件。

    自合并（``keeper_id == loser_id``）由三条配对来源各自排除：高频的
    ``DEDUP_CANDIDATES_BY_NAME`` / ``_BY_EMBED`` 带 ``elementId(e1) < elementId(e2)``，
    低频 LLM 判定与同名直合在 ``Layer2Inspector`` 内 ``continue`` 掉。真走到这里也是
    安全的——两条事件身份重复，``_events_from_rows`` 会在 commit 前抛错并回滚。
    """
    rows = await _write(MERGE_ENTITIES, {"end_user_id": end_user_id, "keeper_id": keeper_id,
                        "loser_id": loser_id, "merged_name": merged_name, "merged_aliases": merged_aliases}, **dependencies)
    return bool(rows)


async def create_unresolved_entity(parameters: dict[str, Any], **dependencies) -> list[dict[str, Any]]:
    return await _write(CREATE_UNRESOLVED_ENTITY, parameters, **dependencies)


async def append_user_info(end_user_id: str, description: str, **dependencies) -> list[dict[str, Any]]:
    if not description:
        return []
    return await _write(APPEND_USER_INFO, {"end_user_id": end_user_id, "description": description}, **dependencies)


async def resolve_statement(statement_id: str, **dependencies) -> bool:
    """把 Statement 的 ``has_unsolved_reference`` 置为 false，按业务 ``statement_id`` 定位。"""
    rows = await _write(RESOLVE_STATEMENT, {"statement_id": statement_id}, **dependencies)
    return bool(rows)


async def merge_alias_properties(end_user_id: str, alias_ids: list[str], **dependencies) -> int:
    """别名归并第 1 步：source.name → target.aliases、source.description → target.description。

    三步（本步、边重定向、删别名节点）各自一个事务，单步失败由
    ``alias_merger.merge_alias_belongs_to`` 隔离，不回滚其它步骤。发 target 的 UPSERT 事件。

    Returns:
        受影响的 target 数量。
    """
    if not alias_ids:
        return 0
    rows = await _write(
        MERGE_ALIAS_PROPERTIES,
        {"end_user_id": end_user_id, "alias_ids": sorted(set(alias_ids))},
        **dependencies,
    )
    return int(rows[0]["alias_merged"]) if rows else 0


async def delete_alias_nodes(end_user_id: str, alias_ids: list[str], **dependencies) -> int:
    """别名归并第 3 步：DETACH DELETE 别名节点。发 alias 的 DELETE 事件。

    事件身份在 ``DETACH DELETE`` 之前构造：节点删除后 ``a.id`` 已不可读。

    Returns:
        被删除的别名节点数量。
    """
    if not alias_ids:
        return 0
    rows = await _write(
        DELETE_ALIAS_NODES,
        {"end_user_id": end_user_id, "alias_ids": sorted(set(alias_ids))},
        **dependencies,
    )
    return int(rows[0]["alias_nodes_deleted"]) if rows else 0


async def _relationship_write(
    query: str,
    parameters: dict[str, Any],
    *,
    neo4j_client: Neo4jClient | None = None,
) -> list[dict[str, Any]]:
    """Commit a relationship-only reflection mutation through storage Neo4j.

    Relationship writes do not create projection events by themselves; the two
    endpoint nodes are projected by their own UPSERT events. The caller still uses
    the storage-owned client so that no reflection mutation bypasses the storage
    boundary. Parameters are passed through as-is.
    """
    client = neo4j_client or await Neo4jClient.create()
    try:
        if client.client is None:
            raise RuntimeError("Neo4jClient is not initialized")
        async with client.client.session() as session:
            transaction = await session.begin_transaction()
            try:
                result = await transaction.run(query, **parameters)
                rows = await result.data()
                await transaction.commit()
                return rows
            except BaseException:
                await transaction.rollback()
                raise
    finally:
        if neo4j_client is None:
            await client.close()


async def create_unresolved_relationship(
    parameters: dict[str, Any],
    *,
    neo4j_client: Neo4jClient | None = None,
) -> list[dict[str, Any]]:
    return await _relationship_write(
        CREATE_UNRESOLVED_RELATIONSHIP,
        parameters,
        neo4j_client=neo4j_client,
    )


async def create_unresolved_statement_entity_edge(
    parameters: dict[str, Any],
    *,
    neo4j_client: Neo4jClient | None = None,
) -> list[dict[str, Any]]:
    return await _relationship_write(
        CREATE_UNRESOLVED_STATEMENT_ENTITY_EDGE,
        parameters,
        neo4j_client=neo4j_client,
    )


async def redirect_alias_edges(
    end_user_id: str,
    alias_ids: list[str],
    *,
    neo4j_client: Neo4jClient | None = None,
) -> int:
    """别名归并第 2 步：别名节点上「别名属于」以外的边重定向到 target。

    纯关系写，不发节点事件；被重定向的两端节点属性未变，其状态由第 1、3 步的
    节点事件覆盖。四段各自一个 ``CALL () {}`` 子查询，空输入时不会让后续段被跳过。

    Returns:
        重定向的边总数（入边 + 出边 + Statement 边）。
    """
    if not alias_ids:
        return 0
    rows = await _relationship_write(
        REDIRECT_ALIAS_EDGES,
        {"end_user_id": end_user_id, "alias_ids": sorted(set(alias_ids))},
        neo4j_client=neo4j_client,
    )
    if not rows:
        return 0
    row = rows[0]
    return (
        int(row.get("redirected_incoming") or 0)
        + int(row.get("redirected_outgoing") or 0)
        + int(row.get("redirected_stmt") or 0)
    )


async def drop_alias_belongs_edges(
    end_user_id: str,
    drop_alias_ids: list[str],
    *,
    neo4j_client: Neo4jClient | None = None,
) -> int:
    if not drop_alias_ids:
        return 0
    rows = await _relationship_write(
        DROP_ALIAS_BELONGS_EDGES,
        {"end_user_id": end_user_id, "drop_alias_ids": sorted(set(drop_alias_ids))},
        neo4j_client=neo4j_client,
    )
    return int(rows[0].get("dropped_count", 0) or 0) if rows else 0


DROP_ALIAS_BELONGS_EDGES = """
MATCH (alias:ExtractedEntity {end_user_id: $end_user_id})
      -[r:EXTRACTED_RELATIONSHIP {predicate: '别名属于'}]->
      (target:ExtractedEntity {end_user_id: $end_user_id})
WHERE alias.id IN $drop_alias_ids
  AND alias.delete_at IS NULL
  AND target.delete_at IS NULL
DELETE r
RETURN count(r) AS dropped_count
"""

PATCH_METADATA = """
MATCH (e:ExtractedEntity {id: $entity_id})
WHERE e.delete_at IS NULL
// ── core_facts ──
WITH e,
     [x IN coalesce(e.core_facts, []) WHERE NOT x IN $core_facts_delete] AS cf0
WITH e, reduce(acc = cf0, pair IN $core_facts_update |
        [x IN acc | CASE WHEN x = pair.old THEN pair.new ELSE x END]) AS cf1
WITH e, reduce(acc = cf1, item IN $core_facts_add |
        CASE WHEN item IN acc THEN acc ELSE acc + item END) AS cf2
SET e.core_facts = cf2
// ── traits ──
WITH e,
     [x IN coalesce(e.traits, []) WHERE NOT x IN $traits_delete] AS tr0
WITH e, reduce(acc = tr0, pair IN $traits_update |
        [x IN acc | CASE WHEN x = pair.old THEN pair.new ELSE x END]) AS tr1
WITH e, reduce(acc = tr1, item IN $traits_add |
        CASE WHEN item IN acc THEN acc ELSE acc + item END) AS tr2
SET e.traits = tr2
// ── relations ──
WITH e,
     [x IN coalesce(e.relations, []) WHERE NOT x IN $relations_delete] AS re0
WITH e, reduce(acc = re0, pair IN $relations_update |
        [x IN acc | CASE WHEN x = pair.old THEN pair.new ELSE x END]) AS re1
WITH e, reduce(acc = re1, item IN $relations_add |
        CASE WHEN item IN acc THEN acc ELSE acc + item END) AS re2
SET e.relations = re2
// ── goals ──
WITH e,
     [x IN coalesce(e.goals, []) WHERE NOT x IN $goals_delete] AS go0
WITH e, reduce(acc = go0, pair IN $goals_update |
        [x IN acc | CASE WHEN x = pair.old THEN pair.new ELSE x END]) AS go1
WITH e, reduce(acc = go1, item IN $goals_add |
        CASE WHEN item IN acc THEN acc ELSE acc + item END) AS go2
SET e.goals = go2
// ── interests ──
WITH e,
     [x IN coalesce(e.interests, []) WHERE NOT x IN $interests_delete] AS in0
WITH e, reduce(acc = in0, pair IN $interests_update |
        [x IN acc | CASE WHEN x = pair.old THEN pair.new ELSE x END]) AS in1
WITH e, reduce(acc = in1, item IN $interests_add |
        CASE WHEN item IN acc THEN acc ELSE acc + item END) AS in2
SET e.interests = in2
// ── beliefs_or_stances ──
WITH e,
     [x IN coalesce(e.beliefs_or_stances, []) WHERE NOT x IN $beliefs_or_stances_delete] AS be0
WITH e, reduce(acc = be0, pair IN $beliefs_or_stances_update |
        [x IN acc | CASE WHEN x = pair.old THEN pair.new ELSE x END]) AS be1
WITH e, reduce(acc = be1, item IN $beliefs_or_stances_add |
        CASE WHEN item IN acc THEN acc ELSE acc + item END) AS be2
SET e.beliefs_or_stances = be2
// ── anchors ──
WITH e,
     [x IN coalesce(e.anchors, []) WHERE NOT x IN $anchors_delete] AS an0
WITH e, reduce(acc = an0, pair IN $anchors_update |
        [x IN acc | CASE WHEN x = pair.old THEN pair.new ELSE x END]) AS an1
WITH e, reduce(acc = an1, item IN $anchors_add |
        CASE WHEN item IN acc THEN acc ELSE acc + item END) AS an2
SET e.anchors = an2
// ── events ──
WITH e,
     [x IN coalesce(e.events, []) WHERE NOT x IN $events_delete] AS ev0
WITH e, reduce(acc = ev0, pair IN $events_update |
        [x IN acc | CASE WHEN x = pair.old THEN pair.new ELSE x END]) AS ev1
WITH e, reduce(acc = ev1, item IN $events_add |
        CASE WHEN item IN acc THEN acc ELSE acc + item END) AS ev2
SET e.events = ev2
RETURN 1 AS affected_count,
       [{label: 'ExtractedEntity', node_id: e.id, operation: 'UPSERT'}] AS affected_nodes,
       e.id AS uuid,
       e.core_facts AS core_facts,
       e.traits AS traits,
       e.relations AS relations,
       e.goals AS goals,
       e.interests AS interests,
       e.beliefs_or_stances AS beliefs_or_stances,
       e.anchors AS anchors,
       e.events AS events
"""

MERGE_ENTITIES = """
MATCH (keeper:ExtractedEntity {id: $keeper_id, end_user_id: $end_user_id})
WHERE keeper.delete_at IS NULL
MATCH (loser:ExtractedEntity {id: $loser_id, end_user_id: $end_user_id})
WHERE loser.delete_at IS NULL
SET keeper.name = $merged_name,
    keeper.aliases = $merged_aliases,
    keeper.description = CASE
      WHEN coalesce(keeper.description, '') = '' THEN coalesce(loser.description, '')
      WHEN coalesce(loser.description, '') = '' THEN coalesce(keeper.description, '')
      ELSE keeper.description + '；' + loser.description
    END,
    keeper.connect_strength = CASE
      WHEN keeper.connect_strength = 'both' OR loser.connect_strength = 'both' THEN 'both'
      WHEN keeper.connect_strength <> loser.connect_strength THEN 'both'
      ELSE coalesce(keeper.connect_strength, loser.connect_strength, 'weak')
    END,
    keeper.importance_score = CASE
      WHEN coalesce(loser.importance_score, 0) > coalesce(keeper.importance_score, 0)
      THEN loser.importance_score ELSE keeper.importance_score END,
    keeper.access_count = coalesce(keeper.access_count, 0) + coalesce(loser.access_count, 0),
    keeper.extraction_count = coalesce(keeper.extraction_count, 1) + coalesce(loser.extraction_count, 1),
    keeper.created_at = CASE
      WHEN keeper.created_at IS NULL THEN loser.created_at
      WHEN loser.created_at IS NULL THEN keeper.created_at
      WHEN loser.created_at > keeper.created_at THEN loser.created_at
      ELSE keeper.created_at END,
    keeper.core_facts = apoc.coll.toSet(coalesce(keeper.core_facts,[]) + coalesce(loser.core_facts,[])),
    keeper.traits = apoc.coll.toSet(coalesce(keeper.traits,[]) + coalesce(loser.traits,[])),
    keeper.relations = apoc.coll.toSet(coalesce(keeper.relations,[]) + coalesce(loser.relations,[])),
    keeper.goals = apoc.coll.toSet(coalesce(keeper.goals,[]) + coalesce(loser.goals,[])),
    keeper.interests = apoc.coll.toSet(coalesce(keeper.interests,[]) + coalesce(loser.interests,[])),
    keeper.beliefs_or_stances = apoc.coll.toSet(coalesce(keeper.beliefs_or_stances,[]) + coalesce(loser.beliefs_or_stances,[])),
    keeper.anchors = apoc.coll.toSet(coalesce(keeper.anchors,[]) + coalesce(loser.anchors,[])),
    keeper.events = apoc.coll.toSet(coalesce(keeper.events,[]) + coalesce(loser.events,[]))
WITH keeper, loser
OPTIONAL MATCH (s:Statement)-[r:REFERENCES_ENTITY]->(loser)
WHERE s.delete_at IS NULL AND NOT (s)-[:REFERENCES_ENTITY]->(keeper)
FOREACH (_ IN CASE WHEN r IS NOT NULL THEN [1] ELSE [] END |
  CREATE (s)-[:REFERENCES_ENTITY]->(keeper)
)
WITH DISTINCT keeper, loser
OPTIONAL MATCH (loser)-[r:EXTRACTED_RELATIONSHIP]->(target)
WHERE target <> keeper
FOREACH (_ IN CASE WHEN r IS NOT NULL THEN [1] ELSE [] END |
  MERGE (keeper)-[nr:EXTRACTED_RELATIONSHIP {predicate: r.predicate}]->(target)
  SET nr += properties(r)
)
WITH DISTINCT keeper, loser
OPTIONAL MATCH (source)-[r:EXTRACTED_RELATIONSHIP]->(loser)
WHERE source <> keeper
FOREACH (_ IN CASE WHEN r IS NOT NULL THEN [1] ELSE [] END |
  MERGE (source)-[nr:EXTRACTED_RELATIONSHIP {predicate: r.predicate}]->(keeper)
  SET nr += properties(r)
)
WITH DISTINCT keeper, loser,
     [{label: 'ExtractedEntity', node_id: keeper.id, operation: 'UPSERT'},
      {label: 'ExtractedEntity', node_id: loser.id, operation: 'DELETE'}] AS affected_nodes
DETACH DELETE loser
RETURN keeper.id AS merged_id, 2 AS affected_count, affected_nodes
"""

CREATE_UNRESOLVED_ENTITY = """
MERGE (e:ExtractedEntity {
  end_user_id: $end_user_id,
  name: $name,
  entity_type: $entity_type
})
ON CREATE SET
  e.delete_at = null,
  e.id = randomUUID(),
  e.description = $description,
  e.example = "",
  e.statement_id = $statement_id,
  e.aliases = [],
  e.connect_strength = "weak",
  e.source = "reflection_unresolved",
  e.run_id = $run_id,
  e.type_id = $type_id,
  e.type_description = $type_description,
  e.entity_idx = $entity_idx,
  e.importance_score = 0.5,
  e.activation_value = null,
  e.access_history = [],
  e.access_count = 0,
  e.last_access_time = null,
  e.is_explicit_memory = $is_explicit_memory,
  e.created_at = $created_at,
  e.extraction_count = 1
ON MATCH SET
  e.delete_at = null,
  e.description = CASE
    WHEN e.description IS NULL OR e.description = "" THEN $description
    ELSE e.description + '；' + $description
  END,
  e.extraction_count = coalesce(e.extraction_count, 1) + 1,
  e.created_at = CASE
    WHEN e.created_at IS NULL THEN $created_at
    WHEN $created_at IS NULL THEN e.created_at
    WHEN $created_at > e.created_at THEN $created_at
    ELSE e.created_at END
RETURN e.id AS entity_id, e.name AS name, 1 AS affected_count,
       [{label: 'ExtractedEntity', node_id: e.id, operation: 'UPSERT'}] AS affected_nodes
"""

APPEND_USER_INFO = """
MATCH (e:ExtractedEntity {end_user_id: $end_user_id, entity_type: '用户'})
WHERE e.delete_at IS NULL
SET e.description = CASE
    WHEN $description IS NULL OR $description = '' THEN e.description
    WHEN e.description IS NULL OR e.description = '' THEN $description
    ELSE e.description + '；' + $description
END
RETURN e.id AS entity_id, 1 AS affected_count,
       [{label: 'ExtractedEntity', node_id: e.id, operation: 'UPSERT'}] AS affected_nodes
"""

RESOLVE_STATEMENT = """
MATCH (s:Statement {id: $statement_id})
WHERE s.delete_at IS NULL
SET s.has_unsolved_reference = false
RETURN s.id AS statement_id, 1 AS affected_count,
       [{label: 'Statement', node_id: s.id, operation: 'UPSERT'}] AS affected_nodes
"""

CREATE_UNRESOLVED_RELATIONSHIP = """
MATCH (subj:ExtractedEntity {end_user_id: $end_user_id, name: $subject_name})
WHERE subj.delete_at IS NULL
MATCH (obj:ExtractedEntity {end_user_id: $end_user_id, name: $object_name})
WHERE obj.delete_at IS NULL
CREATE (subj)-[r:EXTRACTED_RELATIONSHIP {
  predicate: $predicate,
  predicate_id: $predicate_id,
  predicate_surface: $predicate_surface,
  predicate_description: $predicate_description,
  statement_id: $statement_id,
  valid_at: $valid_at,
  invalid_at: $invalid_at,
  end_user_id: $end_user_id,
  run_id: $run_id,
  connect_strength: "weak",
  source: "reflection_unresolved",
  created_at: $created_at
}]->(obj)
RETURN count(r) AS relationship_count
"""

CREATE_UNRESOLVED_STATEMENT_ENTITY_EDGE = """
MATCH (s:Statement {id: $statement_id})
WHERE s.delete_at IS NULL
MATCH (e:ExtractedEntity {end_user_id: $end_user_id, name: $entity_name})
WHERE e.delete_at IS NULL
MERGE (s)-[r:REFERENCES_ENTITY]->(e)
SET r.end_user_id = $end_user_id,
    r.run_id = $run_id,
    r.created_at = $created_at,
    r.connect_strength = "weak"
RETURN count(r) AS relationship_count
"""

# 别名归并第 1 步：source.name 进 target.aliases、source.description 拼入 target.description。
# 与第 2、3 步分属三个独立事务，单步失败不影响其余两步。
MERGE_ALIAS_PROPERTIES = """
// 先按 target 分组，将所有 source.name 和 source.description 聚合，
// 再一次性 SET，避免多条 别名属于 边对同一 target 反复覆盖。
MATCH (source:ExtractedEntity {end_user_id: $end_user_id})-[r:EXTRACTED_RELATIONSHIP]->(target:ExtractedEntity {end_user_id: $end_user_id})
WHERE r.predicate = '别名属于' AND source.id IN $alias_ids
  AND source.delete_at IS NULL
  AND target.delete_at IS NULL
WITH target,
     coalesce(target.aliases, []) AS existing_aliases,
     coalesce(target.description, '') AS tgt_desc,
     collect(DISTINCT source.name) AS source_names,
     collect(DISTINCT coalesce(source.description, '')) AS source_descs

// 1. 合并 aliases：将所有 source.name 追加到 target.aliases（去重，忽略空值与大小写）
WITH target, tgt_desc, source_names, source_descs, existing_aliases,
     existing_aliases + [n IN source_names WHERE n IS NOT NULL AND n <> '' AND NOT toLower(n) IN [x IN existing_aliases WHERE x IS NOT NULL | toLower(x)]] AS new_aliases

// 2. 合并 description：将所有 source.description 逐一追加（去重，分号分隔）
WITH target, new_aliases, existing_aliases, source_descs,
     reduce(desc = tgt_desc, src IN source_descs |
         CASE
             WHEN src <> '' AND NOT desc CONTAINS src
             THEN CASE WHEN desc = '' THEN src ELSE desc + '；' + src END
             ELSE desc
         END
     ) AS new_description

SET target.aliases = new_aliases,
    target.description = new_description

// 无分组键聚合：0 命中时也返回一行（affected_nodes=[]、alias_merged=0），不发假事件
WITH collect({label: 'ExtractedEntity', node_id: target.id, operation: 'UPSERT'}) AS affected_nodes,
     count(target) AS alias_merged
RETURN alias_merged, size(affected_nodes) AS affected_count, affected_nodes
"""

# 别名归并第 2 步：别名节点上「别名属于」以外的边重定向到 target。
# 纯关系写，不返回 affected_nodes，经 _relationship_write 提交、不登记 Outbox。
# 各段独立 CALL () {} 子查询，避免空输入时分组聚合丢行导致后续段被跳过。
#
# 四段分别覆盖：入边 EXTRACTED_RELATIONSHIP、出边 EXTRACTED_RELATIONSHIP、
# 陈述句的 STATEMENT_ENTITY（legacy 边类型）、陈述句的 REFERENCES_ENTITY
# （当前主写链 graph_write_queries.STATEMENT_ENTITY_EDGE_SAVE 建的类型）。
# 两种陈述句边都要处理：漏掉任一种，该边会在第 3 步 DETACH DELETE 时被连带删除，
# 导致陈述句丢失对实体的引用。
# 第 4 段用 WHERE NOT (stmt)-[:REFERENCES_ENTITY]->(user4) 跳过 target 已有同名边的
# 情况，不覆盖既有边属性；未被重定向的别名边由第 3 步的 DETACH DELETE 清理。
REDIRECT_ALIAS_EDGES = """
// 1. 入边：其他实体 → 别名节点，重定向到 target
CALL () {
  MATCH (alias:ExtractedEntity {end_user_id: $end_user_id})-[ar:EXTRACTED_RELATIONSHIP]->(user:ExtractedEntity {end_user_id: $end_user_id})
  WHERE ar.predicate = '别名属于' AND alias.id IN $alias_ids AND alias.delete_at IS NULL AND user.delete_at IS NULL
  WITH DISTINCT alias, user
  MATCH (other)-[r:EXTRACTED_RELATIONSHIP]->(alias)
  WHERE r.predicate <> '别名属于' AND other.id <> user.id
  CREATE (other)-[nr:EXTRACTED_RELATIONSHIP]->(user)
  SET nr = properties(r)
  DELETE r
  RETURN count(*) AS redirected_incoming
}
// 2. 出边：别名节点 → 其他实体，重定向到 target
CALL () {
  MATCH (alias:ExtractedEntity {end_user_id: $end_user_id})-[ar2:EXTRACTED_RELATIONSHIP]->(user2:ExtractedEntity {end_user_id: $end_user_id})
  WHERE ar2.predicate = '别名属于' AND alias.id IN $alias_ids AND alias.delete_at IS NULL AND user2.delete_at IS NULL
  WITH DISTINCT alias, user2
  MATCH (alias)-[r:EXTRACTED_RELATIONSHIP]->(other)
  WHERE r.predicate <> '别名属于' AND other.id <> user2.id
  CREATE (user2)-[nr:EXTRACTED_RELATIONSHIP]->(other)
  SET nr = properties(r)
  DELETE r
  RETURN count(*) AS redirected_outgoing
}
// 3. 陈述句 → 别名节点，重定向到 target（legacy STATEMENT_ENTITY）
CALL () {
  MATCH (alias:ExtractedEntity {end_user_id: $end_user_id})-[ar3:EXTRACTED_RELATIONSHIP]->(user3:ExtractedEntity {end_user_id: $end_user_id})
  WHERE ar3.predicate = '别名属于' AND alias.id IN $alias_ids AND alias.delete_at IS NULL AND user3.delete_at IS NULL
  WITH DISTINCT alias, user3
  MATCH (stmt)-[r:STATEMENT_ENTITY]->(alias)
  CREATE (stmt)-[nr:STATEMENT_ENTITY]->(user3)
  SET nr = properties(r)
  DELETE r
  RETURN count(*) AS redirected_legacy_stmt
}
// 4. 陈述句 → 别名节点，重定向到 target（当前 REFERENCES_ENTITY，缺陷修复）
CALL () {
  MATCH (alias:ExtractedEntity {end_user_id: $end_user_id})-[ar4:EXTRACTED_RELATIONSHIP]->(user4:ExtractedEntity {end_user_id: $end_user_id})
  WHERE ar4.predicate = '别名属于' AND alias.id IN $alias_ids AND alias.delete_at IS NULL AND user4.delete_at IS NULL
  WITH DISTINCT alias, user4
  MATCH (stmt)-[r:REFERENCES_ENTITY]->(alias)
  WHERE NOT (stmt)-[:REFERENCES_ENTITY]->(user4)
  CREATE (stmt)-[nr:REFERENCES_ENTITY]->(user4)
  SET nr = properties(r)
  DELETE r
  RETURN count(*) AS redirected_reference_stmt
}
RETURN redirected_incoming, redirected_outgoing,
       redirected_legacy_stmt + redirected_reference_stmt AS redirected_stmt
"""

# 别名归并第 3 步：删除别名节点。调用前第 2 步已把其它边重定向完毕，
# 剩下的边只有 (alias)-[:EXTRACTED_RELATIONSHIP {predicate:'别名属于'}]->(target)，
# DETACH DELETE 一并删除节点和该边。
# 事件身份必须在 DETACH DELETE 之前构造：节点删掉后 a.id 已不可读。
DELETE_ALIAS_NODES = """
MATCH (alias:ExtractedEntity {end_user_id: $end_user_id})-[r:EXTRACTED_RELATIONSHIP]->(user:ExtractedEntity {end_user_id: $end_user_id})
WHERE r.predicate = '别名属于' AND alias.id IN $alias_ids AND alias.delete_at IS NULL AND user.delete_at IS NULL
WITH collect(DISTINCT alias) AS aliases
WITH aliases, [a IN aliases | {label: 'ExtractedEntity', node_id: a.id, operation: 'DELETE'}] AS affected_nodes
FOREACH (a IN aliases | DETACH DELETE a)
RETURN size(aliases) AS alias_nodes_deleted, size(affected_nodes) AS affected_count, affected_nodes
"""
