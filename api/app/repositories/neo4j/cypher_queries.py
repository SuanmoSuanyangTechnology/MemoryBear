
DIALOGUE_NODE_SAVE = """
    UNWIND $dialogues AS dialogue
    MERGE (n:Dialogue {id: dialogue.id})
    SET n.uuid = coalesce(n.uuid, dialogue.id),
        n.group_id = dialogue.group_id,
        n.user_id = dialogue.user_id,
        n.apply_id = dialogue.apply_id,
        n.run_id = dialogue.run_id,
        n.ref_id = dialogue.ref_id,
        n.created_at = dialogue.created_at,
        n.expired_at = dialogue.expired_at,
        n.content = dialogue.content,
        n.dialog_embedding = dialogue.dialog_embedding
    RETURN n.id AS uuid
"""

STATEMENT_NODE_SAVE = """
UNWIND $statements AS statement
MERGE (s:Statement {id: statement.id})
SET s += {
    id: statement.id,
    group_id: statement.group_id,
    user_id: statement.user_id,
    apply_id: statement.apply_id,
    chunk_id: statement.chunk_id,
    run_id: statement.run_id,
    created_at: statement.created_at,
    expired_at: statement.expired_at,
    stmt_type: statement.stmt_type,
    temporal_info: statement.temporal_info,
    relevence_info: statement.relevence_info,
    statement: statement.statement,
    valid_at: statement.valid_at,
    invalid_at: statement.invalid_at,
    statement_embedding: statement.statement_embedding
}
RETURN s.id AS uuid
"""

CHUNK_NODE_SAVE = """
UNWIND $chunks AS chunk
MERGE (c:Chunk {id: chunk.id})
SET c += {
    id: chunk.id,
    name: chunk.name,
    group_id: chunk.group_id,
    user_id: chunk.user_id,
    apply_id: chunk.apply_id,
    run_id: chunk.run_id,
    created_at: chunk.created_at,
    expired_at: chunk.expired_at,
    dialog_id: chunk.dialog_id,
    content: chunk.content,
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
    e.group_id = CASE WHEN entity.group_id IS NOT NULL AND entity.group_id <> '' THEN entity.group_id ELSE e.group_id END,
    e.user_id = CASE WHEN entity.user_id IS NOT NULL AND entity.user_id <> '' THEN entity.user_id ELSE e.user_id END,
    e.apply_id = CASE WHEN entity.apply_id IS NOT NULL AND entity.apply_id <> '' THEN entity.apply_id ELSE e.apply_id END,
    e.run_id = CASE WHEN entity.run_id IS NOT NULL AND entity.run_id <> '' THEN entity.run_id ELSE e.run_id END,
    e.created_at = CASE
        WHEN entity.created_at IS NOT NULL AND (e.created_at IS NULL OR entity.created_at < e.created_at)
        THEN entity.created_at ELSE e.created_at END,
    e.expired_at = CASE
        WHEN entity.expired_at IS NOT NULL AND (e.expired_at IS NULL OR entity.expired_at > e.expired_at)
        THEN entity.expired_at ELSE e.expired_at END,
    e.entity_idx = CASE WHEN e.entity_idx IS NULL OR e.entity_idx = 0 THEN entity.entity_idx ELSE e.entity_idx END,
    e.entity_type = CASE WHEN entity.entity_type IS NOT NULL AND entity.entity_type <> '' THEN entity.entity_type ELSE e.entity_type END,
    e.description = CASE
        WHEN entity.description IS NOT NULL AND entity.description <> ''
         AND (e.description IS NULL OR size(e.description) = 0 OR size(entity.description) > size(e.description))
        THEN entity.description ELSE e.description END,
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
    e.fact_summary = CASE
        WHEN entity.fact_summary IS NOT NULL AND entity.fact_summary <> ''
         AND (e.fact_summary IS NULL OR size(e.fact_summary) = 0 OR size(entity.fact_summary) > size(e.fact_summary))
        THEN entity.fact_summary ELSE e.fact_summary END,
    e.connect_strength = CASE
        WHEN entity.connect_strength IS NULL OR entity.connect_strength = '' THEN e.connect_strength
        ELSE CASE
            WHEN e.connect_strength = 'strong' AND entity.connect_strength = 'weak' THEN 'both'
            WHEN e.connect_strength = 'weak' AND entity.connect_strength = 'strong' THEN 'both'
            WHEN e.connect_strength IS NULL OR e.connect_strength = '' THEN entity.connect_strength
            ELSE e.connect_strength
        END
    END
RETURN e.id AS uuid
"""

