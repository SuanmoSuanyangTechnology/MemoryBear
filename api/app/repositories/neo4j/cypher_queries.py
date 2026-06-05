from app.core.memory.constants.graph_data_constants import SUPPORTED_NODE_TYPES
from app.core.memory.enums import Neo4jNodeType

DIALOGUE_NODE_SAVE = """
    UNWIND $dialogues AS dialogue
    MERGE (n:Dialogue {id: dialogue.id})
    SET n.uuid = coalesce(n.uuid, dialogue.id),
        n.end_user_id = dialogue.end_user_id,
        n.run_id = dialogue.run_id,
        n.ref_id = dialogue.ref_id,
        n.created_at = dialogue.created_at,
        n.content = dialogue.content,
        n.dialog_embedding = dialogue.dialog_embedding
    RETURN n.id AS uuid
"""

STATEMENT_NODE_SAVE = """
UNWIND $statements AS statement
MERGE (s:Statement {id: statement.id})
SET s += {
    id: statement.id,
    run_id: statement.run_id,
    chunk_id: statement.chunk_id,
    end_user_id: statement.end_user_id,
    stmt_type: statement.stmt_type,
    statement: statement.statement,
    speaker: statement.speaker,
    emotion_intensity: statement.emotion_intensity,
    emotion_target: statement.emotion_target,
    emotion_subject: statement.emotion_subject,
    emotion_type: statement.emotion_type,
    emotion_keywords: statement.emotion_keywords,
    temporal_info: statement.temporal_info,
    created_at: statement.created_at,
    valid_at: coalesce(statement.valid_at, ""),
    invalid_at: coalesce(statement.invalid_at, ""),
    statement_embedding: statement.statement_embedding,
    relevence_info: statement.relevence_info,
    importance_score: statement.importance_score,
    activation_value: statement.activation_value,
    access_history: statement.access_history,
    last_access_time: statement.last_access_time,
    access_count: statement.access_count,
    dialog_at: statement.dialog_at,
    has_unsolved_reference: statement.has_unsolved_reference
}
RETURN s.id AS uuid
"""

STATEMENT_EMOTION_UPDATE = """
UNWIND $items AS item
MATCH (s:Statement {id: item.statement_id})
SET s.emotion_type = item.emotion_type,
    s.emotion_intensity = item.emotion_intensity,
    s.emotion_keywords = item.emotion_keywords
RETURN s.id AS uuid
"""

CHUNK_NODE_SAVE = """
UNWIND $chunks AS chunk
MERGE (c:Chunk {id: chunk.id})
SET c += {
    id: chunk.id,
    name: chunk.name,
    end_user_id: chunk.end_user_id,
    run_id: chunk.run_id,
    created_at: chunk.created_at,
    dialog_id: chunk.dialog_id,
    content: chunk.content,
    speaker: chunk.speaker,
    chunk_embedding: chunk.chunk_embedding,
    sequence_number: chunk.sequence_number,
    start_index: chunk.start_index,
    end_index: chunk.end_index
}
RETURN c.id AS uuid
"""
# bug修改点

EXTRACTED_ENTITY_NODE_SAVE = """
// Upsert entity nodes safely: preserve existing non-empty fields when incoming is empty
UNWIND $entities AS entity
MERGE (e:ExtractedEntity {id: entity.id})
SET e.name = CASE WHEN entity.name IS NOT NULL AND entity.name <> '' THEN entity.name ELSE e.name END,
    e.end_user_id = CASE WHEN entity.end_user_id IS NOT NULL AND entity.end_user_id <> '' THEN entity.end_user_id ELSE e.end_user_id END,
    e.run_id = CASE WHEN entity.run_id IS NOT NULL AND entity.run_id <> '' THEN entity.run_id ELSE e.run_id END,
    e.created_at = CASE
        WHEN entity.created_at IS NOT NULL AND (e.created_at IS NULL OR entity.created_at < e.created_at)
        THEN entity.created_at ELSE e.created_at END,
    e.entity_idx = CASE WHEN e.entity_idx IS NULL OR e.entity_idx = 0 THEN entity.entity_idx ELSE e.entity_idx END,
    e.entity_type = CASE WHEN entity.entity_type IS NOT NULL AND entity.entity_type <> '' THEN entity.entity_type ELSE e.entity_type END,
    e.type_id = entity.type_id,
    e.type_description = CASE WHEN entity.type_description IS NOT NULL AND entity.type_description <> '' THEN entity.type_description ELSE coalesce(e.type_description, '') END,
    e.description = CASE
        WHEN entity.description IS NOT NULL AND entity.description <> ''
         AND (e.description IS NULL OR size(e.description) = 0 OR size(entity.description) > size(e.description))
        THEN entity.description ELSE e.description END,
    e.example = CASE 
        WHEN entity.example IS NOT NULL AND entity.example <> '' 
        THEN entity.example 
        ELSE coalesce(e.example, '') 
    END,
    e.statement_id = CASE WHEN entity.statement_id IS NOT NULL AND entity.statement_id <> '' THEN entity.statement_id ELSE e.statement_id END,
    e.aliases = CASE
        WHEN entity.aliases IS NOT NULL AND size(entity.aliases) > 0
        THEN CASE 
            WHEN e.aliases IS NULL THEN entity.aliases 
            ELSE reduce(acc = [], alias IN (e.aliases + entity.aliases) | 
                CASE WHEN alias IN acc THEN acc ELSE acc + alias END)
        END
        ELSE e.aliases END,
    e.name_embedding = CASE
        WHEN entity.name_embedding IS NOT NULL AND size(entity.name_embedding) > 0 THEN entity.name_embedding
        ELSE e.name_embedding END,
    // TODO: fact_summary 功能暂时禁用，待后续开发完善后启用
    // e.fact_summary = CASE
    //     WHEN entity.fact_summary IS NOT NULL AND entity.fact_summary <> ''
    //      AND (e.fact_summary IS NULL OR size(e.fact_summary) = 0 OR size(entity.fact_summary) > size(e.fact_summary))
    //     THEN entity.fact_summary ELSE e.fact_summary END,
    e.connect_strength = CASE
        WHEN entity.connect_strength IS NULL OR entity.connect_strength = '' THEN e.connect_strength
        ELSE CASE
            WHEN e.connect_strength = 'strong' AND entity.connect_strength = 'weak' THEN 'both'
            WHEN e.connect_strength = 'weak' AND entity.connect_strength = 'strong' THEN 'both'
            WHEN e.connect_strength IS NULL OR e.connect_strength = '' THEN entity.connect_strength
            ELSE e.connect_strength
        END
    END,
    e.importance_score = CASE WHEN entity.importance_score IS NOT NULL THEN entity.importance_score ELSE coalesce(e.importance_score, 0.5) END,
    e.activation_value = CASE WHEN entity.activation_value IS NOT NULL THEN entity.activation_value ELSE e.activation_value END,
    e.access_history = CASE WHEN entity.access_history IS NOT NULL THEN entity.access_history ELSE coalesce(e.access_history, []) END,
    e.last_access_time = CASE WHEN entity.last_access_time IS NOT NULL THEN entity.last_access_time ELSE e.last_access_time END,
    e.access_count = CASE WHEN entity.access_count IS NOT NULL THEN entity.access_count ELSE coalesce(e.access_count, 0) END,
    e.is_explicit_memory = CASE WHEN entity.is_explicit_memory IS NOT NULL THEN entity.is_explicit_memory ELSE coalesce(e.is_explicit_memory, false) END
RETURN e.id AS uuid
"""

# ── 元数据增量回写：将 LLM 提取的元数据追加到用户实体节点 ──
ENTITY_METADATA_UPDATE = """
MATCH (e:ExtractedEntity {id: $entity_id})
SET e.core_facts = CASE
        WHEN $core_facts IS NOT NULL AND size($core_facts) > 0
        THEN reduce(acc = coalesce(e.core_facts, []), item IN $core_facts |
            CASE WHEN item IN acc THEN acc ELSE acc + item END)
        ELSE coalesce(e.core_facts, []) END,
    e.traits = CASE
        WHEN $traits IS NOT NULL AND size($traits) > 0
        THEN reduce(acc = coalesce(e.traits, []), item IN $traits |
            CASE WHEN item IN acc THEN acc ELSE acc + item END)
        ELSE coalesce(e.traits, []) END,
    e.relations = CASE
        WHEN $relations IS NOT NULL AND size($relations) > 0
        THEN reduce(acc = coalesce(e.relations, []), item IN $relations |
            CASE WHEN item IN acc THEN acc ELSE acc + item END)
        ELSE coalesce(e.relations, []) END,
    e.goals = CASE
        WHEN $goals IS NOT NULL AND size($goals) > 0
        THEN reduce(acc = coalesce(e.goals, []), item IN $goals |
            CASE WHEN item IN acc THEN acc ELSE acc + item END)
        ELSE coalesce(e.goals, []) END,
    e.interests = CASE
        WHEN $interests IS NOT NULL AND size($interests) > 0
        THEN reduce(acc = coalesce(e.interests, []), item IN $interests |
            CASE WHEN item IN acc THEN acc ELSE acc + item END)
        ELSE coalesce(e.interests, []) END,
    e.beliefs_or_stances = CASE
        WHEN $beliefs_or_stances IS NOT NULL AND size($beliefs_or_stances) > 0
        THEN reduce(acc = coalesce(e.beliefs_or_stances, []), item IN $beliefs_or_stances |
            CASE WHEN item IN acc THEN acc ELSE acc + item END)
        ELSE coalesce(e.beliefs_or_stances, []) END,
    e.anchors = CASE
        WHEN $anchors IS NOT NULL AND size($anchors) > 0
        THEN reduce(acc = coalesce(e.anchors, []), item IN $anchors |
            CASE WHEN item IN acc THEN acc ELSE acc + item END)
        ELSE coalesce(e.anchors, []) END,
    e.events = CASE
        WHEN $events IS NOT NULL AND size($events) > 0
        THEN reduce(acc = coalesce(e.events, []), item IN $events |
            CASE WHEN item IN acc THEN acc ELSE acc + item END)
        ELSE coalesce(e.events, []) END
RETURN e.id AS uuid
"""

# ── 查询用户实体已有的元数据（供增量提取时去重） ──
ENTITY_METADATA_QUERY = """
MATCH (e:ExtractedEntity {id: $entity_id})
RETURN e.core_facts AS core_facts,
       e.traits AS traits,
       e.relations AS relations,
       e.goals AS goals,
       e.interests AS interests,
       e.beliefs_or_stances AS beliefs_or_stances,
       e.anchors AS anchors,
       e.events AS events
"""

# Add back ENTITY_RELATIONSHIP_SAVE to be used by graph_saver.save_entities_and_relationships
ENTITY_RELATIONSHIP_SAVE = """
UNWIND $relationships AS rel
// Match entities by stable id within end_user_id, do not constrain by run_id
MATCH (subject:ExtractedEntity {id: rel.source_id, end_user_id: rel.end_user_id})
MATCH (object:ExtractedEntity {id: rel.target_id, end_user_id: rel.end_user_id})
// Avoid duplicate edges across runs for the same endpoints
MERGE (subject)-[r:EXTRACTED_RELATIONSHIP]->(object)
SET r.predicate = rel.predicate,
    r.predicate_id = rel.predicate_id,
    r.predicate_surface = rel.predicate_surface,
    r.predicate_description = rel.predicate_description,
    r.statement_id = rel.statement_id,
    r.value = rel.value,
    r.statement = rel.statement,
    r.valid_at = coalesce(rel.valid_at, ""),
    r.invalid_at = coalesce(rel.invalid_at, ""),
    r.created_at = rel.created_at,
    r.run_id = rel.run_id,
    r.end_user_id = rel.end_user_id
RETURN elementId(r) AS uuid
"""

# 在 Neo4j 5及后续版本中，id() 函数已被标记为弃用，用elementId() 函数替代

# 保存弱关系实体，设置 e.is_weak = true；不维护 e.relations 聚合字段
WEAK_ENTITY_NODE_SAVE = """
UNWIND $weak_entities AS entity
MERGE (e:ExtractedEntity {id: entity.id, run_id: entity.run_id})
SET e += {
    name: entity.name,
    end_user_id: entity.end_user_id,
    run_id: entity.run_id,
    description: entity.description,
    chunk_id: entity.chunk_id,
    dialog_id: entity.dialog_id
}
// Independent weak flag，仅标记弱关系，不再维护 relations 聚合字段
SET e.is_weak = true
RETURN e.id AS id
"""

# 为强关系三元组中的主语和宾语创建/更新实体节点，仅设置 e.is_strong = true，不维护 e.relations 字段
SAVE_STRONG_TRIPLE_ENTITIES = """
UNWIND $items AS item
MERGE (s:ExtractedEntity {id: item.source_id, run_id: item.run_id})
SET s += {name: item.subject, end_user_id: item.end_user_id, run_id: item.run_id}
// Independent strong flag
SET s.is_strong = true
MERGE (o:ExtractedEntity {id: item.target_id, run_id: item.run_id})
SET o += {name: item.object, end_user_id: item.end_user_id, run_id: item.run_id}
// Independent strong flag
SET o.is_strong = true
"""


