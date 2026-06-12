import hashlib
import json
import logging
from typing import Any, Dict, List, Tuple

from app.repositories.neo4j.neo4j_connector import Neo4jConnector

logger = logging.getLogger(__name__)

# Fulltext 索引: (name, label, properties, analyzer)
FULLTEXT_DEFS: List[Tuple[str, str, List[str], str]] = [
    ("statementsFulltext", "Statement", ["statement"], "cjk"),
    ("entitiesFulltext", "ExtractedEntity",
     ["name", "description", "aliases", "description_summary", "description_timeline"], "cjk"),
    ("chunksFulltext", "Chunk", ["content"], "cjk"),
    ("summariesFulltext", "MemorySummary", ["content"], "cjk"),
    ("communitiesFulltext", "Community", ["name", "summary"], "cjk"),
    ("perceptualFulltext", "Perceptual", ["summary", "topic", "domain", "keywords"], "cjk"),
    ("assistantPrunedFulltext", "AssistantPruned", ["text"], "cjk"),
]

# Vector 索引: (name, label, property, dimensions)
VECTOR_DEFS: List[Tuple[str, str, str, int]] = [
    ("statement_embedding_index", "Statement", "statement_embedding", 1024),
    ("chunk_embedding_index", "Chunk", "chunk_embedding", 1024),
    ("entity_embedding_index", "ExtractedEntity", "name_embedding", 1024),
    ("summary_embedding_index", "MemorySummary", "summary_embedding", 1024),
    ("community_summary_embedding_index", "Community", "summary_embedding", 1024),
    ("dialogue_embedding_index", "Dialogue", "dialog_embedding", 1024),
    ("perceptual_summary_embedding_index", "Perceptual", "summary_embedding", 1024),
    ("assistant_pruned_embedding_index", "AssistantPruned", "text_embedding", 1024),
]

# Range 索引 (end_user_id): (name, label) — property 固定为 end_user_id
RANGE_DEFS: List[Tuple[str, str]] = [
    ("user_dialogue", "Dialogue"),
    ("user_chunk", "Chunk"),
    ("user_statement", "Statement"),
    ("user_extracted_entity", "ExtractedEntity"),
    ("user_memory_summary", "MemorySummary"),
    ("user_perceptual", "Perceptual"),
    ("user_assistant_original", "AssistantOriginal"),
    ("user_assistant_pruned", "AssistantPruned"),
    ("user_conversation", "Conversation"),
]

# Uniqueness 约束: (name, label, property)
CONSTRAINT_DEFS: List[Tuple[str, str, str]] = [
    ("dialog_id_unique", "Dialogue", "id"),
    ("statement_id_unique", "Statement", "id"),
    ("chunk_id_unique", "Chunk", "id"),
    ("assistant_original_id_unique", "AssistantOriginal", "id"),
    ("assistant_pruned_id_unique", "AssistantPruned", "id"),
    ("conversation_id_unique", "Conversation", "id"),
    ("entity_id_unique", "ExtractedEntity", "id"),
    ("memory_summary_id_unique", "MemorySummary", "id"),
    ("perceptual_id_unique", "Perceptual", "id"),
    # Community 存在重复数据，暂不建唯一约束
]


def _fingerprint(data: Any) -> str:
    """对任意可序列化对象生成短哈希指纹，用于快速比较。"""
    raw = json.dumps(data, sort_keys=True, default=str)
    return hashlib.md5(raw.encode()).hexdigest()[:12]


# 每个 category 的比较字段；用于 fingerprint + def_key 构建
_DIFF_KEYS = {
    "fulltext index": ("label", "properties", "analyzer"),
    "vector index": ("label", "property", "dimensions", "similarity_function"),
    "range index": ("label", "property"),
    "constraint": ("label", "property"),
}


def _make_def_key(d: Dict[str, Any], diff_keys: Tuple[str, ...]) -> str:
    """由比较字段构建结构键（用于按定义匹配，而非按名称）。

    def_key 格式需与各 show_query 中 Cypher 侧拼接保持一致。
    list 保持原始顺序，因为 Neo4j SHOW 返回的 properties 顺序与
    CREATE 语句中 ON EACH [...] 的声明顺序一致。
    """
    parts = []
    for k in diff_keys:
        v = d.get(k)
        if isinstance(v, list):
            parts.append("[" + ",".join(v) + "]")
        else:
            parts.append(str(v))
    return "|".join(parts)