# Add back ENTITY_RELATIONSHIP_SAVE to be used by graph_saver.save_entities_and_relationships
ENTITY_RELATIONSHIP_SAVE = """
UNWIND $relationships AS rel
// Match entities by stable id within group, do not constrain by run_id
MATCH (subject:ExtractedEntity {id: rel.source_id, group_id: rel.group_id})
MATCH (object:ExtractedEntity {id: rel.target_id, group_id: rel.group_id})
// Avoid duplicate edges across runs for the same endpoints
MERGE (subject)-[r:EXTRACTED_RELATIONSHIP]->(object)
SET r.predicate = rel.predicate,
    r.statement_id = rel.statement_id,
    r.value = rel.value,
    r.statement = rel.statement,
    r.valid_at = rel.valid_at,
    r.invalid_at = rel.invalid_at,
    r.created_at = rel.created_at,
    r.expired_at = rel.expired_at,
    r.run_id = rel.run_id,
    r.group_id = rel.group_id
RETURN elementId(r) AS uuid
"""

# 在 Neo4j 5及后续版本中，id() 函数已被标记为弃用，用elementId() 函数替代

# 保存弱关系实体，设置 e.is_weak = true；不维护 e.relations 聚合字段
WEAK_ENTITY_NODE_SAVE = """
UNWIND $weak_entities AS entity
MERGE (e:ExtractedEntity {id: entity.id, run_id: entity.run_id})
SET e += {
    name: entity.name,
    group_id: entity.group_id,
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
SET s += {name: item.subject, group_id: item.group_id, run_id: item.run_id}
// Independent strong flag
SET s.is_strong = true
MERGE (o:ExtractedEntity {id: item.target_id, run_id: item.run_id})
SET o += {name: item.object, group_id: item.group_id, run_id: item.run_id}
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
        e.group_id = edge.group_id,
        e.created_at = edge.created_at,
        e.expired_at = edge.expired_at
    RETURN e.uuid AS uuid
"""

# 在 Neo4j 5及后续版本中，id() 函数已被标记为弃用，用elementId() 函数替代


CHUNK_STATEMENT_EDGE_SAVE = """
    UNWIND $chunk_statement_edges AS edge
    MATCH (statement:Statement {id: edge.source, run_id: edge.run_id})
    MATCH (chunk:Chunk {id: edge.target, run_id: edge.run_id})
    MERGE (chunk)-[e:CONTAINS {id: edge.id}]->(statement)
    SET e.group_id = edge.group_id,
        e.run_id = edge.run_id,
        e.created_at = edge.created_at,
        e.expired_at = edge.expired_at
    RETURN e.id AS uuid
"""

STATEMENT_ENTITY_EDGE_SAVE = """
UNWIND $relationships AS rel
// Statement nodes are per-run; keep run_id constraint on statements
// Statement nodes are per-run; keep run_id constraint on statements
MATCH (statement:Statement {id: rel.source, run_id: rel.run_id})
// Entities are shared across runs within a group; do not constrain by run_id
MATCH (entity:ExtractedEntity {id: rel.target, group_id: rel.group_id})
// Avoid duplicate edges across runs for same endpoints
MERGE (statement)-[r:REFERENCES_ENTITY]->(entity)
SET r.group_id = rel.group_id,
    r.run_id = rel.run_id,
    r.created_at = rel.created_at,
    r.expired_at = rel.expired_at,
    r.connect_strength = rel.connect_strength
RETURN elementId(r) AS uuid
"""

ENTITY_EMBEDDING_SEARCH = """
CALL db.index.vector.queryNodes('entity_embedding_index', $limit * 100, $embedding)
YIELD node AS e, score
WHERE e.name_embedding IS NOT NULL
  AND ($group_id IS NULL OR e.group_id = $group_id)
RETURN e.id AS id,
       e.name AS name,
       e.group_id AS group_id,
       e.entity_type AS entity_type,
       score
ORDER BY score DESC
LIMIT $limit
"""
# Embedding-based search: cosine similarity on Statement.statement_embedding
STATEMENT_EMBEDDING_SEARCH = """
CALL db.index.vector.queryNodes('statement_embedding_index', $limit * 100, $embedding)
YIELD node AS s, score
WHERE s.statement_embedding IS NOT NULL
  AND ($group_id IS NULL OR s.group_id = $group_id)
RETURN s.id AS id,
       s.statement AS statement,
       s.group_id AS group_id,
       s.chunk_id AS chunk_id,
       s.created_at AS created_at,
       s.expired_at AS expired_at,
       s.valid_at AS valid_at,
       s.invalid_at AS invalid_at,
       score
ORDER BY score DESC
LIMIT $limit
"""