DIALOGUE_STATEMENT_EDGE_SAVE = """
    UNWIND $dialogue_statement_edges AS edge
    // 支持按 uuid 或 ref_id 连接到 Dialogue，避免因来源 ID 不一致而断链
    MATCH (dialogue:Dialogue)
    WHERE dialogue.uuid = edge.source OR dialogue.ref_id = edge.source
    MATCH (statement:Statement {id: edge.target})
    // 仅按端点去重，关系属性可更新
    MERGE (dialogue)-[e:MENTIONS]->(statement)
    SET e.uuid = edge.id,
        e.end_user_id = edge.end_user_id,
        e.created_at = edge.created_at
    RETURN e.uuid AS uuid
"""

# 在 Neo4j 5及后续版本中，id() 函数已被标记为弃用，用elementId() 函数替代


CHUNK_STATEMENT_EDGE_SAVE = """
    UNWIND $chunk_statement_edges AS edge
    MATCH (statement:Statement {id: edge.source, run_id: edge.run_id})
    MATCH (chunk:Chunk {id: edge.target, run_id: edge.run_id})
    MERGE (chunk)-[e:CONTAINS {id: edge.id}]->(statement)
    SET e.end_user_id = edge.end_user_id,
        e.run_id = edge.run_id,
        e.created_at = edge.created_at
    RETURN e.id AS uuid
"""

STATEMENT_ENTITY_EDGE_SAVE = """
UNWIND $relationships AS rel
// Statement nodes are per-run; keep run_id constraint on statements
MATCH (statement:Statement {id: rel.source, run_id: rel.run_id})
// Entities are shared across runs within end_user_id; do not constrain by run_id
MATCH (entity:ExtractedEntity {id: rel.target, end_user_id: rel.end_user_id})
// Avoid duplicate edges across runs for same endpoints
MERGE (statement)-[r:REFERENCES_ENTITY]->(entity)
SET r.end_user_id = rel.end_user_id,
    r.run_id = rel.run_id,
    r.created_at = rel.created_at,
    r.connect_strength = rel.connect_strength
RETURN elementId(r) AS uuid
"""

ENTITY_EMBEDDING_SEARCH = """
CALL db.index.vector.queryNodes('entity_embedding_index', $limit * 100, $embedding)
YIELD node AS e, score
WHERE e.name_embedding IS NOT NULL
  AND ($end_user_id IS NULL OR e.end_user_id = $end_user_id)
RETURN e.id AS id,
       e.name AS name,
       e.end_user_id AS end_user_id,
       e.entity_type AS entity_type,
       COALESCE(e.activation_value, e.importance_score, 0.5) AS activation_value,
       COALESCE(e.importance_score, 0.5) AS importance_score,
       e.last_access_time AS last_access_time,
       COALESCE(e.access_count, 0) AS access_count,
       score
ORDER BY score DESC
LIMIT $limit
"""
# Embedding-based search: cosine similarity on Statement.statement_embedding
STATEMENT_EMBEDDING_SEARCH = """
CALL db.index.vector.queryNodes('statement_embedding_index', $limit * 100, $embedding)
YIELD node AS s, score
WHERE s.statement_embedding IS NOT NULL
  AND ($end_user_id IS NULL OR s.end_user_id = $end_user_id)
RETURN s.id AS id,
       s.statement AS statement,
       s.end_user_id AS end_user_id,
       s.chunk_id AS chunk_id,
       s.created_at AS created_at,
       s.valid_at AS valid_at,
       s.invalid_at AS invalid_at,
       COALESCE(s.activation_value, s.importance_score, 0.5) AS activation_value,
       COALESCE(s.importance_score, 0.5) AS importance_score,
       s.last_access_time AS last_access_time,
       COALESCE(s.access_count, 0) AS access_count,
       score
ORDER BY score DESC
LIMIT $limit
"""

# Embedding-based search: cosine similarity on Chunk.chunk_embedding
CHUNK_EMBEDDING_SEARCH = """
CALL db.index.vector.queryNodes('chunk_embedding_index', $limit * 100, $embedding)
YIELD node AS c, score
WHERE c.chunk_embedding IS NOT NULL
  AND ($end_user_id IS NULL OR c.end_user_id = $end_user_id)
RETURN c.id AS chunk_id,
       c.end_user_id AS end_user_id,
       c.content AS content,
       c.dialog_id AS dialog_id,
       COALESCE(c.activation_value, 0.5) AS activation_value,
       c.last_access_time AS last_access_time,
       COALESCE(c.access_count, 0) AS access_count,
       score
ORDER BY score DESC
LIMIT $limit
"""

SEARCH_STATEMENTS_BY_KEYWORD = """
CALL db.index.fulltext.queryNodes("statementsFulltext", $query) YIELD node AS s, score
WHERE ($end_user_id IS NULL OR s.end_user_id = $end_user_id)
OPTIONAL MATCH (c:Chunk)-[:CONTAINS]->(s)
OPTIONAL MATCH (s)-[:REFERENCES_ENTITY]->(e:ExtractedEntity)
RETURN s.id AS id,
       s.statement AS statement,
       s.end_user_id AS end_user_id,
       s.chunk_id AS chunk_id,
       s.created_at AS created_at,
       s.valid_at AS valid_at,
       s.invalid_at AS invalid_at,
       c.id AS chunk_id_from_rel,
       collect(DISTINCT e.id) AS entity_ids,
       COALESCE(s.activation_value, s.importance_score, 0.5) AS activation_value,
       COALESCE(s.importance_score, 0.5) AS importance_score,
       s.last_access_time AS last_access_time,
       COALESCE(s.access_count, 0) AS access_count,
       score
ORDER BY score DESC
LIMIT $limit
"""

SEARCH_ENTITIES_BY_NAME_OR_ALIAS = """
CALL db.index.fulltext.queryNodes("entitiesFulltext", $query) YIELD node AS e, score
WHERE ($end_user_id IS NULL OR e.end_user_id = $end_user_id)
WITH e, score
With collect({entity: e, score: score}) AS fulltextResults

OPTIONAL MATCH (ae:ExtractedEntity)
WHERE ($end_user_id IS NULL OR ae.end_user_id = $end_user_id)
  AND ae.aliases IS NOT NULL
  AND ANY(alias IN ae.aliases WHERE toLower(alias) CONTAINS toLower($query))
WITH fulltextResults, collect(ae) AS aliasEntities

UNWIND (fulltextResults + [x IN aliasEntities | {entity: x, score:
     CASE 
       WHEN ANY(alias IN x.aliases WHERE toLower(alias) = toLower($query)) THEN 1.0
       WHEN ANY(alias IN x.aliases WHERE toLower(alias) STARTS WITH toLower($query)) THEN 0.9
       ELSE 0.8
     END
}]) AS row
WITH row.entity AS e, row.score AS score
WITH DISTINCT e, MAX(score) AS score
OPTIONAL MATCH (s:Statement)-[:REFERENCES_ENTITY]->(e)
OPTIONAL MATCH (c:Chunk)-[:CONTAINS]->(s)
RETURN e.id AS id,
       e.name AS name,
       e.end_user_id AS end_user_id,
       e.entity_type AS entity_type,
       e.created_at AS created_at,
       e.entity_idx AS entity_idx,
       e.statement_id AS statement_id,
       e.description AS description,
       e.aliases AS aliases,
       e.name_embedding AS name_embedding,
       e.connect_strength AS connect_strength,
       e.is_explicit_memory AS is_explicit_memory,
       collect(DISTINCT s.id) AS statement_ids,
       collect(DISTINCT c.id) AS chunk_ids,
       COALESCE(e.activation_value, e.importance_score, 0.5) AS activation_value,
       COALESCE(e.importance_score, 0.5) AS importance_score,
       e.last_access_time AS last_access_time,
       COALESCE(e.access_count, 0) AS access_count,
       score
ORDER BY score DESC
LIMIT $limit
"""


SEARCH_CHUNKS_BY_CONTENT = """
CALL db.index.fulltext.queryNodes("chunksFulltext", $query) YIELD node AS c, score
WHERE ($end_user_id IS NULL OR c.end_user_id = $end_user_id)
OPTIONAL MATCH (c)-[:CONTAINS]->(s:Statement)
OPTIONAL MATCH (s)-[:REFERENCES_ENTITY]->(e:ExtractedEntity)
RETURN c.id AS chunk_id,
       c.end_user_id AS end_user_id,
       c.content AS content,
       c.dialog_id AS dialog_id,
       c.sequence_number AS sequence_number,
       collect(DISTINCT s.id) AS statement_ids,
       collect(DISTINCT e.id) AS entity_ids,
       COALESCE(c.activation_value, 0.5) AS activation_value,
       c.last_access_time AS last_access_time,
       COALESCE(c.access_count, 0) AS access_count,
       score
ORDER BY score DESC
LIMIT $limit
"""

# 以下是关于第二层去重消歧与数据库进行检索的语句，在最近的规划中不再使用

# # 同组group_id下按“精确名字或别名+可选类型一致”来检索
# SECOND_LAYER_CANDIDATE_MATCH_BATCH = """
# UNWIND $rows AS row
# MATCH (e:ExtractedEntity)
# WHERE e.group_id = row.group_id
#   AND (toLower(e.name) = toLower(row.name) OR any(a IN e.aliases WHERE toLower(a) = toLower(row.name)))
#   AND (row.entity_type IS NULL OR e.entity_type = row.entity_type)
# RETURN row.id AS incoming_id,
#        e.id AS id,
#        e.name AS name,
#        e.group_id AS group_id,
#        e.entity_idx AS entity_idx,
#        e.entity_type AS entity_type,
#        e.description AS description,
#        e.statement_id AS statement_id,
#        e.aliases AS aliases,
#        e.name_embedding AS name_embedding,
#        e.fact_summary AS fact_summary,
#        e.connect_strength AS connect_strength,
#        e.created_at AS created_at,
#        e.expired_at AS expired_at
# """
# # 同组group_id下按name contains召回补充
# SECOND_LAYER_CANDIDATE_CONTAINS_BATCH = """
# UNWIND $rows AS row
# MATCH (e:ExtractedEntity)
# WHERE e.group_id = row.group_id
#   AND toLower(e.name) CONTAINS toLower(row.name)
# RETURN row.id AS incoming_id,
#        e.id AS id,
#        e.name AS name,
#        e.group_id AS group_id,
#        e.entity_idx AS entity_idx,
#        e.entity_type AS entity_type,
#        e.description AS description,
#        e.statement_id AS statement_id,
#        e.aliases AS aliases,
#        e.name_embedding AS name_embedding,
#        e.fact_summary AS fact_summary,
#        e.connect_strength AS connect_strength,
#        e.created_at AS created_at,
#        e.expired_at AS expired_at
# """

SEARCH_DIALOGUE_BY_DIALOG_ID = """
MATCH (d:Dialogue)
WHERE ($end_user_id IS NULL OR d.end_user_id = $end_user_id)
  AND d.id = $dialog_id
RETURN d.id AS dialog_id,
       d.end_user_id AS end_user_id,
       d.content AS content,
       d.created_at AS created_at
ORDER BY d.created_at DESC
LIMIT $limit
"""

SEARCH_CHUNK_BY_CHUNK_ID = """
MATCH (c:Chunk)
WHERE ($end_user_id IS NULL OR c.end_user_id = $end_user_id)
  AND c.id = $chunk_id
RETURN c.id AS chunk_id,
       c.end_user_id AS end_user_id,
       c.content AS content,
       c.dialog_id AS dialog_id,
       c.created_at AS created_at,
       c.sequence_number AS sequence_number
ORDER BY c.created_at DESC
LIMIT $limit
"""

SEARCH_STATEMENTS_BY_TEMPORAL = """
MATCH (s:Statement)
WHERE ($end_user_id IS NULL OR s.end_user_id = $end_user_id)
  AND ((($start_date IS NULL OR datetime(s.created_at) >= datetime($start_date))
  AND ($end_date IS NULL OR datetime(s.created_at) <= datetime($end_date)))
  OR (($valid_date IS NULL OR (s.valid_at IS NOT NULL AND datetime(s.valid_at) >= datetime($valid_date)))
  AND ($invalid_date IS NULL OR (s.invalid_at IS NOT NULL AND datetime(s.invalid_at) <= datetime($invalid_date)))))
RETURN s.id AS id,
       s.statement AS statement,
       s.end_user_id AS end_user_id,
       s.chunk_id AS chunk_id,
       s.created_at AS created_at,
       s.valid_at AS valid_at,
       s.invalid_at AS invalid_at,
       collect(DISTINCT s.id) AS statement_ids,
       COALESCE(s.activation_value, s.importance_score, 0.5) AS activation_value,
       COALESCE(s.importance_score, 0.5) AS importance_score,
       s.last_access_time AS last_access_time,
       COALESCE(s.access_count, 0) AS access_count
ORDER BY datetime(s.created_at) DESC
LIMIT $limit
"""