async def _smart_upsert(
        connector: Neo4jConnector,
        *,
        desired: List[Dict[str, Any]],
        show_query: str,
        category: str,
) -> Dict[str, int]:
    """
    通用 Smart Upsert：查询已有定义 → diff → 仅重建变化项。

    匹配策略（def_key 优先，name 兜底）：

      def_key 命中 ─┬─ name 相同 ─┬─ fp 相同 → 跳过
                    │             └─ fp 不同 → DROP + CREATE（定义变化）
                    └─ name 不同 ────────────→ DROP 旧名 + CREATE 新名（重命名）

      def_key 未命中 ─┬─ name 命中 ──────────→ DROP 旧（同名不同定义） + CREATE
                      └─ name 未命中 ────────→ CREATE（全新）

    show_query 需 RETURN: name, def_key, + 各 category 的 diff 字段。
    def_key 由 Cypher 侧拼接，格式需与 _make_def_key 一致。

    Args:
        connector: Neo4j 连接器
        desired: 期望定义列表，每项含 name / drop_query / create_query + 比较字段
        show_query: 查询已有索引/约束的 Cypher
        category: 日志分类名
    """
    diff_keys = _DIFF_KEYS.get(category, ())

    # 1. 查询已有定义
    try:
        existing_rows = await connector.execute_query(show_query)
    except Exception as e:
        logger.warning(f"[Index] 查询已有 {category} 失败: {e}，跳过")
        return {"created": 0, "rebuilt": 0, "skipped": 0}

    # 2. 建立双重映射：def_key → info  +  name → info
    existing_by_def: Dict[str, Dict[str, str]] = {}
    existing_by_name: Dict[str, Dict[str, str]] = {}
    for row in (existing_rows or []):
        row_def_key = row.get("def_key", "")
        row_name = row.get("name", "")
        if not row_def_key or not row_name:
            continue
        info = {
            "name": row_name,
            "def_key": row_def_key,
            "fingerprint": _fingerprint({k: row.get(k) for k in diff_keys}),
        }
        existing_by_def[row_def_key] = info
        existing_by_name[row_name] = info

    # 3. Diff & 执行
    created = rebuilt = skipped = 0

    for d in desired:
        name = d["name"]
        def_key = _make_def_key(d, diff_keys)
        desired_fp = _fingerprint({k: d[k] for k in diff_keys})
        existing = existing_by_def.get(def_key)
        same_name = existing_by_name.get(name)

        if existing is not None:
            # ── def_key 命中 ──
            if existing["name"] != name:
                # 结构相同、名称不同 → 重命名
                try:
                    drop_old = d["drop_query"].replace(name, existing["name"])
                    await connector.execute_query(drop_old)
                    await connector.execute_query(d["create_query"])
                    logger.info(
                        f"[Index] 重命名 {category}: {existing['name']} → {name}"
                    )
                    rebuilt += 1
                except Exception as e:
                    logger.error(f"[Index] 重命名 {category} 失败 {existing['name']} → {name}: {e}")
            elif existing["fingerprint"] != desired_fp:
                # 结构相同、名称相同、定义变化 → 重建
                try:
                    await connector.execute_query(d["drop_query"])
                    await connector.execute_query(d["create_query"])
                    logger.info(
                        f"[Index] 重建 {category}: {name} "
                        f"(fp: {existing['fingerprint'][:8]} → {desired_fp[:8]})"
                    )
                    rebuilt += 1
                except Exception as e:
                    logger.error(f"[Index] 重建 {category} 失败 {name}: {e}")
            else:
                skipped += 1
        elif same_name is not None:
            # ── def_key 未命中，但 name 命中 → 同名不同定义，删旧建新 ──
            try:
                drop_old = d["drop_query"].replace(name, same_name["name"])
                await connector.execute_query(drop_old)
                await connector.execute_query(d["create_query"])
                logger.info(
                    f"[Index] 重建 {category}（同名不同定义）: {name} "
                    f"(def: {same_name['def_key'][:40]}... → {def_key[:40]}...)"
                )
                rebuilt += 1
            except Exception as e:
                logger.error(f"[Index] 重建 {category} 失败 {name}: {e}")
        else:
            # ── def_key 和 name 都未命中 → 全新创建 ──
            try:
                await connector.execute_query(d["create_query"])
                logger.info(f"[Index] 创建 {category}: {name}")
                created += 1
            except Exception as e:
                logger.error(f"[Index] 创建 {category} 失败 {name}: {e}")

    logger.info(
        f"[Index] {category} 完成: created={created}, rebuilt={rebuilt}, skipped={skipped}"
    )
    return {"created": created, "rebuilt": rebuilt, "skipped": skipped}