# Embedding-based search: cosine similarity on Chunk.chunk_embedding
CHUNK_EMBEDDING_SEARCH = """
CALL db.index.vector.queryNodes('chunk_embedding_index', $limit * 100, $embedding)
YIELD node AS c, score
WHERE c.chunk_embedding IS NOT NULL
  AND ($group_id IS NULL OR c.group_id = $group_id)
RETURN c.id AS chunk_id,
       c.group_id AS group_id,
       c.content AS content,
       c.dialog_id AS dialog_id,
       score
ORDER BY score DESC
LIMIT $limit
"""

SEARCH_STATEMENTS_BY_KEYWORD = """
CALL db.index.fulltext.queryNodes("statementsFulltext", $q) YIELD node AS s, score
WHERE ($group_id IS NULL OR s.group_id = $group_id)
OPTIONAL MATCH (c:Chunk)-[:CONTAINS]->(s)
OPTIONAL MATCH (s)-[:REFERENCES_ENTITY]->(e:ExtractedEntity)
RETURN s.id AS id,
       s.statement AS statement,
       s.group_id AS group_id,
       s.chunk_id AS chunk_id,
       s.created_at AS created_at,
       s.expired_at AS expired_at,
       s.valid_at AS valid_at,
       s.invalid_at AS invalid_at,
       c.id AS chunk_id_from_rel,
       collect(DISTINCT e.id) AS entity_ids,
       score
ORDER BY score DESC
LIMIT $limit
"""
# 查询实体名称包含指定字符串的实体
SEARCH_ENTITIES_BY_NAME = """
CALL db.index.fulltext.queryNodes("entitiesFulltext", $q) YIELD node AS e, score
WHERE ($group_id IS NULL OR e.group_id = $group_id)
OPTIONAL MATCH (s:Statement)-[:REFERENCES_ENTITY]->(e)
OPTIONAL MATCH (c:Chunk)-[:CONTAINS]->(s)
RETURN e.id AS id,
       e.name AS name,
       e.group_id AS group_id,
       e.entity_type AS entity_type,
       e.apply_id AS apply_id,
       e.user_id AS user_id,
       e.created_at AS created_at,
       e.expired_at AS expired_at,
       e.entity_idx AS entity_idx,
       e.statement_id AS statement_id,
       e.description AS description,
       e.aliases AS aliases,
       e.name_embedding AS name_embedding,
       e.fact_summary AS fact_summary,
       e.connect_strength AS connect_strength,
       collect(DISTINCT s.id) AS statement_ids,
       collect(DISTINCT c.id) AS chunk_ids,
       score
ORDER BY score DESC
LIMIT $limit
"""

SEARCH_CHUNKS_BY_CONTENT = """
CALL db.index.fulltext.queryNodes("chunksFulltext", $q) YIELD node AS c, score
WHERE ($group_id IS NULL OR c.group_id = $group_id)
OPTIONAL MATCH (c)-[:CONTAINS]->(s:Statement)
OPTIONAL MATCH (s)-[:REFERENCES_ENTITY]->(e:ExtractedEntity)
RETURN c.id AS chunk_id,
       c.group_id AS group_id,
       c.content AS content,
       c.dialog_id AS dialog_id,
       c.sequence_number AS sequence_number,
       collect(DISTINCT s.id) AS statement_ids,
       collect(DISTINCT e.id) AS entity_ids,
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
WHERE ($group_id IS NULL OR d.group_id = $group_id)
  AND d.id = $dialog_id
RETURN d.id AS dialog_id,
       d.group_id AS group_id,
       d.content AS content,
       d.created_at AS created_at,
       d.expired_at AS expired_at
ORDER BY d.created_at DESC
LIMIT $limit
"""

SEARCH_CHUNK_BY_CHUNK_ID = """
MATCH (c:Chunk)
WHERE ($group_id IS NULL OR c.group_id = $group_id)
  AND c.id = $chunk_id
RETURN c.id AS chunk_id,
       c.group_id AS group_id,
       c.content AS content,
       c.dialog_id AS dialog_id,
       c.created_at AS created_at,
       c.expired_at AS expired_at,
       c.sequence_number AS sequence_number
ORDER BY c.created_at DESC
LIMIT $limit
"""