SEARCH_STATEMENTS_BY_KEYWORD_TEMPORAL = """
CALL db.index.fulltext.queryNodes("statementsFulltext", $query) YIELD node AS s, score
WHERE ($end_user_id IS NULL OR s.end_user_id = $end_user_id)
  AND ((($start_date IS NULL OR (s.created_at IS NOT NULL AND datetime(s.created_at) >= datetime($start_date)))
  AND ($end_date IS NULL OR (s.created_at IS NOT NULL AND datetime(s.created_at) <= datetime($end_date))))
  OR (($valid_date IS NULL OR (s.valid_at IS NOT NULL AND datetime(s.valid_at) >= datetime($valid_date)))
  AND ($invalid_date IS NULL OR (s.invalid_at IS NOT NULL AND datetime(s.invalid_at) <= datetime($invalid_date)))))
OPTIONAL MATCH (c:Chunk)-[:CONTAINS]->(s)
OPTIONAL MATCH (s)-[:REFERENCES_ENTITY]->(e:ExtractedEntity)
RETURN s.id AS id,
       s.statement AS statement,
       s.end_user_id AS end_user_id,
       s.chunk_id AS chunk_id,
       s.created_at AS created_at,
       s.valid_at AS valid_at,
       s.invalid_at AS invalid_at,
       c.id AS chunk_id_from_rel,
       collect(DISTINCT e.id) AS entity_ids,
       COALESCE(s.activation_value, s.importance_score, 0.5) AS activation_value,
       COALESCE(s.importance_score, 0.5) AS importance_score,
       s.last_access_time AS last_access_time,
       COALESCE(s.access_count, 0) AS access_count,
       score
ORDER BY s.created_at DESC, score DESC
LIMIT $limit
"""

SEARCH_STATEMENTS_BY_CREATED_AT = """
MATCH (n:Statement)
WHERE ($end_user_id IS NULL OR n.end_user_id = $end_user_id)
  AND ($created_at IS NOT NULL AND date(substring(n.created_at, 0, 10)) = date($created_at))
RETURN n.id AS id,
       n.statement AS statement,
       n.end_user_id AS end_user_id,
       n.chunk_id AS chunk_id,
       n.created_at AS created_at,
       n.valid_at AS valid_at,
       n.invalid_at AS invalid_at,
       collect(DISTINCT n.id) AS statement_ids
ORDER BY n.created_at DESC
LIMIT $limit
"""

SEARCH_STATEMENTS_BY_VALID_AT = """
MATCH (n:Statement)
WHERE ($end_user_id IS NULL OR n.end_user_id = $end_user_id)
  AND ($valid_at IS NOT NULL AND date(substring(n.valid_at, 0, 10)) = date($valid_at))
RETURN n.id AS id,
       n.statement AS statement,
       n.end_user_id AS end_user_id,
       n.chunk_id AS chunk_id,
       n.created_at AS created_at,
       n.valid_at AS valid_at,
       n.invalid_at AS invalid_at,
       collect(DISTINCT n.id) AS statement_ids
ORDER BY n.valid_at DESC
LIMIT $limit
"""

SEARCH_STATEMENTS_G_CREATED_AT = """
MATCH (n:Statement)
WHERE ($end_user_id IS NULL OR n.end_user_id = $end_user_id)
  AND ($created_at IS NOT NULL AND date(substring(n.created_at, 0, 19)) = date($created_at))
RETURN n.id AS id,
       n.statement AS statement,
       n.end_user_id AS end_user_id,
       n.chunk_id AS chunk_id,
       n.created_at AS created_at,
       n.valid_at AS valid_at,
       n.invalid_at AS invalid_at,
       collect(DISTINCT n.id) AS statement_ids
ORDER BY n.created_at DESC
LIMIT $limit
"""

SEARCH_STATEMENTS_L_CREATED_AT = """
MATCH (n:Statement)
WHERE ($end_user_id IS NULL OR n.end_user_id = $end_user_id)
  AND ($created_at IS NOT NULL AND date(substring(n.created_at, 0, 19)) < date($created_at))
RETURN n.id AS id,
       n.statement AS statement,
       n.end_user_id AS end_user_id,
       n.chunk_id AS chunk_id,
       n.created_at AS created_at,
       n.valid_at AS valid_at,
       n.invalid_at AS invalid_at,
       collect(DISTINCT n.id) AS statement_ids
ORDER BY n.created_at DESC
LIMIT $limit
"""

SEARCH_STATEMENTS_G_VALID_AT = """
MATCH (n:Statement)
WHERE ($end_user_id IS NULL OR n.end_user_id = $end_user_id)
  AND ($valid_at IS NOT NULL AND date(substring(n.valid_at, 0, 10)) > date($valid_at))
RETURN n.id AS id,
       n.statement AS statement,
       n.end_user_id AS end_user_id,
       n.chunk_id AS chunk_id,
       n.created_at AS created_at,
       n.valid_at AS valid_at,
       n.invalid_at AS invalid_at,
       collect(DISTINCT n.id) AS statement_ids
ORDER BY n.valid_at DESC
LIMIT $limit
"""

SEARCH_STATEMENTS_L_VALID_AT = """
MATCH (n:Statement)
WHERE ($end_user_id IS NULL OR n.end_user_id = $end_user_id)
  AND ($valid_at IS NOT NULL AND date(substring(n.valid_at, 0, 10)) < date($valid_at))
RETURN n.id AS id,
       n.statement AS statement,
       n.end_user_id AS end_user_id,
       n.chunk_id AS chunk_id,
       n.created_at AS created_at,
       n.valid_at AS valid_at,
       n.invalid_at AS invalid_at,
       collect(DISTINCT n.id) AS statement_ids
ORDER BY n.valid_at DESC
LIMIT $limit
"""

# 以下是关于第二层去重消歧与数据库进行检索的语句，在最近的规划中不再使用

# # 同组group_id下按“精确名字或别名+可选类型一致”来检索
# SECOND_LAYER_CANDIDATE_MATCH_BATCH = """
# UNWIND $rows AS row
# MATCH (e:ExtractedEntity)
# WHERE e.group_id = row.group_id
#   AND (toLower(e.name) = toLower(row.name) OR any(a IN e.aliases WHERE toLower(a) = toLower(row.name)))
#   AND (row.entity_type IS NULL OR e.entity_type = row.entity_type)
# RETURN row.id AS incoming_id,
#        e.id AS id,
#        e.name AS name,
#        e.group_id AS group_id,
#        e.entity_idx AS entity_idx,
#        e.entity_type AS entity_type,
#        e.description AS description,
#        e.statement_id AS statement_id,
#        e.aliases AS aliases,
#        e.name_embedding AS name_embedding,
#        e.fact_summary AS fact_summary,
#        e.connect_strength AS connect_strength,
#        e.created_at AS created_at,
#        e.expired_at AS expired_at
# """
# # 同组group_id下按name contains召回补充
# SECOND_LAYER_CANDIDATE_CONTAINS_BATCH = """
# UNWIND $rows AS row
# MATCH (e:ExtractedEntity)
# WHERE e.group_id = row.group_id
#   AND toLower(e.name) CONTAINS toLower(row.name)
# RETURN row.id AS incoming_id,
#        e.id AS id,
#        e.name AS name,
#        e.group_id AS group_id,
#        e.entity_idx AS entity_idx,
#        e.entity_type AS entity_type,
#        e.description AS description,
#        e.statement_id AS statement_id,
#        e.aliases AS aliases,
#        e.name_embedding AS name_embedding,
#        e.fact_summary AS fact_summary,
#        e.connect_strength AS connect_strength,
#        e.created_at AS created_at,
#        e.expired_at AS expired_at
# """

# 根据id修改句子的invalid_at的值
UPDATE_STATEMENT_INVALID_AT = """
MATCH (n:Statement {end_user_id: $end_user_id, id: $id})
SET n.invalid_at = $new_invalid_at
"""

MEMORY_SUMMARY_NODE_SAVE = """
UNWIND $summaries AS summary
MERGE (m:MemorySummary {id: summary.id})
SET m += {
    id: summary.id,
    name: summary.name,
    end_user_id: summary.end_user_id,
    run_id: summary.run_id,
    created_at: summary.created_at,
    dialog_id: summary.dialog_id,
    chunk_ids: summary.chunk_ids,
    content: summary.content,
    memory_type: summary.memory_type,
    summary_embedding: summary.summary_embedding,
    config_id: summary.config_id,
    importance_score: CASE WHEN summary.importance_score IS NOT NULL THEN summary.importance_score ELSE coalesce(m.importance_score, 0.5) END,
    activation_value: CASE WHEN summary.activation_value IS NOT NULL THEN summary.activation_value ELSE m.activation_value END,
    access_history: CASE WHEN summary.access_history IS NOT NULL THEN summary.access_history ELSE coalesce(m.access_history, []) END,
    last_access_time: CASE WHEN summary.last_access_time IS NOT NULL THEN summary.last_access_time ELSE m.last_access_time END,
    access_count: CASE WHEN summary.access_count IS NOT NULL THEN summary.access_count ELSE coalesce(m.access_count, 0) END
}
RETURN m.id AS uuid
"""

MEMORY_SUMMARY_STATEMENT_EDGE_SAVE = """
UNWIND $edges AS e
MATCH (ms:MemorySummary {id: e.summary_id, run_id: e.run_id})
MATCH (c:Chunk {id: e.chunk_id, run_id: e.run_id})
MATCH (c)-[:CONTAINS]->(s:Statement {run_id: e.run_id})
MERGE (ms)-[r:DERIVED_FROM_STATEMENT]->(s)
SET r.end_user_id = e.end_user_id,
    r.run_id = e.run_id,
    r.created_at = e.created_at
RETURN elementId(r) AS uuid
"""

# Entity Merge Query
MERGE_ENTITIES = """
MATCH (canonical:ExtractedEntity {id: $canonical_id})
MATCH (losing:ExtractedEntity {id: $losing_id})

// 更新canonical实体的aliases
SET canonical.aliases = $merged_aliases

// 转移所有从losing出发的关系到canonical
WITH canonical, losing
OPTIONAL MATCH (losing)-[r]->(target)
WHERE NOT (canonical)-[:RELATES_TO]->(target)
FOREACH (rel IN CASE WHEN r IS NOT NULL THEN [r] ELSE [] END |
    CREATE (canonical)-[:RELATES_TO {
        id: rel.id,
        relation_type: rel.relation_type,
        relation_value: rel.relation_value,
        statement: rel.statement,
        source_statement_id: rel.source_statement_id,
        valid_at: rel.valid_at,
        invalid_at: rel.invalid_at,
        end_user_id: rel.end_user_id,
        user_id: rel.user_id,
        apply_id: rel.apply_id,
        run_id: rel.run_id,
        created_at: rel.created_at
    }]->(target)
)

// 转移所有指向losing的关系到canonical
WITH canonical, losing
OPTIONAL MATCH (source)-[r]->(losing)
WHERE NOT (source)-[:RELATES_TO]->(canonical)
FOREACH (rel IN CASE WHEN r IS NOT NULL THEN [r] ELSE [] END |
    CREATE (source)-[:RELATES_TO {
        id: rel.id,
        relation_type: rel.relation_type,
        relation_value: rel.relation_value,
        statement: rel.statement,
        source_statement_id: rel.source_statement_id,
        valid_at: rel.valid_at,
        invalid_at: rel.invalid_at,
        end_user_id: rel.end_user_id,
        user_id: rel.user_id,
        apply_id: rel.apply_id,
        run_id: rel.run_id,
        created_at: rel.created_at
    }]->(canonical)
)

// 删除losing实体及其所有关系
WITH losing
DETACH DELETE losing

RETURN count(losing) as deleted
"""

neo4j_statement_part = '''
MATCH (n:Statement)
WHERE n.end_user_id = "{}" 
  AND datetime(n.created_at) >= datetime() - duration('P3D')
RETURN 
  n.statement as statement_name,
  n.id as statement_id,
   n.created_at as   statement_created_at

'''
neo4j_statement_all = '''
MATCH (n:Statement)
WHERE n.end_user_id = "{}" 
RETURN 
  n.statement as statement_name,
  n.id as statement_id

'''
neo4j_query_part = """
            MATCH (n)-[r]-(m:ExtractedEntity)
            WHERE n.end_user_id = "{}" 
            AND datetime(n.created_at) >= datetime() - duration('P3D')
            WITH DISTINCT m
            OPTIONAL MATCH (m)-[rel]-(other:ExtractedEntity)
            RETURN 
             elementId(m) as id,
            m.name as entity1_name,
            m.description as description,
            m.statement_id as statement_id,
            m.created_at as created_at,
            CASE WHEN rel IS NULL THEN "NO_RELATIONSHIP" ELSE type(rel) END as relationship_type,
              elementId(rel) as rel_id,
            rel.predicate as predicate,
            rel.statement as relationship,
            rel.statement_id as relationship_statement_id,
            CASE WHEN other IS NULL THEN "ISOLATED_NODE" ELSE other.name END as entity2_name,
            other as entity2
                          """