async def create_fulltext_indexes():
    """Smart Upsert fulltext indexes."""
    connector = Neo4jConnector()
    try:
        desired: List[Dict[str, Any]] = []
        for name, label, props, analyzer in FULLTEXT_DEFS:
            props_cypher = ", ".join(f"n.{p}" for p in props)
            desired.append({
                "name": name,
                "label": label,
                "properties": props,  # 原始顺序，与 SHOW 返回顺序一致
                "analyzer": analyzer,
                "drop_query": f"DROP INDEX {name}",
                "create_query": (
                    f"CREATE FULLTEXT INDEX {name} "
                    f"FOR (n:{label}) ON EACH [{props_cypher}] "
                    f"OPTIONS {{ indexConfig: {{ `fulltext.analyzer`: '{analyzer}' }} }}"
                ),
            })

        await _smart_upsert(
            connector,
            desired=desired,
            show_query="""
                SHOW FULLTEXT INDEXES
                YIELD name, labelsOrTypes, properties, options
                RETURN name,
                       labelsOrTypes[0] AS label,
                       properties,
                       coalesce(options.indexConfig.`fulltext.analyzer`, '')
                         AS analyzer,
                       labelsOrTypes[0] + '|[' +
                         reduce(s='', p IN properties | s + CASE WHEN s='' THEN '' ELSE ',' END + p) +
                         ']|' +
                         coalesce(options.indexConfig.`fulltext.analyzer`, '')
                         AS def_key
            """,
            category="fulltext index",
        )
    finally:
        await connector.close()


async def create_vector_indexes():
    """Smart Upsert vector indexes."""
    connector = Neo4jConnector()
    try:
        desired: List[Dict[str, Any]] = []
        for name, label, prop, dims in VECTOR_DEFS:
            desired.append({
                "name": name,
                "label": label,
                "property": prop,
                "dimensions": dims,
                "similarity_function": "cosine",
                "drop_query": f"DROP INDEX {name}",
                "create_query": (
                    f"CREATE VECTOR INDEX {name} "
                    f"FOR (n:{label}) ON n.{prop} "
                    f"OPTIONS {{indexConfig: {{"
                    f"  `vector.dimensions`: {dims},"
                    f"  `vector.similarity_function`: 'cosine'"
                    f"}}}}"
                ),
            })

        await _smart_upsert(
            connector,
            desired=desired,
            show_query="""
                SHOW VECTOR INDEXES
                YIELD name, labelsOrTypes, properties, options
                RETURN name,
                       labelsOrTypes[0] AS label,
                       properties[0] AS property,
                       options.indexConfig.`vector.dimensions` AS dimensions,
                       toLower(options.indexConfig.`vector.similarity_function`)
                         AS similarity_function,
                       labelsOrTypes[0] + '|' + properties[0] + '|' +
                         toString(options.indexConfig.`vector.dimensions`) + '|' +
                         toLower(options.indexConfig.`vector.similarity_function`)
                         AS def_key
            """,
            category="vector index",
        )
    finally:
        await connector.close()


async def create_end_user_id_indexes():
    """Smart Upsert range indexes on end_user_id."""
    connector = Neo4jConnector()
    try:
        desired: List[Dict[str, Any]] = []
        for name, label in RANGE_DEFS:
            desired.append({
                "name": name,
                "label": label,
                "property": "end_user_id",
                "drop_query": f"DROP INDEX {name}",
                "create_query": (
                    f"CREATE INDEX {name} FOR (n:{label}) ON (n.end_user_id)"
                ),
            })

        await _smart_upsert(
            connector,
            desired=desired,
            show_query="""
                SHOW INDEXES
                YIELD name, labelsOrTypes, properties, type
                WHERE type = 'RANGE'
                  AND any(prop IN properties WHERE prop = 'end_user_id')
                RETURN name,
                       labelsOrTypes[0] AS label,
                       properties[0] AS property,
                       labelsOrTypes[0] + '|' + properties[0] AS def_key
            """,
            category="range index",
        )
    finally:
        await connector.close()


async def create_user_indexes():
    """Deprecated: 历史保留入口；新代码请直接调用 :func:`create_end_user_id_indexes`。"""
    await create_end_user_id_indexes()


async def create_unique_constraints():
    """Smart Upsert uniqueness constraints."""
    connector = Neo4jConnector()
    try:
        desired: List[Dict[str, Any]] = []
        for name, label, prop in CONSTRAINT_DEFS:
            desired.append({
                "name": name,
                "label": label,
                "property": prop,
                "drop_query": f"DROP CONSTRAINT {name}",
                "create_query": (
                    f"CREATE CONSTRAINT {name} "
                    f"FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE"
                ),
            })

        await _smart_upsert(
            connector,
            desired=desired,
            show_query="""
                SHOW CONSTRAINTS
                YIELD name, labelsOrTypes, properties, type
                WHERE type = 'UNIQUENESS'
                RETURN name,
                       labelsOrTypes[0] AS label,
                       properties[0] AS property,
                       labelsOrTypes[0] + '|' + properties[0] AS def_key
            """,
            category="constraint",
        )
    finally:
        await connector.close()


async def create_all_indexes():
    """Create all indexes and constraints in one go."""
    await create_fulltext_indexes()
    await create_vector_indexes()
    await create_end_user_id_indexes()
    await create_unique_constraints()