SEARCH_STATEMENTS_BY_TEMPORAL = """
MATCH (s:Statement)
WHERE ($group_id IS NULL OR s.group_id = $group_id)
  AND ($apply_id IS NULL OR s.apply_id = $apply_id)
  AND ($user_id IS NULL OR s.user_id = $user_id)
  AND ((($start_date IS NULL OR datetime(s.created_at) >= datetime($start_date))
  AND ($end_date IS NULL OR datetime(s.created_at) <= datetime($end_date)))
  OR (($valid_date IS NULL OR (s.valid_at IS NOT NULL AND datetime(s.valid_at) >= datetime($valid_date)))
  AND ($invalid_date IS NULL OR (s.invalid_at IS NOT NULL AND datetime(s.invalid_at) <= datetime($invalid_date)))))
RETURN s.id AS id,
       s.statement AS statement,
       s.group_id AS group_id,
       s.apply_id AS apply_id,
       s.user_id AS user_id,
       s.chunk_id AS chunk_id,
       s.created_at AS created_at,
       s.valid_at AS valid_at,
       s.invalid_at AS invalid_at,
       collect(DISTINCT s.id) AS statement_ids
ORDER BY datetime(s.created_at) DESC
LIMIT $limit
"""

SEARCH_STATEMENTS_BY_KEYWORD_TEMPORAL = """
CALL db.index.fulltext.queryNodes("statementsFulltext", $q) YIELD node AS s, score
WHERE ($group_id IS NULL OR s.group_id = $group_id)
  AND ($apply_id IS NULL OR s.apply_id = $apply_id)
  AND ($user_id IS NULL OR s.user_id = $user_id)
  AND ((($start_date IS NULL OR (s.created_at IS NOT NULL AND datetime(s.created_at) >= datetime($start_date)))
  AND ($end_date IS NULL OR (s.created_at IS NOT NULL AND datetime(s.created_at) <= datetime($end_date))))
  OR (($valid_date IS NULL OR (s.valid_at IS NOT NULL AND datetime(s.valid_at) >= datetime($valid_date)))
  AND ($invalid_date IS NULL OR (s.invalid_at IS NOT NULL AND datetime(s.invalid_at) <= datetime($invalid_date)))))
OPTIONAL MATCH (c:Chunk)-[:CONTAINS]->(s)
OPTIONAL MATCH (s)-[:REFERENCES_ENTITY]->(e:ExtractedEntity)
RETURN s.id AS id,
       s.statement AS statement,
       s.group_id AS group_id,
       s.apply_id AS apply_id,
       s.user_id AS user_id,
       s.chunk_id AS chunk_id,
       s.created_at AS created_at,
       s.valid_at AS valid_at,
       s.invalid_at AS invalid_at,
       c.id AS chunk_id_from_rel,
       collect(DISTINCT e.id) AS entity_ids,
       score
ORDER BY s.created_at DESC, score DESC
LIMIT $limit
"""

SEARCH_STATEMENTS_BY_CREATED_AT = """
MATCH (n:Statement)
WHERE ($group_id IS NULL OR n.group_id = $group_id)
  AND ($apply_id IS NULL OR n.apply_id = $apply_id)
  AND ($user_id IS NULL OR n.user_id = $user_id)
  AND ($created_at IS NOT NULL AND date(substring(n.created_at, 0, 10)) = date($created_at))
RETURN n.id AS id,
       n.statement AS statement,
       n.group_id AS group_id,
       n.apply_id AS apply_id,
       n.user_id AS user_id,
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
WHERE ($group_id IS NULL OR n.group_id = $group_id)
  AND ($apply_id IS NULL OR n.apply_id = $apply_id)
  AND ($user_id IS NULL OR n.user_id = $user_id)
  AND ($valid_at IS NOT NULL AND date(substring(n.valid_at, 0, 10)) = date($valid_at))
RETURN n.id AS id,
       n.statement AS statement,
       n.group_id AS group_id,
       n.apply_id AS apply_id,
       n.user_id AS user_id,
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
WHERE ($group_id IS NULL OR n.group_id = $group_id)
  AND ($apply_id IS NULL OR n.apply_id = $apply_id)
  AND ($user_id IS NULL OR n.user_id = $user_id)
  AND ($created_at IS NOT NULL AND date(substring(n.created_at, 0, 19)) = date($created_at))
RETURN n.id AS id,
       n.statement AS statement,
       n.group_id AS group_id,
       n.apply_id AS apply_id,
       n.user_id AS user_id,
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
WHERE ($group_id IS NULL OR n.group_id = $group_id)
  AND ($apply_id IS NULL OR n.apply_id = $apply_id)
  AND ($user_id IS NULL OR n.user_id = $user_id)
  AND ($created_at IS NOT NULL AND date(substring(n.created_at, 0, 19)) < date($created_at))
RETURN n.id AS id,
       n.statement AS statement,
       n.group_id AS group_id,
       n.apply_id AS apply_id,
       n.user_id AS user_id,
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
WHERE ($group_id IS NULL OR n.group_id = $group_id)
  AND ($apply_id IS NULL OR n.apply_id = $apply_id)
  AND ($user_id IS NULL OR n.user_id = $user_id)
  AND ($valid_at IS NOT NULL AND date(substring(n.valid_at, 0, 10)) > date($valid_at))
RETURN n.id AS id,
       n.statement AS statement,
       n.group_id AS group_id,
       n.apply_id AS apply_id,
       n.user_id AS user_id,
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
WHERE ($group_id IS NULL OR n.group_id = $group_id)
  AND ($apply_id IS NULL OR n.apply_id = $apply_id)
  AND ($user_id IS NULL OR n.user_id = $user_id)
  AND ($valid_at IS NOT NULL AND date(substring(n.valid_at, 0, 10)) < date($valid_at))
RETURN n.id AS id,
       n.statement AS statement,
       n.group_id AS group_id,
       n.apply_id AS apply_id,
       n.user_id AS user_id,
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
MATCH (n:Statement {group_id: $group_id, id: $id})
SET n.invalid_at = $new_invalid_at
"""