neo4j_query_all = """
                MATCH (n)-[r]-(m:ExtractedEntity)
                WHERE n.end_user_id = "{}" 
                WITH DISTINCT m
                OPTIONAL MATCH (m)-[rel]-(other:ExtractedEntity)
                RETURN 
                 elementId(m) as id,
                m.name as entity1_name,
                m.description as description,
                m.statement_id as statement_id,
                m.created_at as created_at,
                CASE WHEN rel IS NULL THEN "NO_RELATIONSHIP" ELSE type(rel) END as relationship_type,
                  elementId(rel) as rel_id,
                rel.predicate as predicate,
                rel.statement as relationship,
                rel.statement_id as relationship_statement_id,
                CASE WHEN other IS NULL THEN "ISOLATED_NODE" ELSE other.name END as entity2_name,
                other as entity2
                          """

'''针对当前节点下扩长的句子，实体和总结'''
Memory_Timeline_ExtractedEntity = """
MATCH (n)-[r1]-(e)-[r2]-(ms)
WHERE elementId(n) = $id
  AND (ms:ExtractedEntity OR ms:MemorySummary)

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

CALL {
  WITH n
  MATCH (n)-[]-(m:ExtractedEntity)
  WHERE NOT m:MemorySummary AND NOT m:Chunk
  RETURN collect(
    DISTINCT {
      text: m.name,
      created_at: m.created_at,
      type: "情景记忆" 
    }
  ) AS ExtractedEntity
}

CALL {
  WITH n
  MATCH (n)-[]-(m:MemorySummary)
  WHERE NOT m:Chunk
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

'''针对当前节点，主要获取更加完整的句子节点'''
Memory_Space_Emotion_Statement = """
MATCH (n)
WHERE elementId(n) = $id
RETURN
  n.emotion_intensity AS emotion_intensity,
  n.created_at        AS created_at,
  n.emotion_type      AS emotion_type,
  n.statement         AS statement;