# MemorySummary keyword search using fulltext index
SEARCH_MEMORY_SUMMARIES_BY_KEYWORD = """
CALL db.index.fulltext.queryNodes("summariesFulltext", $q) YIELD node AS m, score
WHERE ($group_id IS NULL OR m.group_id = $group_id)
OPTIONAL MATCH (m)-[:DERIVED_FROM_STATEMENT]->(s:Statement)
RETURN m.id AS id,
       m.name AS name,
       m.group_id AS group_id,
       m.dialog_id AS dialog_id,
       m.chunk_ids AS chunk_ids,
       m.content AS content,
       m.created_at AS created_at,
       score
ORDER BY score DESC
LIMIT $limit
"""

# Embedding-based search: cosine similarity on MemorySummary.summary_embedding
MEMORY_SUMMARY_EMBEDDING_SEARCH = """
CALL db.index.vector.queryNodes('summary_embedding_index', $limit * 100, $embedding)
YIELD node AS m, score
WHERE m.summary_embedding IS NOT NULL
  AND ($group_id IS NULL OR m.group_id = $group_id)
RETURN m.id AS id,
       m.name AS name,
       m.group_id AS group_id,
       m.dialog_id AS dialog_id,
       m.chunk_ids AS chunk_ids,
       m.content AS content,
       m.created_at AS created_at,
       score
ORDER BY score DESC
LIMIT $limit
"""

MEMORY_SUMMARY_NODE_SAVE = """
UNWIND $summaries AS summary
MERGE (m:MemorySummary {id: summary.id})
SET m += {
    id: summary.id,
    name: summary.name,
    group_id: summary.group_id,
    user_id: summary.user_id,
    apply_id: summary.apply_id,
    run_id: summary.run_id,
    created_at: summary.created_at,
    expired_at: summary.expired_at,
    dialog_id: summary.dialog_id,
    chunk_ids: summary.chunk_ids,
    content: summary.content,
    summary_embedding: summary.summary_embedding,
    config_id: summary.config_id
}
RETURN m.id AS uuid
"""

MEMORY_SUMMARY_STATEMENT_EDGE_SAVE = """
UNWIND $edges AS e
MATCH (ms:MemorySummary {id: e.summary_id, run_id: e.run_id})
MATCH (c:Chunk {id: e.chunk_id, run_id: e.run_id})
MATCH (c)-[:CONTAINS]->(s:Statement {run_id: e.run_id})
MERGE (ms)-[r:DERIVED_FROM_STATEMENT]->(s)
SET r.group_id = e.group_id,
    r.run_id = e.run_id,
    r.created_at = e.created_at,
    r.expired_at = e.expired_at
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
        group_id: rel.group_id,
        user_id: rel.user_id,
        apply_id: rel.apply_id,
        run_id: rel.run_id,
        created_at: rel.created_at,
        expired_at: rel.expired_at
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
        group_id: rel.group_id,
        user_id: rel.user_id,
        apply_id: rel.apply_id,
        run_id: rel.run_id,
        created_at: rel.created_at,
        expired_at: rel.expired_at
    }]->(canonical)
)

// 删除losing实体及其所有关系
WITH losing
DETACH DELETE losing

RETURN count(losing) as deleted
"""