"""
Memory_Space_Emotion_MemorySummary = """
MATCH (n)-[]-(e)
WHERE elementId(n) = $id
  AND EXISTS {
    MATCH (e)-[]-(ms)
    WHERE ms:MemorySummary OR ms:ExtractedEntity
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
  AND EXISTS {
    MATCH (e)-[]-(ms:ExtractedEntity)
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
return DISTINCT elementId(m) as id
"""
Memory_Space_Entity = """
MATCH (n)-[]-(m)
WHERE elementId(m) = $id AND  m.entity_type = "Person"
RETURN
DISTINCT m.name as name,m.end_user_id as end_user_id
"""
Memory_Space_Associative = """
MATCH (u)-[]-(x)-[]-(h)
WHERE elementId(u) = $user_id
  AND elementId(h) = $id
RETURN DISTINCT
 x.statement as statement,x.created_at as created_at
"""

# ============================================================
# graph_data 接口的参数化 Cypher 查询（spec: graph-data-per-type-limit）
# ============================================================
# 以下查询用于支撑 GET /api/memory-storage/analytics/graph_data 的
# 「按类型独立 LIMIT + 批量关联计数 + 全量计数」流水线（参见
# .kiro/specs/graph-data-per-type-limit/design.md）。
#
# 索引命中的关键约束（Neo4j 5.x）：
#   `end_user_id` 范围索引是 label-property 索引（``FOR (n:Label) ON (n.end_user_id)``），
#   规划器只有在 **label 出现在 MATCH 模式里**（``MATCH (n:Label)``）时才会用
#   NodeIndexSeek。若把 label 放进 WHERE 用 ``labels(n)[0] = $node_type``，
#   规划器无法绑定具体 label 索引，只能 AllNodesScan 全表扫描。动态 label
#   (``MATCH (n:$($node_type))``, Neo4j 5.26+) 同样无法稳定命中索引。
#   因此 Q1/Q4 改为「把白名单内的 label 字面量内联进 MATCH 模式」的 builder 形式。
#   label 取自 :data:`SUPPORTED_NODE_TYPES`（已 ``isidentifier`` 校验 + 白名单过滤），
#   字面量内联不存在注入风险。


def build_graph_nodes_by_type_query(node_type: str) -> str:
    """构建「按单个 Node_Type 检索节点」的 Cypher（Q1），label 字面量内联以命中索引。

    Neo4j 不允许 ``LIMIT`` 引用运行期变量，故 ``$limit`` 仍以静态参数下发；
    service 层 helper 对每个非零 ``Per_Type_Limit`` 类型循环调用一次本 builder，
    整体调用次数与节点总数 N 无关（最多 = 类型数，常数级），符合 Requirement 6.4。

    Args:
        node_type: 节点 label，必须属于 :data:`SUPPORTED_NODE_TYPES`。

    Returns:
        把 ``node_type`` 内联进 ``MATCH (n:<label>)`` 后的 Cypher 字符串；
        运行期参数为 ``$end_user_id`` (STRING) 与 ``$limit`` (INTEGER)。

    Raises:
        ValueError: 当 ``node_type`` 不在 :data:`SUPPORTED_NODE_TYPES` 中时
            （防止任何未经白名单校验的字符串被内联进 Cypher）。
    """
    if node_type not in SUPPORTED_NODE_TYPES:
        raise ValueError(f"不支持的 Node_Type，拒绝内联进 Cypher: {node_type!r}")
    return f"""
// GRAPH_NODES_BY_TYPE_LIMITS({node_type})
MATCH (n:{node_type})
WHERE n.end_user_id = $end_user_id
RETURN
    elementId(n)        AS id,
    labels(n)           AS labels,
    properties(n)       AS properties
LIMIT $limit
"""


def build_graph_total_count_by_type_query(node_type: str) -> str:
    """构建「单个 Node_Type 全量计数」的 Cypher（Q4），label 字面量内联以命中索引。

    取代旧版「``MATCH (n) WHERE labels(n)[0] IN $supported_types``」单查询——
    后者无 label 在模式里，会对全库做 AllNodesScan。改为按类型 NodeIndexSeek，
    service 层循环调用并合并为 ``{label: total}``（调用次数 = 类型数，常数级）。

    Args:
        node_type: 节点 label，必须属于 :data:`SUPPORTED_NODE_TYPES`。

    Returns:
        把 ``node_type`` 内联进 ``MATCH (n:<label>)`` 后的计数 Cypher；
        运行期参数为 ``$end_user_id`` (STRING)。

    Raises:
        ValueError: 当 ``node_type`` 不在 :data:`SUPPORTED_NODE_TYPES` 中时。
    """
    if node_type not in SUPPORTED_NODE_TYPES:
        raise ValueError(f"不支持的 Node_Type，拒绝内联进 Cypher: {node_type!r}")
    return f"""
// GRAPH_NODES_TOTAL_COUNT_BY_TYPE({node_type})
MATCH (n:{node_type})
WHERE n.end_user_id = $end_user_id
RETURN count(n) AS total
"""


# Q2：批量查询若干节点的关联边总数，取代旧实现里每节点一次的 N+1 子查询。
# Q2 按 ``elementId(n)`` 直接定位节点，无需 label，不涉及上面的索引命中问题。
#
# 注意：Neo4j 5 已移除 ``size((n)--())`` 这种「pattern expression in size()」用法，
# 必须改用 ``COUNT { (n)--() }`` 子查询表达式（见
# ``Neo.ClientError.Statement.SyntaxError 50N42 — A pattern expression should
# only be used in order to test the existence of a pattern. It can no longer be
# used inside the function size(), an alternative is to replace size() with
# COUNT {}.``）。
#
# 参数：$node_ids (LIST<STRING>)
GRAPH_NODES_REL_COUNT_BATCH = """
// GRAPH_NODES_REL_COUNT_BATCH
UNWIND $node_ids AS nid
MATCH (n) WHERE elementId(n) = nid
RETURN nid AS id, COUNT { (n)--() } AS rel_count
"""

# DEPRECATED: Graph_Node_query 已被 build_graph_nodes_by_type_query() 取代，
# 后者按 SUPPORTED_NODE_TYPES 内联 label 字面量，可命中 end_user_id 索引并支持
# 独立 Per_Type_Limit。此常量仅保留以避免破坏式改动；新代码不应再使用。
Graph_Node_query = """
MATCH (n:MemorySummary)
WHERE n.end_user_id = $end_user_id
RETURN
  elementId(n) AS id,
  labels(n) AS labels,
  properties(n) AS properties,
  0 AS priority
LIMIT $limit
                
UNION ALL

MATCH (n:Dialogue)
WHERE n.end_user_id =  $end_user_id
RETURN
  elementId(n) AS id,
  labels(n) AS labels,
  properties(n) AS properties,
  1 AS priority
LIMIT 1

UNION ALL

MATCH (n:Statement)
WHERE n.end_user_id =  $end_user_id
RETURN
  elementId(n) AS id,
  labels(n) AS labels,
  properties(n) AS properties,
  1 AS priority
LIMIT $limit

UNION ALL

MATCH (n:ExtractedEntity)
WHERE n.end_user_id =  $end_user_id
RETURN
  elementId(n) AS id,
  labels(n) AS labels,
  properties(n) AS properties,
  2 AS priority
LIMIT $limit

UNION ALL

MATCH (n:Chunk)
WHERE n.end_user_id =  $end_user_id
RETURN
  elementId(n) AS id,
  labels(n) AS labels,
  properties(n) AS properties,
  3 AS priority
LIMIT $limit

UNION ALL
MATCH (n:Perceptual)
WHERE n.end_user_id = $end_user_id
RETURN
  elementId(n) AS id,
  labels(n) AS labels,
  properties(n) AS properties,
  4 AS priority

"""

# ============================================================
# Community 节点 & BELONGS_TO_COMMUNITY 边
# ============================================================

# ─── Community 聚类相关 Cypher 模板 ───────────────────────────────────────────

COMMUNITY_NODE_UPSERT = """
MERGE (c:Community {community_id: $community_id})
ON CREATE SET c.id = $community_id
SET c.end_user_id = $end_user_id,
    c.member_count = $member_count,
    c.updated_at = datetime()
RETURN c.community_id AS community_id
"""

ENTITY_JOIN_COMMUNITY = """
MATCH (e:ExtractedEntity {id: $entity_id, end_user_id: $end_user_id})
MATCH (c:Community {community_id: $community_id, end_user_id: $end_user_id})
MERGE (e)-[:BELONGS_TO_COMMUNITY]->(c)
SET c.updated_at = datetime()
RETURN e.id AS entity_id, c.community_id AS community_id
"""

ENTITY_LEAVE_ALL_COMMUNITIES = """
MATCH (e:ExtractedEntity {id: $entity_id, end_user_id: $end_user_id})
MATCH (e)-[r:BELONGS_TO_COMMUNITY]->(:Community)
DELETE r
"""

GET_ENTITY_NEIGHBORS = """
MATCH (e:ExtractedEntity {id: $entity_id, end_user_id: $end_user_id})

// 来源一：直接关系邻居（EXTRACTED_RELATIONSHIP 边）
OPTIONAL MATCH (e)-[:EXTRACTED_RELATIONSHIP]-(nb1:ExtractedEntity {end_user_id: $end_user_id})

// 来源二：同 Statement 共现邻居（REFERENCES_ENTITY 边）
OPTIONAL MATCH (s:Statement)-[:REFERENCES_ENTITY]->(e)
OPTIONAL MATCH (s)-[:REFERENCES_ENTITY]->(nb2:ExtractedEntity {end_user_id: $end_user_id})
WHERE nb2.id <> e.id

WITH collect(DISTINCT nb1) + collect(DISTINCT nb2) AS all_neighbors
UNWIND all_neighbors AS nb
WITH nb WHERE nb IS NOT NULL
OPTIONAL MATCH (nb)-[:BELONGS_TO_COMMUNITY]->(c:Community)
RETURN DISTINCT
    nb.id               AS id,
    nb.name             AS name,
    nb.name_embedding   AS name_embedding,
    nb.activation_value AS activation_value,
    CASE WHEN c IS NOT NULL THEN c.community_id ELSE null END AS community_id
"""

GET_ALL_ENTITIES_FOR_USER = """
MATCH (e:ExtractedEntity {end_user_id: $end_user_id})
OPTIONAL MATCH (e)-[:BELONGS_TO_COMMUNITY]->(c:Community)
RETURN e.id AS id,
       e.name AS name,
       e.name_embedding AS name_embedding,
       e.activation_value AS activation_value,
       CASE WHEN c IS NOT NULL THEN c.community_id ELSE null END AS community_id
"""

GET_ENTITY_COUNT_FOR_USER = """
MATCH (e:ExtractedEntity {end_user_id: $end_user_id})
RETURN count(e) AS entity_count
"""

GET_ALL_ENTITY_IDS_FOR_USER = """
MATCH (e:ExtractedEntity {end_user_id: $end_user_id})
RETURN e.id AS id
"""

GET_COMMUNITY_MEMBERS = """
MATCH (e:ExtractedEntity {end_user_id: $end_user_id})-[:BELONGS_TO_COMMUNITY]->(c:Community {community_id: $community_id})
RETURN e.id AS id, e.name AS name, e.entity_type AS entity_type,
       e.importance_score AS importance_score, e.activation_value AS activation_value,
       e.name_embedding AS name_embedding,
       e.aliases AS aliases, e.description AS description,
       e.example AS example
ORDER BY coalesce(e.activation_value, 0) DESC
"""

GET_COMMUNITY_RELATIONSHIPS = """
MATCH (e1:ExtractedEntity {end_user_id: $end_user_id})-[:BELONGS_TO_COMMUNITY]->(c:Community {community_id: $community_id})
MATCH (e2:ExtractedEntity {end_user_id: $end_user_id})-[:BELONGS_TO_COMMUNITY]->(c)
MATCH (e1)-[r:EXTRACTED_RELATIONSHIP]->(e2)
RETURN e1.name AS subject, r.predicate AS predicate, e2.name AS object
ORDER BY e1.name, r.predicate, e2.name
LIMIT 20
"""

GET_ALL_COMMUNITY_MEMBERS_BATCH = """
MATCH (e:ExtractedEntity {end_user_id: $end_user_id})-[:BELONGS_TO_COMMUNITY]->(c:Community)
RETURN c.community_id AS community_id,
       e.id AS id, e.name AS name, e.entity_type AS entity_type,
       e.importance_score AS importance_score, e.activation_value AS activation_value,
       e.name_embedding AS name_embedding,
       e.aliases AS aliases, e.description AS description
ORDER BY c.community_id, coalesce(e.activation_value, 0) DESC
"""

CHECK_USER_HAS_COMMUNITIES = """
MATCH (c:Community {end_user_id: $end_user_id})
RETURN count(c) AS community_count
"""

UPDATE_COMMUNITY_MEMBER_COUNT = """
MATCH (e:ExtractedEntity {end_user_id: $end_user_id})-[:BELONGS_TO_COMMUNITY]->(c:Community {community_id: $community_id})
WITH c, count(e) AS cnt
SET c.member_count = cnt
RETURN c.community_id AS community_id, cnt AS member_count
"""

UPDATE_COMMUNITY_METADATA = """
MATCH (c:Community {community_id: $community_id, end_user_id: $end_user_id})
SET c.id               = coalesce(c.id, $community_id),
    c.name             = $name,
    c.summary          = $summary,
    c.core_entities    = $core_entities,
    c.summary_embedding = $summary_embedding,
    c.updated_at       = datetime()
RETURN c.community_id AS community_id
"""

BATCH_UPDATE_COMMUNITY_METADATA = """
UNWIND $communities AS row
MATCH (c:Community {community_id: row.community_id, end_user_id: row.end_user_id})
SET c.id               = coalesce(c.id, row.community_id),
    c.name             = row.name,
    c.summary          = row.summary,
    c.core_entities    = row.core_entities,
    c.summary_embedding = row.summary_embedding,
    c.updated_at       = datetime()
RETURN c.community_id AS community_id
"""

GET_ENTITIES_PAGE = """
MATCH (e:ExtractedEntity {end_user_id: $end_user_id})
OPTIONAL MATCH (e)-[:BELONGS_TO_COMMUNITY]->(c:Community)
RETURN e.id AS id,
       e.name AS name,
       e.name_embedding AS name_embedding,
       e.activation_value AS activation_value,
       CASE WHEN c IS NOT NULL THEN c.community_id ELSE null END AS community_id
ORDER BY e.id
SKIP $skip LIMIT $limit
"""

GET_ENTITY_NEIGHBORS_BATCH_FOR_IDS = """
// 批量拉取指定实体列表的邻居（用于分批全量聚类）
MATCH (e:ExtractedEntity {end_user_id: $end_user_id})
WHERE e.id IN $entity_ids
OPTIONAL MATCH (e)-[:EXTRACTED_RELATIONSHIP]-(nb1:ExtractedEntity {end_user_id: $end_user_id})
OPTIONAL MATCH (s:Statement)-[:REFERENCES_ENTITY]->(e)
OPTIONAL MATCH (s)-[:REFERENCES_ENTITY]->(nb2:ExtractedEntity {end_user_id: $end_user_id})
WHERE nb2.id <> e.id
WITH e, collect(DISTINCT nb1) + collect(DISTINCT nb2) AS all_neighbors
UNWIND all_neighbors AS nb
WITH e, nb WHERE nb IS NOT NULL
OPTIONAL MATCH (nb)-[:BELONGS_TO_COMMUNITY]->(c:Community)
RETURN DISTINCT
    e.id                AS entity_id,
    nb.id               AS id,
    nb.name             AS name,
    nb.name_embedding   AS name_embedding,
    nb.activation_value AS activation_value,
    CASE WHEN c IS NOT NULL THEN c.community_id ELSE null END AS community_id
"""

GET_ALL_ENTITY_NEIGHBORS_BATCH = """
// 批量拉取某用户下所有实体的邻居（用于全量聚类预加载）
MATCH (e:ExtractedEntity {end_user_id: $end_user_id})

// 来源一：直接关系邻居
OPTIONAL MATCH (e)-[:EXTRACTED_RELATIONSHIP]-(nb1:ExtractedEntity {end_user_id: $end_user_id})

// 来源二：同 Statement 共现邻居
OPTIONAL MATCH (s:Statement)-[:REFERENCES_ENTITY]->(e)
OPTIONAL MATCH (s)-[:REFERENCES_ENTITY]->(nb2:ExtractedEntity {end_user_id: $end_user_id})
WHERE nb2.id <> e.id

WITH e, collect(DISTINCT nb1) + collect(DISTINCT nb2) AS all_neighbors
UNWIND all_neighbors AS nb
WITH e, nb WHERE nb IS NOT NULL
OPTIONAL MATCH (nb)-[:BELONGS_TO_COMMUNITY]->(c:Community)
RETURN DISTINCT
    e.id                AS entity_id,
    nb.id               AS id,
    nb.name             AS name,
    nb.name_embedding   AS name_embedding,
    nb.activation_value AS activation_value,
    CASE WHEN c IS NOT NULL THEN c.community_id ELSE null END AS community_id
"""

GET_COMMUNITY_GRAPH_DATA = """
MATCH (c:Community {end_user_id: $end_user_id})
MATCH (e:ExtractedEntity {end_user_id: $end_user_id})-[b:BELONGS_TO_COMMUNITY]->(c)
OPTIONAL MATCH (e)-[r:EXTRACTED_RELATIONSHIP]-(e2:ExtractedEntity {end_user_id: $end_user_id})
RETURN
    elementId(c)          AS c_id,
    properties(c)         AS c_props,
    elementId(e)          AS e_id,
    properties(e)         AS e_props,
    elementId(b)          AS b_id,
    elementId(e2)         AS e2_id,
    properties(e2)        AS e2_props,
    elementId(r)          AS r_id,
    type(r)               AS r_type,
    properties(r)         AS r_props,
    startNode(r) = e      AS r_from_e
"""

CHECK_COMMUNITY_IS_COMPLETE = """
MATCH (c:Community {community_id: $community_id, end_user_id: $end_user_id})
RETURN (
    c.name IS NOT NULL AND c.name <> '' AND
    c.summary IS NOT NULL AND c.summary <> '' AND
    c.core_entities IS NOT NULL
) AS is_complete
"""

CHECK_COMMUNITY_IS_COMPLETE_WITH_EMBEDDING = """
MATCH (c:Community {community_id: $community_id, end_user_id: $end_user_id})
RETURN (
    c.name IS NOT NULL AND c.name <> '' AND
    c.summary IS NOT NULL AND c.summary <> '' AND
    c.core_entities IS NOT NULL AND
    c.summary_embedding IS NOT NULL
) AS is_complete
"""

GET_INCOMPLETE_COMMUNITIES = """
MATCH (c:Community {end_user_id: $end_user_id})
WHERE c.name IS NULL OR c.summary IS NULL OR c.core_entities IS NULL
   OR c.name = '' OR c.summary = ''
RETURN c.community_id AS community_id
"""

GET_INCOMPLETE_COMMUNITIES_WITH_EMBEDDING = """
MATCH (c:Community {end_user_id: $end_user_id})
WHERE c.name IS NULL OR c.name = ''
   OR c.summary IS NULL OR c.summary = ''
   OR c.core_entities IS NULL
   OR (c.summary_embedding IS NULL AND c.summary IS NOT NULL AND c.summary <> '(empty)')
RETURN c.community_id AS community_id
"""

# Community 向量检索 ──────────────────────────────────────────────────
# Community embedding-based search: cosine similarity on Community.summary_embedding
COMMUNITY_EMBEDDING_SEARCH = """
CALL db.index.vector.queryNodes('community_summary_embedding_index', $limit * 100, $embedding)
YIELD node AS c, score
WHERE c.summary_embedding IS NOT NULL
  AND ($end_user_id IS NULL OR c.end_user_id = $end_user_id)
RETURN c.community_id AS id,
       c.name AS name,
       c.summary AS content,
       c.core_entities AS core_entities,
       c.member_count AS member_count,
       c.end_user_id AS end_user_id,
       c.updated_at AS updated_at,
       score
ORDER BY score DESC
LIMIT $limit
"""

# Community 展开检索 ──────────────────────────────────────────────────
# 命中社区后，拉取该社区所有成员实体关联的 Statement 节点（主题→细节两级检索）
EXPAND_COMMUNITY_STATEMENTS = """
MATCH (c:Community {community_id: $community_id})
MATCH (e:ExtractedEntity)-[:BELONGS_TO_COMMUNITY]->(c)
MATCH (s:Statement)-[:REFERENCES_ENTITY]->(e)
WHERE s.end_user_id = $end_user_id
RETURN s.statement AS statement,
       s.id AS id,
       s.end_user_id AS end_user_id,
       s.created_at AS created_at,
       s.valid_at AS valid_at,
       s.invalid_at AS invalid_at,
       COALESCE(s.activation_value, s.importance_score, 0.5) AS activation_value,
       COALESCE(s.importance_score, 0.5) AS importance_score,
       e.name AS source_entity,
       c.name AS community_name
ORDER BY COALESCE(s.activation_value, 0) DESC
LIMIT $limit
"""

# 感知记忆节点保存
PERCEPTUAL_NODE_SAVE = """
UNWIND $perceptuals AS p
MERGE (n:Perceptual {id: p.id})
SET n += {
    id: p.id,
    end_user_id: p.end_user_id,
    perceptual_type: p.perceptual_type,
    file_path: p.file_path,
    file_name: p.file_name,
    file_ext: p.file_ext,
    summary: p.summary,
    keywords: p.keywords,
    topic: p.topic,
    domain: p.domain,
    created_at: p.created_at,
    file_type: p.file_type,
    summary_embedding: p.summary_embedding
}
RETURN n.id AS uuid
"""

# 感知记忆与对话的关联边
PERCEPTUAL_CHUNK_EDGE_SAVE = """
UNWIND $edges AS edge
MATCH (p:Perceptual {id: edge.perceptual_id, end_user_id: edge.end_user_id})
MATCH (c:Chunk {id: edge.chunk_id, end_user_id: edge.end_user_id})
MERGE (c)-[r:HAS_PERCEPTUAL]->(p)
ON CREATE SET r.end_user_id = edge.end_user_id,
    r.created_at = edge.created_at
RETURN elementId(r) AS uuid
"""

# -------------------
# search by user id
# -------------------
SEARCH_PERCEPTUAL_BY_USER_ID = """
MATCH (p:Perceptual)
WHERE p.end_user_id = $end_user_id
RETURN p.id AS id,
       p.summary_embedding AS embedding
"""

SEARCH_STATEMENTS_BY_USER_ID = """
MATCH (s:Statement)
WHERE s.end_user_id = $end_user_id
RETURN s.id AS id,
       s.statement_embedding AS embedding
"""

SEARCH_ENTITIES_BY_USER_ID = """
MATCH (e:ExtractedEntity)
WHERE e.end_user_id = $end_user_id
RETURN e.id AS id,
       e.name_embedding AS embedding
"""

SEARCH_CHUNKS_BY_USER_ID = """
MATCH (c:Chunk)
WHERE c.end_user_id = $end_user_id
RETURN c.id AS id,
       c.chunk_embedding AS embedding
"""

SEARCH_MEMORY_SUMMARIES_BY_USER_ID = """
MATCH (s:MemorySummary)
WHERE s.end_user_id = $end_user_id
RETURN s.id AS id,
       s.summary_embedding AS embedding
"""

SEARCH_COMMUNITIES_BY_USER_ID = """
MATCH (c:Community)
WHERE c.end_user_id = $end_user_id
RETURN c.community_id AS id,
       c.summary_embedding AS embedding
"""

# -------------------
# search by id
# -------------------
SEARCH_PERCEPTUAL_BY_IDS = """
MATCH (p:Perceptual)
WHERE p.id IN $ids
RETURN p.id AS id,
       p.end_user_id AS end_user_id,
       p.perceptual_type AS perceptual_type,
       p.file_path AS file_path,
       p.file_name AS file_name,
       p.file_ext AS file_ext,
       p.summary AS summary,
       p.keywords AS keywords,
       p.topic AS topic,
       p.domain AS domain,
       p.created_at AS created_at,
       p.file_type AS file_type
"""

SEARCH_STATEMENTS_BY_IDS = """
MATCH (s:Statement)
WHERE s.id IN $ids
RETURN s.id AS id,
       s.statement AS statement,
       s.end_user_id AS end_user_id,
       s.chunk_id AS chunk_id,
       s.created_at AS created_at,
       s.expired_at AS expired_at,
       s.valid_at AS valid_at,
       properties(s)['invalid_at'] AS invalid_at,
       COALESCE(s.activation_value, s.importance_score, 0.5) AS activation_value,
       COALESCE(s.importance_score, 0.5) AS importance_score,
       s.last_access_time AS last_access_time,
       COALESCE(s.access_count, 0) AS access_count
"""

SEARCH_CHUNKS_BY_IDS = """
MATCH (c:Chunk)
WHERE c.id IN $ids
RETURN c.id AS id,
       c.end_user_id AS end_user_id,
       c.content AS content,
       c.dialog_id AS dialog_id,
       COALESCE(c.activation_value, 0.5) AS activation_value,
       c.last_access_time AS last_access_time,
       COALESCE(c.access_count, 0) AS access_count
"""

SEARCH_ENTITIES_BY_IDS = """
MATCH (e:ExtractedEntity)
WHERE e.id IN $ids
RETURN e.id AS id,
       e.name AS name,
       e.end_user_id AS end_user_id,
       e.entity_type AS entity_type,
       e.description AS description,
       COALESCE(e.activation_value, e.importance_score, 0.5) AS activation_value,
       COALESCE(e.importance_score, 0.5) AS importance_score,
       e.last_access_time AS last_access_time,
       COALESCE(e.access_count, 0) AS access_count
"""

SEARCH_MEMORY_SUMMARIES_BY_IDS = """
MATCH (m:MemorySummary)
WHERE m.id IN $ids
RETURN m.id AS id,
       m.name AS name,
       m.end_user_id AS end_user_id,
       m.dialog_id AS dialog_id,
       m.chunk_ids AS chunk_ids,
       m.content AS content,
       m.created_at AS created_at,
       COALESCE(m.activation_value, m.importance_score, 0.5) AS activation_value,
       COALESCE(m.importance_score, 0.5) AS importance_score,
       m.last_access_time AS last_access_time,
       COALESCE(m.access_count, 0) AS access_count
"""

SEARCH_COMMUNITIES_BY_IDS = """
MATCH (c:Community)
WHERE c.id IN $ids
RETURN c.id AS id,
       c.name AS name,
       c.summary AS content,
       c.core_entities AS core_entities,
       c.member_count AS member_count,
       c.end_user_id AS end_user_id,
       c.updated_at AS updated_at
"""
# -------------------
# search by fulltext
# -------------------
SEARCH_PERCEPTUALS_BY_KEYWORD = """
CALL db.index.fulltext.queryNodes("perceptualFulltext", $query) YIELD node AS p, score
WHERE p.end_user_id = $end_user_id
RETURN p.id AS id,
       p.end_user_id AS end_user_id,
       p.perceptual_type AS perceptual_type,
       p.file_path AS file_path,
       p.file_name AS file_name,
       p.file_ext AS file_ext,
       p.summary AS summary,
       p.keywords AS keywords,
       p.topic AS topic,
       p.domain AS domain,
       p.created_at AS created_at,
       p.file_type AS file_type,
       score
ORDER BY score DESC
LIMIT $limit
"""

SEARCH_STATEMENTS_BY_KEYWORD = """
CALL db.index.fulltext.queryNodes("statementsFulltext", $query) YIELD node AS s, score
WHERE ($end_user_id IS NULL OR s.end_user_id = $end_user_id)
OPTIONAL MATCH (c:Chunk)-[:CONTAINS]->(s)
OPTIONAL MATCH (s)-[:REFERENCES_ENTITY]->(e:ExtractedEntity)
RETURN s.id AS id,
       s.statement AS statement,
       s.end_user_id AS end_user_id,
       s.chunk_id AS chunk_id,
       s.created_at AS created_at,
       s.expired_at AS expired_at,
       s.valid_at AS valid_at,
       properties(s)['invalid_at'] AS invalid_at,
       c.id AS chunk_id_from_rel,
       collect(DISTINCT e.id) AS entity_ids,
       COALESCE(s.activation_value, s.importance_score, 0.5) AS activation_value,
       COALESCE(s.importance_score, 0.5) AS importance_score,
       s.last_access_time AS last_access_time,
       COALESCE(s.access_count, 0) AS access_count,
       score
ORDER BY score DESC
LIMIT $limit
"""

SEARCH_ENTITIES_BY_NAME_OR_ALIAS = """
CALL db.index.fulltext.queryNodes("entitiesFulltext", $query) YIELD node AS e, score
WHERE ($end_user_id IS NULL OR e.end_user_id = $end_user_id)
WITH e, score
With collect({entity: e, score: score}) AS fulltextResults

OPTIONAL MATCH (ae:ExtractedEntity)
WHERE ($end_user_id IS NULL OR ae.end_user_id = $end_user_id)
  AND ae.aliases IS NOT NULL
  AND ANY(alias IN ae.aliases WHERE toLower(alias) CONTAINS toLower($query))
WITH fulltextResults, collect(ae) AS aliasEntities

UNWIND (fulltextResults + [x IN aliasEntities | {entity: x, score:
     CASE 
       WHEN ANY(alias IN x.aliases WHERE toLower(alias) = toLower($query)) THEN 1.0
       WHEN ANY(alias IN x.aliases WHERE toLower(alias) STARTS WITH toLower($query)) THEN 0.9
       ELSE 0.8
     END
}]) AS row
WITH row.entity AS e, row.score AS score
WITH DISTINCT e, MAX(score) AS score
OPTIONAL MATCH (s:Statement)-[:REFERENCES_ENTITY]->(e)
OPTIONAL MATCH (c:Chunk)-[:CONTAINS]->(s)
RETURN e.id AS id,
       e.name AS name,
       e.end_user_id AS end_user_id,
       e.entity_type AS entity_type,
       e.created_at AS created_at,
       e.expired_at AS expired_at,
       e.entity_idx AS entity_idx,
       e.statement_id AS statement_id,
       e.description AS description,
       e.aliases AS aliases,
       e.name_embedding AS name_embedding,
       e.connect_strength AS connect_strength,
       collect(DISTINCT s.id) AS statement_ids,
       collect(DISTINCT c.id) AS chunk_ids,
       COALESCE(e.activation_value, e.importance_score, 0.5) AS activation_value,
       COALESCE(e.importance_score, 0.5) AS importance_score,
       e.last_access_time AS last_access_time,
       COALESCE(e.access_count, 0) AS access_count,
       score
ORDER BY score DESC
LIMIT $limit
"""

SEARCH_CHUNKS_BY_CONTENT = """
CALL db.index.fulltext.queryNodes("chunksFulltext", $query) YIELD node AS c, score
WHERE ($end_user_id IS NULL OR c.end_user_id = $end_user_id)
OPTIONAL MATCH (c)-[:CONTAINS]->(s:Statement)
OPTIONAL MATCH (s)-[:REFERENCES_ENTITY]->(e:ExtractedEntity)
RETURN c.id AS id,
       c.end_user_id AS end_user_id,
       c.content AS content,
       c.dialog_id AS dialog_id,
       c.sequence_number AS sequence_number,
       collect(DISTINCT s.id) AS statement_ids,
       collect(DISTINCT e.id) AS entity_ids,
       COALESCE(c.activation_value, 0.5) AS activation_value,
       c.last_access_time AS last_access_time,
       COALESCE(c.access_count, 0) AS access_count,
       score
ORDER BY score DESC
LIMIT $limit
"""

# MemorySummary keyword search using fulltext index
SEARCH_MEMORY_SUMMARIES_BY_KEYWORD = """
CALL db.index.fulltext.queryNodes("summariesFulltext", $query) YIELD node AS m, score
WHERE ($end_user_id IS NULL OR m.end_user_id = $end_user_id)
OPTIONAL MATCH (m)-[:DERIVED_FROM_STATEMENT]->(s:Statement)
RETURN m.id AS id,
       m.name AS name,
       m.end_user_id AS end_user_id,
       m.dialog_id AS dialog_id,
       m.chunk_ids AS chunk_ids,
       m.content AS content,
       m.created_at AS created_at,
       COALESCE(m.activation_value, m.importance_score, 0.5) AS activation_value,
       COALESCE(m.importance_score, 0.5) AS importance_score,
       m.last_access_time AS last_access_time,
       COALESCE(m.access_count, 0) AS access_count,
       score
ORDER BY score DESC
LIMIT $limit
"""

# Community keyword search: matches name or summary via fulltext index
SEARCH_COMMUNITIES_BY_KEYWORD = """
CALL db.index.fulltext.queryNodes("communitiesFulltext", $query) YIELD node AS c, score
WHERE ($end_user_id IS NULL OR c.end_user_id = $end_user_id)
RETURN c.community_id AS id,
       c.name AS name,
       c.summary AS content,
       c.core_entities AS core_entities,
       c.member_count AS member_count,
       c.end_user_id AS end_user_id,
       c.updated_at AS updated_at,
       score
ORDER BY score DESC
LIMIT $limit
"""

SEARCH_ENITITES_BY_RELATIONSHIP = """
MATCH (n:ExtractedEntity)-[r]-(m:ExtractedEntity)
WHERE (n.end_user_id = $end_user_id AND n.id = $source_id AND r.predicate_id IN $predicates)
RETURN m.id AS id,
       n.name AS source_name,
       r.predicate AS relation_predicate,
       m.name AS target_name
"""

SEARCH_RELATION_BETWEEN_ENTITIES = """
MATCH (n:ExtractedEntity)-[r]-(m:ExtractedEntity)
WHERE n.end_user_id = $end_user_id AND n.id = $source_id AND m.id = $target_id
RETURN n.id AS source_id,
       n.name AS source_name,
       r.predicate AS relation_predicate,
       m.id AS target_id,
       m.name AS target_name
"""

SEARCH_RELATIONS_BETWEEN_ENTITY_PAIRS = """
UNWIND $pairs AS pair
MATCH (n:ExtractedEntity)-[r]-(m:ExtractedEntity)
WHERE n.end_user_id = $end_user_id AND n.id = pair.source_id AND m.id = pair.target_id
RETURN n.id AS source_id,
       n.name AS source_name,
       r.predicate AS relation_predicate,
       m.id AS target_id,
       m.name AS target_name
"""

SEARCH_USER_METADATA = """
MATCH (n:ExtractedEntity)
WHERE (n.end_user_id = $end_user_id AND n.entity_type ='用户')
RETURN n.description AS description,
       n.aliases AS aliases,
       n.anchors AS anchors,
       n.beliefs_or_stances AS beliefs_or_stances,
       n.core_facts AS core_facts,
       n.events AS events,
       n.goals AS goals,
       n.interests AS interests,
       n.relations AS relations,
       n.traits AS traits,
       n.id AS id
"""

# -------------------
# cosine similarity search (vector.similarity.cosine in Cypher)
# -------------------
COSINE_STATEMENT_SEARCH = """
MATCH (s:Statement)
WHERE s.end_user_id = $end_user_id AND s.statement_embedding IS NOT NULL
WITH s, vector.similarity.cosine(s.statement_embedding, $embedding) AS score
WHERE score > 0
RETURN s.id AS id,
       s.statement AS statement,
       s.end_user_id AS end_user_id,
       s.chunk_id AS chunk_id,
       s.created_at AS created_at,
       s.valid_at AS valid_at,
       properties(s)['invalid_at'] AS invalid_at,
       COALESCE(s.activation_value, s.importance_score, 0.5) AS activation_value,
       COALESCE(s.importance_score, 0.5) AS importance_score,
       s.last_access_time AS last_access_time,
       COALESCE(s.access_count, 0) AS access_count,
       score
ORDER BY score DESC LIMIT $limit
"""

COSINE_ENTITY_SEARCH = """
MATCH (e:ExtractedEntity)
WHERE e.end_user_id = $end_user_id AND e.name_embedding IS NOT NULL
WITH e, vector.similarity.cosine(e.name_embedding, $embedding) AS score
WHERE score > 0
RETURN e.id AS id,
       e.name AS name,
       e.end_user_id AS end_user_id,
       e.entity_type AS entity_type,
       e.description AS description,
       COALESCE(e.activation_value, e.importance_score, 0.5) AS activation_value,
       COALESCE(e.importance_score, 0.5) AS importance_score,
       e.last_access_time AS last_access_time,
       COALESCE(e.access_count, 0) AS access_count,
       score
ORDER BY score DESC LIMIT $limit
"""

COSINE_CHUNK_SEARCH = """
MATCH (c:Chunk)
WHERE c.end_user_id = $end_user_id AND c.chunk_embedding IS NOT NULL
WITH c, vector.similarity.cosine(c.chunk_embedding, $embedding) AS score
WHERE score > 0
RETURN c.id AS id,
       c.end_user_id AS end_user_id,
       c.content AS content,
       c.dialog_id AS dialog_id,
       COALESCE(c.activation_value, 0.5) AS activation_value,
       c.last_access_time AS last_access_time,
       COALESCE(c.access_count, 0) AS access_count,
       score
ORDER BY score DESC LIMIT $limit
"""

COSINE_MEMORY_SUMMARY_SEARCH = """
MATCH (m:MemorySummary)
WHERE m.end_user_id = $end_user_id AND m.summary_embedding IS NOT NULL
WITH m, vector.similarity.cosine(m.summary_embedding, $embedding) AS score
WHERE score > 0
RETURN m.id AS id,
       m.name AS name,
       m.end_user_id AS end_user_id,
       m.dialog_id AS dialog_id,
       m.chunk_ids AS chunk_ids,
       m.content AS content,
       m.created_at AS created_at,
       COALESCE(m.activation_value, m.importance_score, 0.5) AS activation_value,
       COALESCE(m.importance_score, 0.5) AS importance_score,
       m.last_access_time AS last_access_time,
       COALESCE(m.access_count, 0) AS access_count,
       score
ORDER BY score DESC LIMIT $limit
"""

COSINE_COMMUNITY_SEARCH = """
MATCH (c:Community)
WHERE c.end_user_id = $end_user_id AND c.summary_embedding IS NOT NULL
WITH c, vector.similarity.cosine(c.summary_embedding, $embedding) AS score
WHERE score > 0
RETURN c.community_id AS id,
       c.name AS name,
       c.summary AS content,
       c.core_entities AS core_entities,
       c.member_count AS member_count,
       c.end_user_id AS end_user_id,
       c.updated_at AS updated_at,
       score
ORDER BY score DESC LIMIT $limit
"""

COSINE_PERCEPTUAL_SEARCH = """
MATCH (p:Perceptual)
WHERE p.end_user_id = $end_user_id AND p.summary_embedding IS NOT NULL
WITH p, vector.similarity.cosine(p.summary_embedding, $embedding) AS score
WHERE score > 0
RETURN p.id AS id,
       p.end_user_id AS end_user_id,
       p.perceptual_type AS perceptual_type,
       p.file_path AS file_path,
       p.file_name AS file_name,
       p.file_ext AS file_ext,
       p.summary AS summary,
       p.keywords AS keywords,
       p.topic AS topic,
       p.domain AS domain,
       p.created_at AS created_at,
       p.file_type AS file_type,
       score
ORDER BY score DESC LIMIT $limit
"""

COSINE_SEARCH_CYPHER_MAPPING = {
    Neo4jNodeType.STATEMENT: COSINE_STATEMENT_SEARCH,
    Neo4jNodeType.EXTRACTEDENTITY: COSINE_ENTITY_SEARCH,
    Neo4jNodeType.CHUNK: COSINE_CHUNK_SEARCH,
    Neo4jNodeType.MEMORYSUMMARY: COSINE_MEMORY_SUMMARY_SEARCH,
    Neo4jNodeType.COMMUNITY: COSINE_COMMUNITY_SEARCH,
    Neo4jNodeType.PERCEPTUAL: COSINE_PERCEPTUAL_SEARCH,
}

FULLTEXT_QUERY_CYPHER_MAPPING = {
    Neo4jNodeType.STATEMENT: SEARCH_STATEMENTS_BY_KEYWORD,
    Neo4jNodeType.EXTRACTEDENTITY: SEARCH_ENTITIES_BY_NAME_OR_ALIAS,
    Neo4jNodeType.CHUNK: SEARCH_CHUNKS_BY_CONTENT,
    Neo4jNodeType.MEMORYSUMMARY: SEARCH_MEMORY_SUMMARIES_BY_KEYWORD,
    Neo4jNodeType.COMMUNITY: SEARCH_COMMUNITIES_BY_KEYWORD,
    Neo4jNodeType.PERCEPTUAL: SEARCH_PERCEPTUALS_BY_KEYWORD
}
USER_ID_QUERY_CYPHER_MAPPING = {
    Neo4jNodeType.STATEMENT: SEARCH_STATEMENTS_BY_USER_ID,
    Neo4jNodeType.EXTRACTEDENTITY: SEARCH_ENTITIES_BY_USER_ID,
    Neo4jNodeType.CHUNK: SEARCH_CHUNKS_BY_USER_ID,
    Neo4jNodeType.MEMORYSUMMARY: SEARCH_MEMORY_SUMMARIES_BY_USER_ID,
    Neo4jNodeType.COMMUNITY: SEARCH_COMMUNITIES_BY_USER_ID,
    Neo4jNodeType.PERCEPTUAL: SEARCH_PERCEPTUAL_BY_USER_ID
}
NODE_ID_QUERY_CYPHER_MAPPING = {
    Neo4jNodeType.STATEMENT: SEARCH_STATEMENTS_BY_IDS,
    Neo4jNodeType.EXTRACTEDENTITY: SEARCH_ENTITIES_BY_IDS,
    Neo4jNodeType.CHUNK: SEARCH_CHUNKS_BY_IDS,
    Neo4jNodeType.MEMORYSUMMARY: SEARCH_MEMORY_SUMMARIES_BY_IDS,
    Neo4jNodeType.COMMUNITY: SEARCH_COMMUNITIES_BY_IDS,
    Neo4jNodeType.PERCEPTUAL: SEARCH_PERCEPTUAL_BY_IDS
}

# -------------------
# Assistant Original / Pruned nodes and edges
# -------------------

ASSISTANT_ORIGINAL_NODE_SAVE = """
UNWIND $originals AS o
MERGE (n:AssistantOriginal {id: o.id})
SET n += {
    id: o.id,
    name: o.name,
    end_user_id: o.end_user_id,
    run_id: o.run_id,
    created_at: o.created_at,
    pair_id: o.pair_id,
    dialog_id: o.dialog_id,
    text: o.text
}
RETURN n.id AS uuid
"""

ASSISTANT_PRUNED_NODE_SAVE = """
UNWIND $pruneds AS p
MERGE (n:AssistantPruned {id: p.id})
SET n += {
    id: p.id,
    name: p.name,
    end_user_id: p.end_user_id,
    run_id: p.run_id,
    created_at: p.created_at,
    pair_id: p.pair_id,
    dialog_id: p.dialog_id,
    text: p.text,
    memory_type: p.memory_type,
    text_embedding: p.text_embedding
}
RETURN n.id AS uuid
"""

ASSISTANT_PRUNED_EDGE_SAVE = """
UNWIND $edges AS edge
MATCH (orig:AssistantOriginal {id: edge.source, end_user_id: edge.end_user_id})
MATCH (pruned:AssistantPruned {id: edge.target, end_user_id: edge.end_user_id})
MERGE (orig)-[r:PRUNED_TO {pair_id: edge.pair_id}]->(pruned)
ON CREATE SET r.id = edge.id,
    r.end_user_id = edge.end_user_id,
    r.run_id = edge.run_id,
    r.created_at = edge.created_at
RETURN elementId(r) AS uuid
"""

ASSISTANT_DIALOG_EDGE_SAVE = """
UNWIND $edges AS edge
MATCH (orig:AssistantOriginal {id: edge.source, end_user_id: edge.end_user_id})
MATCH (dialog:Dialogue {id: edge.target, end_user_id: edge.end_user_id})
MERGE (orig)-[r:BELONGS_TO_DIALOG]->(dialog)
ON CREATE SET r.id = edge.id,
    r.end_user_id = edge.end_user_id,
    r.run_id = edge.run_id,
    r.created_at = edge.created_at
RETURN elementId(r) AS uuid
"""

# Conversation hub node：会话级中心节点，用 MERGE 保证跨写入任务幂等复用。
# 所有 AssistantOriginal 通过 BELONGS_TO_CONVERSATION 挂到该节点上，
# 从而把同一会话的 assistant 剪枝节点聚成一个连通子图。
CONVERSATION_NODE_SAVE = """
UNWIND $conversations AS c
MERGE (n:Conversation {id: c.id})
ON CREATE SET n.name = c.name,
    n.end_user_id = c.end_user_id,
    n.conversation_id = c.conversation_id,
    n.run_id = c.run_id,
    n.created_at = c.created_at
RETURN n.id AS uuid
"""

ASSISTANT_CONVERSATION_EDGE_SAVE = """
UNWIND $edges AS edge
MATCH (orig:AssistantOriginal {id: edge.source, end_user_id: edge.end_user_id})
MATCH (conv:Conversation {id: edge.target})
MERGE (orig)-[r:BELONGS_TO_CONVERSATION]->(conv)
ON CREATE SET r.id = edge.id,
    r.end_user_id = edge.end_user_id,
    r.run_id = edge.run_id,
    r.created_at = edge.created_at
RETURN elementId(r) AS uuid
"""
# --- Reflection Engine Layer 2: Description Merge ---

# Find entities whose description has accumulated >= min_fragments
REFLECTION_DESC_MERGE_CANDIDATES = """
MATCH (e:ExtractedEntity)
WHERE e.end_user_id = $end_user_id
  AND e.description IS NOT NULL
  AND e.description <> ""
  AND size(split(e.description, '；')) >= $min_fragments
RETURN e.id AS entity_id,
       e.name AS name,
       e.entity_type AS entity_type,
       e.description AS description,
       e.description_summary AS description_summary,
       e.description_timeline AS description_timeline,
       e.event_timeline AS event_timeline,
       e.aliases AS aliases
ORDER BY size(split(e.description, '；')) DESC
LIMIT $batch_size
"""

# Clear description, write summary, timeline and event_timeline
REFLECTION_DESC_UPDATE = """
MATCH (e:ExtractedEntity {id: $entity_id})
SET e.description = "",
    e.description_summary = $summary,
    e.description_timeline = $timeline,
    e.event_timeline = $event_timeline
RETURN e.id
"""

# --- Reflection Engine Layer 2: Entity Rename ---
REFLECTION_RENAME_CHECK_CONFLICT = """
MATCH (e:ExtractedEntity {end_user_id: $end_user_id, name: $suggested_name})
WHERE e.id <> $current_entity_id
RETURN count(e) AS conflict_count
"""

REFLECTION_RENAME_ENTITY = """
MATCH (e:ExtractedEntity {id: $entity_id})
SET e.name = $new_name
RETURN e.id
"""

REFLECTION_UPDATE_NAME_EMBEDDING = """
MATCH (e:ExtractedEntity {id: $entity_id})
SET e.name_embedding = $name_embedding
RETURN e.id
"""
# --- Reflection Engine Layer 2: Entity Dedup ---
#来源一：名称相似度
DEDUP_CANDIDATES_BY_NAME = """
MATCH (e1:ExtractedEntity)
WHERE e1.end_user_id = $end_user_id
  AND NOT toLower(e1.name) IN ['用户', '我', 'user', 'ai助手', '助手', '助理', 'ai', 'assistant', 'ai回复']
WITH e1
MATCH (e2:ExtractedEntity)
WHERE e2.end_user_id = $end_user_id
  AND e2.entity_type = e1.entity_type
  AND elementId(e1) < elementId(e2)
  AND NOT toLower(e2.name) IN ['用户', '我', 'user', 'ai助手', '助手', '助理', 'ai', 'assistant', 'ai回复']
  AND (
    toLower(e1.name) CONTAINS toLower(e2.name)
    OR toLower(e2.name) CONTAINS toLower(e1.name)
    OR any(a IN coalesce(e1.aliases, []) WHERE a IN coalesce(e2.aliases, []))
    OR e1.name IN coalesce(e2.aliases, [])
    OR e2.name IN coalesce(e1.aliases, [])
  )
RETURN e1.id AS a_id, e2.id AS b_id,
       e1.name AS a_name, e2.name AS b_name,
       e1.entity_type AS entity_type,
       e1.description AS a_desc, e2.description AS b_desc,
       e1.aliases AS a_aliases, e2.aliases AS b_aliases,
       e1.name_embedding AS a_embed, e2.name_embedding AS b_embed
LIMIT $candidate_cap
"""

#路径B 通过name_embedding 相似度检索
DEDUP_CANDIDATES_BY_EMBED = """
MATCH (e1:ExtractedEntity)
WHERE e1.end_user_id = $end_user_id
  AND e1.name_embedding IS NOT NULL
  AND NOT toLower(e1.name) IN ['用户', '我', 'user', 'ai助手', '助手', '助理', 'ai', 'assistant', 'ai回复']
CALL db.index.vector.queryNodes('entity_embedding_index', $top_k, e1.name_embedding)
YIELD node AS e2, score
WHERE e2.end_user_id = $end_user_id
  AND e2.entity_type = e1.entity_type
  AND elementId(e1) < elementId(e2)
  AND score >= $theta_embed_floor
  AND NOT toLower(e2.name) IN ['用户', '我', 'user', 'ai助手', '助手', '助理', 'ai', 'assistant', 'ai回复']
RETURN e1.id AS a_id, e2.id AS b_id,
       e1.name AS a_name, e2.name AS b_name,
       e1.entity_type AS entity_type,
       e1.description AS a_desc, e2.description AS b_desc,
       e1.aliases AS a_aliases, e2.aliases AS b_aliases,
       score AS sim_embed
LIMIT $candidate_cap
"""


#去重两个实体合并
DEDUP_MERGE_ENTITIES = """
MATCH (keeper:ExtractedEntity {id: $keeper_id, end_user_id: $end_user_id})
MATCH (loser:ExtractedEntity {id: $loser_id, end_user_id: $end_user_id})
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
    keeper.created_at = CASE
      WHEN keeper.created_at IS NULL THEN loser.created_at
      WHEN loser.created_at IS NULL THEN keeper.created_at
      WHEN loser.created_at < keeper.created_at THEN loser.created_at
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
WHERE NOT (s)-[:REFERENCES_ENTITY]->(keeper)
FOREACH (_ IN CASE WHEN r IS NOT NULL THEN [1] ELSE [] END |
  CREATE (s)-[:REFERENCES_ENTITY]->(keeper)
)
WITH keeper, loser
OPTIONAL MATCH (loser)-[r:EXTRACTED_RELATIONSHIP]->(target)
WHERE target <> keeper AND NOT (keeper)-[:EXTRACTED_RELATIONSHIP]->(target)
FOREACH (_ IN CASE WHEN r IS NOT NULL THEN [1] ELSE [] END |
  CREATE (keeper)-[:EXTRACTED_RELATIONSHIP {relation_type: r.relation_type}]->(target)
)
WITH keeper, loser
OPTIONAL MATCH (source)-[r:EXTRACTED_RELATIONSHIP]->(loser)
WHERE source <> keeper AND NOT (source)-[:EXTRACTED_RELATIONSHIP]->(keeper)
FOREACH (_ IN CASE WHEN r IS NOT NULL THEN [1] ELSE [] END |
  CREATE (source)-[:EXTRACTED_RELATIONSHIP {relation_type: r.relation_type}]->(keeper)
)
WITH keeper, loser
DETACH DELETE loser
RETURN keeper.id AS merged_id
"""

# 方案B：查出指定类型下所有实体（全量送 LLM 分组判定）
DEDUP_FULL_SCAN_ENTITIES = """
MATCH (e:ExtractedEntity)
WHERE e.end_user_id = $end_user_id
  AND e.entity_type = $entity_type
  AND NOT toLower(e.name) IN ['用户', '我', 'user', 'ai助手', '助手', '助理', 'ai', 'assistant', 'ai回复']
RETURN e.id AS entity_id, e.name AS name, e.entity_type AS entity_type,
       e.description AS description, e.aliases AS aliases, e.created_at AS created_at
ORDER BY e.created_at
"""

# 方案B：查上次扫描后新增的实体数（增量判断，new_count=0 则跳过该类型）
DEDUP_FULL_SCAN_NEW_COUNT = """
MATCH (e:ExtractedEntity)
WHERE e.end_user_id = $end_user_id
  AND e.entity_type = $entity_type
  AND e.created_at > $last_scan_time
  AND NOT toLower(e.name) IN ['用户', '我', 'user', 'ai助手', '助手', '助理', 'ai', 'assistant', 'ai回复']
RETURN count(e) AS new_count
"""

# 方案B：查该用户所有 entity_type 及数量（决定遍历哪些类型）
DEDUP_FULL_SCAN_ENTITY_TYPES = """
MATCH (e:ExtractedEntity)
WHERE e.end_user_id = $end_user_id
  AND NOT toLower(e.name) IN ['用户', '我', 'user', 'ai助手', '助手', '助理', 'ai', 'assistant', 'ai回复']
RETURN DISTINCT e.entity_type AS entity_type, count(e) AS count
"""


# --- Reflection Engine Layer 2: Unresolved Entity (子问题5) ---

UNRESOLVED_STATEMENT_CANDIDATES = """
MATCH (s:Statement)
WHERE s.end_user_id = $end_user_id
  AND s.has_unsolved_reference = true
RETURN s.id AS statement_id,
       s.statement AS statement_text,
       s.dialog_at AS dialog_at,
       s.chunk_id AS chunk_id,
       s.stmt_type AS stmt_type,
       s.temporal_info AS temporal_info,
       s.speaker AS speaker,
       s.valid_at AS valid_at,
       s.invalid_at AS invalid_at
ORDER BY s.created_at ASC
LIMIT $batch_size
"""

UNRESOLVED_CONTEXT_CHUNKS = """
MATCH (c:Chunk {id: $chunk_id})
MATCH (nearby:Chunk {end_user_id: $end_user_id})
WHERE nearby.id <> c.id
WITH c, nearby,
     abs(duration.between(datetime(nearby.created_at), datetime(c.created_at)).days * 86400
       + duration.between(datetime(nearby.created_at), datetime(c.created_at)).seconds) AS diff_sec
ORDER BY diff_sec ASC
LIMIT $limit
WITH collect(nearby) AS chunks
UNWIND chunks AS chunk
RETURN chunk.content AS content, chunk.created_at AS created_at
ORDER BY chunk.created_at ASC
"""

UNRESOLVED_CREATE_ENTITY = """
MERGE (e:ExtractedEntity {
  end_user_id: $end_user_id,
  name: $name,
  entity_type: $entity_type
})
ON CREATE SET
  e.id = randomUUID(),
  e.description = $description,
  e.aliases = [],
  e.connect_strength = "weak",
  e.source = "reflection_unresolved",
  e.created_at = localdatetime()
ON MATCH SET
  e.description = CASE
    WHEN e.description IS NULL OR e.description = "" THEN $description
    ELSE e.description + '；' + $description
  END
RETURN e.id AS entity_id, e.name AS name
"""

UNRESOLVED_UPDATE_NAME_EMBEDDING = """
MATCH (e:ExtractedEntity {id: $entity_id})
SET e.name_embedding = $name_embedding
RETURN e.id
"""

UNRESOLVED_CREATE_RELATIONSHIP = """
MATCH (subj:ExtractedEntity {end_user_id: $end_user_id, name: $subject_name})
MATCH (obj:ExtractedEntity {end_user_id: $end_user_id, name: $object_name})
CREATE (subj)-[r:EXTRACTED_RELATIONSHIP {
  predicate: $predicate,
  predicate_id: $predicate_id,
  predicate_surface: $predicate_surface,
  statement_id: $statement_id,
  valid_at: $valid_at,
  invalid_at: $invalid_at,
  end_user_id: $end_user_id,
  connect_strength: "weak",
  source: "reflection_unresolved",
  created_at: datetime()
}]->(obj)
RETURN r.predicate AS predicate
"""

UNRESOLVED_CREATE_STATEMENT_ENTITY_EDGE = """
MATCH (s:Statement {id: $statement_id})
MATCH (e:ExtractedEntity {end_user_id: $end_user_id, name: $entity_name})
MERGE (s)-[:REFERENCES_ENTITY]->(e)
RETURN s.id AS statement_id
"""

UNRESOLVED_UPDATE_STATEMENT_FLAG = """
MATCH (s:Statement {id: $statement_id})
SET s.has_unsolved_reference = false
RETURN s.id AS statement_id
"""

# ============================================================================
# 反思阶段 · 别名归并（"别名属于" 关系处理）
# 由 Layer2Inspector 确定性步骤调用，按 end_user_id 全量扫描 "别名属于" 边：
#   1. MERGE_ALIAS_BELONGS_TO：将 source.name 并入 target.aliases，
#      source.description 追加到 target.description
#   2. REDIRECT_ALIAS_EDGES：将别名节点(source)上其它边重定向到 target
#   3. DELETE_ALIAS_NODES：DETACH DELETE 别名节点（连同 "别名属于" 边）
# ============================================================================

# 别名归并：将 predicate="别名属于" 的 EXTRACTED_RELATIONSHIP 边的 source.name
# 合并进 target.aliases（去重），并将 source.description 追加到 target.description（分号分隔）
MERGE_ALIAS_BELONGS_TO = """
// 先按 target 分组，将所有 source.name 和 source.description 聚合，
// 再一次性 SET，避免多条 别名属于 边对同一 target 反复覆盖。
MATCH (source:ExtractedEntity {end_user_id: $end_user_id})-[r:EXTRACTED_RELATIONSHIP]->(target:ExtractedEntity {end_user_id: $end_user_id})
WHERE r.predicate = '别名属于'
WITH target,
     coalesce(target.aliases, []) AS existing_aliases,
     coalesce(target.description, '') AS tgt_desc,
     collect(DISTINCT source.name) AS source_names,
     collect(DISTINCT coalesce(source.description, '')) AS source_descs

// 1. 合并 aliases：将所有 source.name 追加到 target.aliases（去重，忽略空值）
WITH target, tgt_desc, source_names, source_descs, existing_aliases,
     existing_aliases + [n IN source_names WHERE n IS NOT NULL AND n <> '' AND NOT n IN existing_aliases] AS new_aliases

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

RETURN target.name AS target_name, new_aliases AS updated_aliases, size(new_aliases) - size(existing_aliases) AS added_count
"""

# 边重定向：将指向别名节点（"别名属于"关系的 source）的所有其他边，重定向到用户节点（target）。
# 处理两类边：
#   1. EXTRACTED_RELATIONSHIP：其他实体 → 别名节点 或 别名节点 → 其他实体
#   2. STATEMENT_ENTITY：陈述句 → 别名节点
# 对于每条需要重定向的边，创建一条指向用户节点的新边（复制所有属性），然后删除旧边。
REDIRECT_ALIAS_EDGES = """
// 找到所有 别名→用户 的映射
MATCH (alias:ExtractedEntity {end_user_id: $end_user_id})-[ar:EXTRACTED_RELATIONSHIP]->(user:ExtractedEntity {end_user_id: $end_user_id})
WHERE ar.predicate = '别名属于'
WITH collect({alias_id: elementId(alias), user_id: elementId(user), alias_eid: alias.id, user_eid: user.id}) AS mappings

// 1. 重定向 EXTRACTED_RELATIONSHIP 边：别名节点作为 target 的情况
UNWIND mappings AS m
MATCH (other)-[r:EXTRACTED_RELATIONSHIP]->(alias:ExtractedEntity {end_user_id: $end_user_id})
WHERE alias.id = m.alias_eid
  AND r.predicate <> '别名属于'
  AND other.id <> m.user_eid
WITH m, other, r, alias
MATCH (user:ExtractedEntity {id: m.user_eid, end_user_id: $end_user_id})
CREATE (other)-[nr:EXTRACTED_RELATIONSHIP]->(user)
SET nr = properties(r)
DELETE r
WITH count(*) AS redirected_incoming

// 2. 重定向 EXTRACTED_RELATIONSHIP 边：别名节点作为 source 的情况
MATCH (alias:ExtractedEntity {end_user_id: $end_user_id})-[ar2:EXTRACTED_RELATIONSHIP]->(user2:ExtractedEntity {end_user_id: $end_user_id})
WHERE ar2.predicate = '别名属于'
WITH alias, user2, redirected_incoming
MATCH (alias)-[r:EXTRACTED_RELATIONSHIP]->(other)
WHERE r.predicate <> '别名属于'
  AND other.id <> user2.id
WITH user2, other, r, redirected_incoming
CREATE (user2)-[nr:EXTRACTED_RELATIONSHIP]->(other)
SET nr = properties(r)
DELETE r
WITH redirected_incoming, count(*) AS redirected_outgoing

// 3. 重定向 STATEMENT_ENTITY 边：陈述句 → 别名节点
MATCH (alias:ExtractedEntity {end_user_id: $end_user_id})-[ar3:EXTRACTED_RELATIONSHIP]->(user3:ExtractedEntity {end_user_id: $end_user_id})
WHERE ar3.predicate = '别名属于'
WITH alias, user3, redirected_incoming, redirected_outgoing
MATCH (stmt)-[r:STATEMENT_ENTITY]->(alias)
WITH user3, stmt, r, redirected_incoming, redirected_outgoing
CREATE (stmt)-[nr:STATEMENT_ENTITY]->(user3)
SET nr = properties(r)
DELETE r

RETURN redirected_incoming, redirected_outgoing, count(*) AS redirected_stmt
"""

# 删除别名节点：在别名归并和边重定向完成后，删除所有 predicate="别名属于" 关系的 source 节点。
# 此时这些节点的其他边已被 REDIRECT_ALIAS_EDGES 重定向完毕，
# 唯一剩余的边就是 (alias)-[:EXTRACTED_RELATIONSHIP {predicate:'别名属于'}]->(user)，
# 使用 DETACH DELETE 一并删除节点和该关系。
DELETE_ALIAS_NODES = """
MATCH (alias:ExtractedEntity {end_user_id: $end_user_id})-[r:EXTRACTED_RELATIONSHIP]->(user:ExtractedEntity {end_user_id: $end_user_id})
WHERE r.predicate = '别名属于'
WITH alias, count(r) AS rel_count
DETACH DELETE alias
RETURN count(alias) AS deleted_count
"""