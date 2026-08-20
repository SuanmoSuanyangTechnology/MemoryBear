from app.core.memory.constants.graph_data_constants import (
    NODE_PROPERTY_WHITELIST,
    SUPPORTED_NODE_TYPES,
    _DEFAULT_FIELDS,
)
from app.core.memory.constants.value_weight_constants import (
    G_WEIGHT,
    T_CYPHER_EXPR,
    T_WEIGHT,
)
from app.core.memory.enums import Neo4jNodeType

DIALOGUE_NODE_SAVE = """
    UNWIND $dialogues AS dialogue
    MERGE (n:Dialogue {id: dialogue.id})
    SET n.uuid = coalesce(n.uuid, dialogue.id)
    // 覆盖竞态守卫：只许 normal 覆盖 fast，不许 fast 反向降级 normal。
    // 当已存在节点为 normal 且本次写入为 fast 时跳过内容写入（canWrite=[]），
    // 保留正写的权威版本；其余情况（新建 / normal 覆盖 fast / 同模式重试）照常写入。
    WITH n, dialogue,
         CASE WHEN n.write_mode = 'normal' AND dialogue.write_mode = 'fast'
              THEN [] ELSE [1] END AS canWrite
    FOREACH (_ IN canWrite |
        SET n.end_user_id = dialogue.end_user_id,
            n.run_id = dialogue.run_id,
            n.ref_id = dialogue.ref_id,
            n.created_at = coalesce(n.created_at, dialogue.created_at),
            n.content = dialogue.content,
            n.name = dialogue.name,
            n.dialog_embedding = dialogue.dialog_embedding,
            n.config_id = dialogue.config_id,
            n.write_mode = coalesce(dialogue.write_mode, 'normal'),
            // emotion 粘性保留：本次写入未带值（None）则保留原值，避免正写（不算情绪）
            n.emotion = coalesce(dialogue.emotion, n.emotion),
            n.emotion_score = coalesce(dialogue.emotion_score, n.emotion_score)
    )
    RETURN n.id AS uuid
"""

STATEMENT_NODE_SAVE = """
UNWIND $statements AS statement
MERGE (s:Statement {id: statement.id})
SET s.delete_at = null,
    s += {
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
    has_unsolved_reference: statement.has_unsolved_reference,
    is_permanent: CASE
        WHEN coalesce(s.is_permanent, false) THEN true
        ELSE coalesce(statement.is_permanent, false)
    END
}
RETURN s.id AS uuid
"""

PERMANENT_MEMORY_COUNT = """
MATCH (s:Statement {end_user_id: $end_user_id})
WHERE s.delete_at IS NULL
  AND coalesce(s.is_permanent, false) = true
RETURN count(s) AS used
"""

# 用户全部存活 Statement 总数（用于 value-ranking 列表分页，非永久节点也算）
LIVE_STATEMENT_COUNT = """
MATCH (s:Statement {end_user_id: $end_user_id})
WHERE s.delete_at IS NULL
RETURN count(s) AS total
"""
# 动态价值重排（v1.3）：value_score = 0.75*G + 0.25*T；is_permanent=true 恒为 1.0
# （原文档权重 G=0.30/T=0.10；仅用两因子时按 3:1 比例归一化，使普通节点上限=1）
# G = topology_score（缺失取 0）
# T = 2^(-age_days/30)，age_days 按 created_at 距今（未来时间按 0 天，T=1）
PERMANENT_MEMORY_LIST = f"""
MATCH (s:Statement {{end_user_id: $end_user_id}})
WHERE s.delete_at IS NULL
WITH s, elementId(s) AS id,
     toFloat(s.topology_score) AS raw_g,
     CASE
       WHEN s.created_at IS NULL THEN null
       ELSE datetime(s.created_at).epochMillis
     END AS created_epoch
WITH id, s, created_epoch,
     CASE
       WHEN raw_g IS NULL OR isNaN(raw_g) THEN 0.0
       WHEN raw_g < 0.0 THEN 0.0
       WHEN raw_g > 1.0 THEN 1.0
       ELSE raw_g
     END AS g
WITH id, s, g,
     CASE WHEN created_epoch IS NULL THEN 0.0 ELSE {T_CYPHER_EXPR} END AS t
WITH id, s,
     CASE
       WHEN coalesce(s.is_permanent, false) THEN 1.0
       ELSE {G_WEIGHT} * g + {T_WEIGHT} * t
     END AS value_score
ORDER BY value_score DESC, s.created_at DESC, id ASC
SKIP $skip
LIMIT $limit
RETURN id,
       'Statement' AS label,
       {{
           statement: s.statement,
           created_at: s.created_at,
           is_permanent: coalesce(s.is_permanent, false),
           value_score: sqrt(value_score) * 100
       }} AS properties
"""

PERMANENT_MEMORY_UNMARK = """
MATCH (s:Statement {end_user_id: $end_user_id})
WHERE elementId(s) = $element_id
  AND s.delete_at IS NULL
SET s.is_permanent = false
RETURN elementId(s) AS id, s.is_permanent AS is_permanent
"""

STATEMENT_EMOTION_UPDATE = """
UNWIND $items AS item
MATCH (s:Statement {id: item.statement_id})
WHERE s.delete_at IS NULL
SET s.emotion_type = item.emotion_type,
    s.emotion_intensity = item.emotion_intensity,
    s.emotion_keywords = item.emotion_keywords
RETURN s.id AS uuid
"""

INTEREST_ENTITY_CANDIDATES_BY_END_USER = """
MATCH (s:Statement)-[:REFERENCES_ENTITY]->(e:ExtractedEntity)
WHERE s.end_user_id = $id
  AND e.end_user_id = $id
  AND s.delete_at IS NULL
  AND e.delete_at IS NULL
  AND e.id IS NOT NULL
  AND e.name IS NOT NULL
  AND (
    s.speaker IS NULL
    OR toLower(trim(toString(s.speaker))) = 'user'
  )
WITH e.id AS entity_id,
     trim(toString(e.name)) AS name,
     coalesce(toString(e.entity_type), '') AS entity_type,
     count(DISTINCT s.id) AS frequency
WHERE name <> ''
  AND NOT (toLower(name) IN $excluded_names)
  AND NOT name =~ $iso_datetime_pattern
  AND NOT name =~ $unix_timestamp_pattern
RETURN entity_id, name, entity_type, frequency
ORDER BY frequency DESC, toLower(name) ASC, entity_id ASC
LIMIT $limit
"""

INTEREST_ENTITY_CANDIDATES_BY_USER = """
MATCH (s:Statement)-[:REFERENCES_ENTITY]->(e:ExtractedEntity)
WHERE s.user_id = $id
  AND e.user_id = $id
  AND s.delete_at IS NULL
  AND e.delete_at IS NULL
  AND e.id IS NOT NULL
  AND e.name IS NOT NULL
  AND (
    s.speaker IS NULL
    OR toLower(trim(toString(s.speaker))) = 'user'
  )
WITH e.id AS entity_id,
     trim(toString(e.name)) AS name,
     coalesce(toString(e.entity_type), '') AS entity_type,
     count(DISTINCT s.id) AS frequency
WHERE name <> ''
  AND NOT (toLower(name) IN $excluded_names)
  AND NOT name =~ $iso_datetime_pattern
  AND NOT name =~ $unix_timestamp_pattern
RETURN entity_id, name, entity_type, frequency
ORDER BY frequency DESC, toLower(name) ASC, entity_id ASC
LIMIT $limit
"""

INTEREST_STATEMENT_EVIDENCE_BY_END_USER = """
UNWIND range(0, size($entity_ids) - 1) AS entity_rank
WITH entity_rank, $entity_ids[entity_rank] AS entity_id
MATCH (s:Statement)-[:REFERENCES_ENTITY]->(e:ExtractedEntity {id: entity_id})
WHERE s.end_user_id = $id
  AND e.end_user_id = $id
  AND s.delete_at IS NULL
  AND e.delete_at IS NULL
  AND s.id IS NOT NULL
  AND s.statement IS NOT NULL
  AND trim(toString(s.statement)) <> ''
  AND (
    s.speaker IS NULL
    OR toLower(trim(toString(s.speaker))) = 'user'
  )
WITH entity_rank, entity_id, s
ORDER BY entity_rank ASC,
         CASE WHEN toLower(trim(toString(s.speaker))) = 'user' THEN 0 ELSE 1 END ASC,
         coalesce(s.dialog_at, s.created_at) DESC,
         s.id ASC
WITH entity_rank, entity_id, collect(s)[0..$per_entity_limit] AS statements
UNWIND statements AS s
WITH s, min(entity_rank) AS first_entity_rank
MATCH (s)-[:REFERENCES_ENTITY]->(related_entity:ExtractedEntity)
WHERE related_entity.id IN $entity_ids
  AND related_entity.end_user_id = $id
  AND related_entity.delete_at IS NULL
WITH s,
     first_entity_rank,
     collect(DISTINCT toString(related_entity.id)) AS related_entity_ids
RETURN toString(s.id) AS statement_id,
       [entity_id IN $entity_ids WHERE entity_id IN related_entity_ids] AS entity_ids,
       substring(trim(toString(s.statement)), 0, $max_chars) AS statement_text
ORDER BY first_entity_rank ASC,
         CASE WHEN toLower(trim(toString(s.speaker))) = 'user' THEN 0 ELSE 1 END ASC,
         coalesce(s.dialog_at, s.created_at) DESC,
         statement_id ASC
LIMIT $limit
"""

INTEREST_STATEMENT_EVIDENCE_BY_USER = """
UNWIND range(0, size($entity_ids) - 1) AS entity_rank
WITH entity_rank, $entity_ids[entity_rank] AS entity_id
MATCH (s:Statement)-[:REFERENCES_ENTITY]->(e:ExtractedEntity {id: entity_id})
WHERE s.user_id = $id
  AND e.user_id = $id
  AND s.delete_at IS NULL
  AND e.delete_at IS NULL
  AND s.id IS NOT NULL
  AND s.statement IS NOT NULL
  AND trim(toString(s.statement)) <> ''
  AND (
    s.speaker IS NULL
    OR toLower(trim(toString(s.speaker))) = 'user'
  )
WITH entity_rank, entity_id, s
ORDER BY entity_rank ASC,
         CASE WHEN toLower(trim(toString(s.speaker))) = 'user' THEN 0 ELSE 1 END ASC,
         coalesce(s.dialog_at, s.created_at) DESC,
         s.id ASC
WITH entity_rank, entity_id, collect(s)[0..$per_entity_limit] AS statements
UNWIND statements AS s
WITH s, min(entity_rank) AS first_entity_rank
MATCH (s)-[:REFERENCES_ENTITY]->(related_entity:ExtractedEntity)
WHERE related_entity.id IN $entity_ids
  AND related_entity.user_id = $id
  AND related_entity.delete_at IS NULL
WITH s,
     first_entity_rank,
     collect(DISTINCT toString(related_entity.id)) AS related_entity_ids
RETURN toString(s.id) AS statement_id,
       [entity_id IN $entity_ids WHERE entity_id IN related_entity_ids] AS entity_ids,
       substring(trim(toString(s.statement)), 0, $max_chars) AS statement_text
ORDER BY first_entity_rank ASC,
         CASE WHEN toLower(trim(toString(s.speaker))) = 'user' THEN 0 ELSE 1 END ASC,
         coalesce(s.dialog_at, s.created_at) DESC,
         statement_id ASC
LIMIT $limit
"""

CHUNK_NODE_SAVE = """
UNWIND $chunks AS chunk
MERGE (c:Chunk {id: chunk.id})
SET c.delete_at = null,
    c += {
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

EXTRACTED_ENTITY_NODE_SAVE = """
// Upsert entity nodes safely: preserve existing non-empty fields when incoming is empty
UNWIND $entities AS entity
MERGE (e:ExtractedEntity {id: entity.id})
SET e.delete_at = null
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
        THEN CASE
            WHEN e.description IS NULL OR size(e.description) = 0 THEN entity.description
            ELSE e.description + '；' + entity.description
        END
        ELSE e.description END,
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
    e.is_explicit_memory = CASE WHEN entity.is_explicit_memory IS NOT NULL THEN entity.is_explicit_memory ELSE coalesce(e.is_explicit_memory, false) END,
    e.extraction_count = CASE WHEN entity.extraction_count IS NOT NULL THEN entity.extraction_count + coalesce(e.extraction_count, 0) ELSE coalesce(e.extraction_count, 1) END,
    e.delete_at = null
RETURN e.id AS uuid
"""

# ── 查询用户实体已有的元数据（供增量提取时去重） ──
ENTITY_METADATA_QUERY = """
MATCH (e:ExtractedEntity {id: $entity_id})
WHERE e.delete_at IS NULL
RETURN e.core_facts AS core_facts,
       e.traits AS traits,
       e.relations AS relations,
       e.goals AS goals,
       e.interests AS interests,
       e.beliefs_or_stances AS beliefs_or_stances,
       e.anchors AS anchors,
       e.events AS events
"""

# ── 元数据 patch 回写：对 8 字段统一应用 delete / update / add 三段操作 ──
# 设计要点：
#   1. 8 个字段一次原子 SET，纯 Cypher（不依赖 APOC），无 race 风险
#   2. delete: [x IN list WHERE NOT x IN $field_delete] —— 仅精确剔除被指名项
#   3. update: [x IN list | CASE WHEN x = pair.old THEN pair.new ELSE x END]
#              对每个 (old, new) pair 顺序应用一次，匹配不到则保持原值
#   4. add:    reduce 去重追加，原值不会丢失
#   5. 输入参数（每字段三类）：
#        $<field>_delete : List[str]
#        $<field>_update : List[{old: str, new: str}]
#        $<field>_add    : List[str]
#      上层调用方对未变更字段传空数组即可，避免 Cypher 内部出现 NULL 分支。
ENTITY_METADATA_PATCH = """
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

RETURN e.id AS uuid,
       e.core_facts AS core_facts,
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
WHERE subject.delete_at IS NULL
MATCH (object:ExtractedEntity {id: rel.target_id, end_user_id: rel.end_user_id})
WHERE object.delete_at IS NULL
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
SET e.delete_at = null,
    e += {
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
SET s.delete_at = null,
    s += {name: item.subject, end_user_id: item.end_user_id, run_id: item.run_id}
// Independent strong flag
SET s.is_strong = true
MERGE (o:ExtractedEntity {id: item.target_id, run_id: item.run_id})
SET o.delete_at = null,
    o += {name: item.object, end_user_id: item.end_user_id, run_id: item.run_id}
// Independent strong flag
SET o.is_strong = true
"""

DIALOGUE_STATEMENT_EDGE_SAVE = """
    UNWIND $dialogue_statement_edges AS edge
    // 支持按 uuid 或 ref_id 连接到 Dialogue，避免因来源 ID 不一致而断链
    MATCH (dialogue:Dialogue)
    WHERE (dialogue.uuid = edge.source OR dialogue.ref_id = edge.source)
      AND dialogue.delete_at IS NULL
    MATCH (statement:Statement {id: edge.target})
    WHERE statement.delete_at IS NULL
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
    WHERE statement.delete_at IS NULL
    MATCH (chunk:Chunk {id: edge.target, run_id: edge.run_id})
    WHERE chunk.delete_at IS NULL
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
WHERE statement.delete_at IS NULL
// Entities are shared across runs within end_user_id; do not constrain by run_id
MATCH (entity:ExtractedEntity {id: rel.target, end_user_id: rel.end_user_id})
WHERE entity.delete_at IS NULL
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
  AND e.delete_at IS NULL
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
  AND s.delete_at IS NULL
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
  AND c.delete_at IS NULL
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
  AND s.delete_at IS NULL
OPTIONAL MATCH (c:Chunk)-[:CONTAINS]->(s)
WHERE c.delete_at IS NULL
OPTIONAL MATCH (s)-[:REFERENCES_ENTITY]->(e:ExtractedEntity)
WHERE e.delete_at IS NULL
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
  AND e.delete_at IS NULL
WITH e, score
With collect({entity: e, score: score}) AS fulltextResults

OPTIONAL MATCH (ae:ExtractedEntity)
WHERE ($end_user_id IS NULL OR ae.end_user_id = $end_user_id)
  AND ae.delete_at IS NULL
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
WHERE s.delete_at IS NULL
OPTIONAL MATCH (c:Chunk)-[:CONTAINS]->(s)
WHERE c.delete_at IS NULL
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
  AND c.delete_at IS NULL
OPTIONAL MATCH (c)-[:CONTAINS]->(s:Statement)
WHERE s.delete_at IS NULL
OPTIONAL MATCH (s)-[:REFERENCES_ENTITY]->(e:ExtractedEntity)
WHERE e.delete_at IS NULL
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
  AND d.delete_at IS NULL
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
  AND c.delete_at IS NULL
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
  AND s.delete_at IS NULL
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
  AND s.delete_at IS NULL
  AND ((($start_date IS NULL OR (s.created_at IS NOT NULL AND datetime(s.created_at) >= datetime($start_date)))
  AND ($end_date IS NULL OR (s.created_at IS NOT NULL AND datetime(s.created_at) <= datetime($end_date))))
  OR (($valid_date IS NULL OR (s.valid_at IS NOT NULL AND datetime(s.valid_at) >= datetime($valid_date)))
  AND ($invalid_date IS NULL OR (s.invalid_at IS NOT NULL AND datetime(s.invalid_at) <= datetime($invalid_date)))))
OPTIONAL MATCH (c:Chunk)-[:CONTAINS]->(s)
WHERE c.delete_at IS NULL
OPTIONAL MATCH (s)-[:REFERENCES_ENTITY]->(e:ExtractedEntity)
WHERE e.delete_at IS NULL
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
  AND n.delete_at IS NULL
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
  AND n.delete_at IS NULL
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
  AND n.delete_at IS NULL
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
  AND n.delete_at IS NULL
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
  AND n.delete_at IS NULL
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
  AND n.delete_at IS NULL
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
WHERE n.delete_at IS NULL
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
WHERE ms.delete_at IS NULL
MATCH (c:Chunk {id: e.chunk_id, run_id: e.run_id})
WHERE c.delete_at IS NULL
MATCH (c)-[:CONTAINS]->(s:Statement {run_id: e.run_id})
WHERE s.delete_at IS NULL
MERGE (ms)-[r:DERIVED_FROM_STATEMENT]->(s)
SET r.end_user_id = e.end_user_id,
    r.run_id = e.run_id,
    r.created_at = e.created_at
RETURN elementId(r) AS uuid
"""

# Entity Merge Query
MERGE_ENTITIES = """
MATCH (canonical:ExtractedEntity {id: $canonical_id})
WHERE canonical.delete_at IS NULL
MATCH (losing:ExtractedEntity {id: $losing_id})
WHERE losing.delete_at IS NULL

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
  AND n.delete_at IS NULL
  AND datetime(n.created_at) >= datetime() - duration('P3D')
RETURN
  n.statement as statement_name,
  n.id as statement_id,
   n.created_at as   statement_created_at

'''
neo4j_statement_all = '''
MATCH (n:Statement)
WHERE n.end_user_id = "{}"
  AND n.delete_at IS NULL
RETURN
  n.statement as statement_name,
  n.id as statement_id

'''
neo4j_query_part = """
            MATCH (n)-[r]-(m:ExtractedEntity)
            WHERE n.end_user_id = "{}"
            AND n.delete_at IS NULL
            AND m.delete_at IS NULL
            AND datetime(n.created_at) >= datetime() - duration('P3D')
            WITH DISTINCT m
            OPTIONAL MATCH (m)-[rel]-(other:ExtractedEntity)
            WHERE other.delete_at IS NULL
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
                AND n.delete_at IS NULL
                AND m.delete_at IS NULL
                WITH DISTINCT m
                OPTIONAL MATCH (m)-[rel]-(other:ExtractedEntity)
                WHERE other.delete_at IS NULL
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

'''按 elementId 查询 ExtractedEntity 的事件时间线字段'''
Memory_Timeline_Entity_Events = """
MATCH (e:ExtractedEntity)
WHERE elementId(e) = $id
  AND e.delete_at IS NULL
RETURN e.name AS entity_name,
       e.entity_type AS entity_type,
       e.description_summary AS description_summary,
       e.event_timeline AS event_timeline
"""

'''针对当前节点下扩长的句子，实体和总结'''
Memory_Timeline_ExtractedEntity = """
MATCH (n)-[r1]-(e)-[r2]-(ms)
WHERE elementId(n) = $id
  AND (ms:ExtractedEntity OR ms:MemorySummary)
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

CALL () {
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

CALL () {
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

'''针对当前节点，主要获取更加完整的句子节点'''
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

    优化：不再使用 ``properties(n)`` 拉取全部属性（包含 embedding 等大字段），
    而是仅 SELECT 白名单字段，大幅减少网络传输和序列化开销。
    使用 CASE 过滤 null 值，保持与 properties(n) 行为一致（仅返回存在的属性）。

    Args:
        node_type: 节点 label，必须属于 :data:`SUPPORTED_NODE_TYPES`。

    Returns:
        Cypher 字符串；运行期参数为 ``$end_user_id`` (STRING) 与 ``$limit`` (INTEGER)。

    Raises:
        ValueError: 当 ``node_type`` 不在 :data:`SUPPORTED_NODE_TYPES` 中时。
    """
    if node_type not in SUPPORTED_NODE_TYPES:
        raise ValueError(f"不支持的 Node_Type，拒绝内联进 Cypher: {node_type!r}")

    # 从白名单获取该类型需要返回的字段
    fields = NODE_PROPERTY_WHITELIST.get(node_type, _DEFAULT_FIELDS)

    # 使用 Cypher map projection + 后续在 Python 中过滤 null
    # Neo4j map literal {k: n.k} 会保留 null 值，需要在应用层过滤
    props_entries = ", ".join(f"{f}: n.{f}" for f in fields)

    return f"""
// GRAPH_NODES_BY_TYPE_LIMITS({node_type})
MATCH (n:{node_type})
WHERE n.end_user_id = $end_user_id
  AND n.delete_at IS NULL
RETURN
    elementId(n)        AS id,
    labels(n)           AS labels,
    {{{props_entries}}} AS properties
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
  AND n.delete_at IS NULL
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
  AND n.delete_at IS NULL
RETURN nid AS id, COUNT { (n)--() } AS rel_count
"""


def build_forget_memory_count_query(label: str, with_end_user: bool = True) -> str:
    """构建「按单个 label 统计激活值低于阈值的节点数」的 Cypher。

    用于遗忘记忆计数，每个 label 独立查询以命中 label-property 索引。

    Args:
        label: 节点 label（Statement / ExtractedEntity / MemorySummary / Chunk）
        with_end_user: 是否按 end_user_id 过滤

    Returns:
        Cypher 字符串；运行期参数为 ``$threshold`` (FLOAT)，
        如果 with_end_user=True 还需要 ``$end_user_id`` (STRING)。
    """
    _ALLOWED_LABELS = frozenset({"Statement", "ExtractedEntity", "MemorySummary", "Chunk"})
    if label not in _ALLOWED_LABELS:
        raise ValueError(f"不支持的 label: {label!r}")

    if with_end_user:
        return f"""
MATCH (n:{label})
WHERE n.end_user_id = $end_user_id
  AND n.activation_value IS NOT NULL
  AND n.activation_value < $threshold
RETURN count(n) AS cnt
"""
    else:
        return f"""
MATCH (n:{label})
WHERE n.activation_value IS NOT NULL
  AND n.activation_value < $threshold
RETURN count(n) AS cnt
"""


def build_node_count_query(node_type: str, with_end_user: bool = True) -> str:
    """构建按 label 统计节点数的 Cypher（用于 analytics_node_statistics）。

    Args:
        node_type: 节点 label
        with_end_user: 是否按 end_user_id 过滤
    """
    if with_end_user:
        return f"""
MATCH (n:{node_type})
WHERE n.end_user_id = $end_user_id
RETURN count(n) as count
"""
    else:
        return f"""
MATCH (n:{node_type})
RETURN count(n) as count
"""


def build_center_node_neighbors_query(depth: int) -> str:
    """构建中心节点邻居查询 Cypher（Center_Mode）。

    Args:
        depth: 邻居跳数（已被调用方钳制为 1-3）
    """
    from app.core.memory.constants.graph_data_constants import DEPTH_HARD_MAX
    safe_depth = max(1, min(int(depth), DEPTH_HARD_MAX))
    return f"""
MATCH path = (center)-[*1..{safe_depth}]-(connected)
WHERE center.end_user_id = $end_user_id
  AND elementId(center) = $center_node_id
WITH collect(DISTINCT center) + collect(DISTINCT connected) as all_nodes
UNWIND all_nodes as n
RETURN DISTINCT
    elementId(n) as id,
    labels(n) as labels,
    properties(n) as properties
LIMIT $limit
"""


# Q3：查询若干节点之间的有向关系
# 参数：$node_ids (LIST<STRING>)
GRAPH_EDGES_AMONG_NODES = """
MATCH (n)-[r]->(m)
WHERE elementId(n) IN $node_ids
  AND elementId(m) IN $node_ids
RETURN
    elementId(r) as id,
    elementId(n) as source,
    elementId(m) as target,
    type(r) as rel_type,
    properties(r) as properties
"""

# DEPRECATED: Graph_Node_query 已被 build_graph_nodes_by_type_query() 取代，
# 后者按 SUPPORTED_NODE_TYPES 内联 label 字面量，可命中 end_user_id 索引并支持
# 独立 Per_Type_Limit。此常量仅保留以避免破坏式改动；新代码不应再使用。
Graph_Node_query = """
MATCH (n:MemorySummary)
WHERE n.end_user_id = $end_user_id
  AND n.delete_at IS NULL
RETURN
  elementId(n) AS id,
  labels(n) AS labels,
  properties(n) AS properties,
  0 AS priority
LIMIT $limit

UNION ALL

MATCH (n:Dialogue)
WHERE n.end_user_id =  $end_user_id
  AND n.delete_at IS NULL
RETURN
  elementId(n) AS id,
  labels(n) AS labels,
  properties(n) AS properties,
  1 AS priority
LIMIT 1

UNION ALL

MATCH (n:Statement)
WHERE n.end_user_id =  $end_user_id
  AND n.delete_at IS NULL
RETURN
  elementId(n) AS id,
  labels(n) AS labels,
  properties(n) AS properties,
  1 AS priority
LIMIT $limit

UNION ALL

MATCH (n:ExtractedEntity)
WHERE n.end_user_id =  $end_user_id
  AND n.delete_at IS NULL
RETURN
  elementId(n) AS id,
  labels(n) AS labels,
  properties(n) AS properties,
  2 AS priority
LIMIT $limit

UNION ALL

MATCH (n:Chunk)
WHERE n.end_user_id =  $end_user_id
  AND n.delete_at IS NULL
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
WHERE e.delete_at IS NULL
MATCH (c:Community {community_id: $community_id, end_user_id: $end_user_id})
MERGE (e)-[:BELONGS_TO_COMMUNITY]->(c)
SET c.updated_at = datetime()
RETURN e.id AS entity_id, c.community_id AS community_id
"""

ENTITY_LEAVE_ALL_COMMUNITIES = """
MATCH (e:ExtractedEntity {id: $entity_id, end_user_id: $end_user_id})
WHERE e.delete_at IS NULL
MATCH (e)-[r:BELONGS_TO_COMMUNITY]->(:Community)
DELETE r
"""

GET_ENTITY_NEIGHBORS = """
MATCH (e:ExtractedEntity {id: $entity_id, end_user_id: $end_user_id})
WHERE e.delete_at IS NULL

// 来源一：直接关系邻居（EXTRACTED_RELATIONSHIP 边）
OPTIONAL MATCH (e)-[:EXTRACTED_RELATIONSHIP]-(nb1:ExtractedEntity {end_user_id: $end_user_id})
WHERE nb1.delete_at IS NULL

// 来源二：同 Statement 共现邻居（REFERENCES_ENTITY 边）
OPTIONAL MATCH (s:Statement)-[:REFERENCES_ENTITY]->(e)
WHERE s.delete_at IS NULL
OPTIONAL MATCH (s)-[:REFERENCES_ENTITY]->(nb2:ExtractedEntity {end_user_id: $end_user_id})
WHERE nb2.id <> e.id
  AND nb2.delete_at IS NULL

WITH collect(DISTINCT nb1) + collect(DISTINCT nb2) AS all_neighbors
UNWIND all_neighbors AS nb
WITH nb WHERE nb IS NOT NULL
OPTIONAL MATCH (nb)-[:BELONGS_TO_COMMUNITY]->(c:Community)
RETURN DISTINCT
    nb.id               AS id,
    nb.name             AS name,
    nb.name_embedding   AS name_embedding,
    CASE WHEN c IS NOT NULL THEN c.community_id ELSE null END AS community_id
"""

GET_ALL_ENTITIES_FOR_USER = """
MATCH (e:ExtractedEntity {end_user_id: $end_user_id})
WHERE e.delete_at IS NULL
OPTIONAL MATCH (e)-[:BELONGS_TO_COMMUNITY]->(c:Community)
RETURN e.id AS id,
       e.name AS name,
       e.name_embedding AS name_embedding,
       CASE WHEN c IS NOT NULL THEN c.community_id ELSE null END AS community_id
"""

GET_ENTITY_COUNT_FOR_USER = """
MATCH (e:ExtractedEntity {end_user_id: $end_user_id})
WHERE e.delete_at IS NULL
RETURN count(e) AS entity_count
"""

GET_ALL_ENTITY_IDS_FOR_USER = """
MATCH (e:ExtractedEntity {end_user_id: $end_user_id})
WHERE e.delete_at IS NULL
RETURN e.id AS id
"""

GET_COMMUNITY_MEMBERS = """
MATCH (e:ExtractedEntity {end_user_id: $end_user_id})-[:BELONGS_TO_COMMUNITY]->(c:Community {community_id: $community_id})
WHERE e.delete_at IS NULL
RETURN e.id AS id, e.name AS name, e.entity_type AS entity_type,
       e.importance_score AS importance_score,
       e.name_embedding AS name_embedding,
       e.aliases AS aliases, e.description AS description,
       e.example AS example
ORDER BY coalesce(e.importance_score, 0) DESC
"""

GET_COMMUNITY_RELATIONSHIPS = """
MATCH (e1:ExtractedEntity {end_user_id: $end_user_id})-[:BELONGS_TO_COMMUNITY]->(c:Community {community_id: $community_id})
WHERE e1.delete_at IS NULL
MATCH (e2:ExtractedEntity {end_user_id: $end_user_id})-[:BELONGS_TO_COMMUNITY]->(c)
WHERE e2.delete_at IS NULL
MATCH (e1)-[r:EXTRACTED_RELATIONSHIP]->(e2)
RETURN e1.name AS subject, r.predicate AS predicate, e2.name AS object
ORDER BY e1.name, r.predicate, e2.name
LIMIT 20
"""

# P0 修复：批量将实体分配到社区（UNWIND），替换逐实体循环的 assign_entity_to_community
BATCH_ASSIGN_ENTITIES_TO_COMMUNITIES = """
UNWIND $assignments AS row
MATCH (e:ExtractedEntity {id: row.entity_id, end_user_id: $end_user_id})
WHERE e.delete_at IS NULL
OPTIONAL MATCH (e)-[old_r:BELONGS_TO_COMMUNITY]->(:Community)
DELETE old_r
WITH e, row
MATCH (c:Community {community_id: row.community_id, end_user_id: $end_user_id})
MERGE (e)-[:BELONGS_TO_COMMUNITY]->(c)
SET c.updated_at = datetime()
RETURN count(e) AS assigned_count
"""

# P7 修复：批量计算各社区的平均 embedding，纯 Cypher 逐元素向量加法（不依赖 APOC）
# 返回每个社区的成员数和平均向量，避免将全量成员数据拉取到 Python 侧
GET_COMMUNITY_AVG_EMBEDDINGS_BATCH = """
MATCH (e:ExtractedEntity {end_user_id: $end_user_id})
      -[:BELONGS_TO_COMMUNITY]->(c:Community)
WHERE c.community_id IN $community_ids
  AND e.delete_at IS NULL
  AND e.name_embedding IS NOT NULL
WITH c.community_id AS cid,
     count(e) AS member_count,
     collect(e.name_embedding) AS all_embeddings
WITH cid, member_count,
     reduce(
       acc = head(all_embeddings),
       emb IN tail(all_embeddings) |
       [i IN range(0, size(acc) - 1) | acc[i] + emb[i]]
     ) AS sum_vec
RETURN cid,
       member_count,
       [v IN sum_vec | v / member_count] AS avg_embedding
"""

CHECK_USER_HAS_COMMUNITIES = """
MATCH (c:Community {end_user_id: $end_user_id})
RETURN count(c) AS community_count
"""

UPDATE_COMMUNITY_MEMBER_COUNT = """
MATCH (e:ExtractedEntity {end_user_id: $end_user_id})-[:BELONGS_TO_COMMUNITY]->(c:Community {community_id: $community_id})
WHERE e.delete_at IS NULL
WITH c, count(e) AS cnt
SET c.member_count = cnt
RETURN c.community_id AS community_id, cnt AS member_count
"""

# ─── 聚类收尾对账（兜底去重/反思并发删实体留下的脏数据）───────────────────────
# 删除该用户下所有"没有任何成员"的社区（含空社区 + member_count=1 但无成员边的孤儿社区）
RECONCILE_DELETE_EMPTY_COMMUNITIES = """
MATCH (c:Community {end_user_id: $end_user_id})
WHERE NOT (:ExtractedEntity {end_user_id: $end_user_id})-[:BELONGS_TO_COMMUNITY]->(c)
DETACH DELETE c
RETURN count(c) AS deleted
"""

# 重算该用户下所有存活社区的 member_count（一次性，按真实成员边计数）
RECONCILE_REFRESH_ALL_MEMBER_COUNTS = """
MATCH (c:Community {end_user_id: $end_user_id})
OPTIONAL MATCH (e:ExtractedEntity {end_user_id: $end_user_id})-[:BELONGS_TO_COMMUNITY]->(c)
WHERE e.delete_at IS NULL
WITH c, count(e) AS cnt
SET c.member_count = cnt
RETURN count(c) AS refreshed
"""

# 仅重算指定社区的 member_count（增量聚类后使用，避免对全用户社区写放大）
RECONCILE_REFRESH_MEMBER_COUNTS_SCOPED = """
MATCH (c:Community {end_user_id: $end_user_id})
WHERE c.community_id IN $community_ids
OPTIONAL MATCH (e:ExtractedEntity {end_user_id: $end_user_id})-[:BELONGS_TO_COMMUNITY]->(c)
WHERE e.delete_at IS NULL
WITH c, count(e) AS cnt
SET c.member_count = cnt
RETURN count(c) AS refreshed
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
WHERE e.delete_at IS NULL
OPTIONAL MATCH (e)-[:BELONGS_TO_COMMUNITY]->(c:Community)
RETURN e.id AS id,
       e.name AS name,
       e.name_embedding AS name_embedding,
       CASE WHEN c IS NOT NULL THEN c.community_id ELSE null END AS community_id
ORDER BY e.id
SKIP $skip LIMIT $limit
"""

GET_ENTITY_NEIGHBORS_BATCH_FOR_IDS = """
// 批量拉取指定实体列表的邻居（用于分批全量聚类）
MATCH (e:ExtractedEntity {end_user_id: $end_user_id})
WHERE e.delete_at IS NULL
  AND e.id IN $entity_ids
  AND e.delete_at IS NULL
OPTIONAL MATCH (e)-[:EXTRACTED_RELATIONSHIP]-(nb1:ExtractedEntity {end_user_id: $end_user_id})
WHERE nb1.delete_at IS NULL
OPTIONAL MATCH (s:Statement)-[:REFERENCES_ENTITY]->(e)
WHERE s.delete_at IS NULL
OPTIONAL MATCH (s)-[:REFERENCES_ENTITY]->(nb2:ExtractedEntity {end_user_id: $end_user_id})
WHERE nb2.id <> e.id
  AND nb2.delete_at IS NULL
WITH e, collect(DISTINCT nb1) + collect(DISTINCT nb2) AS all_neighbors
UNWIND all_neighbors AS nb
WITH e, nb WHERE nb IS NOT NULL
OPTIONAL MATCH (nb)-[:BELONGS_TO_COMMUNITY]->(c:Community)
RETURN DISTINCT
    e.id                AS entity_id,
    nb.id               AS id,
    nb.name             AS name,
    nb.name_embedding   AS name_embedding,
    CASE WHEN c IS NOT NULL THEN c.community_id ELSE null END AS community_id
"""

GET_COMMUNITY_GRAPH_DATA = """
MATCH (c:Community {end_user_id: $end_user_id})
MATCH (e:ExtractedEntity {end_user_id: $end_user_id})-[b:BELONGS_TO_COMMUNITY]->(c)
WHERE e.delete_at IS NULL
OPTIONAL MATCH (e)-[r:EXTRACTED_RELATIONSHIP]-(e2:ExtractedEntity {end_user_id: $end_user_id})
WHERE e2.delete_at IS NULL
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

# Community 展开检索 ──────────────────────────────────────────────────
# 命中社区后，拉取该社区所有成员实体关联的 Statement 节点（主题→细节两级检索）
EXPAND_COMMUNITY_STATEMENTS = """
MATCH (c:Community {community_id: $community_id})
MATCH (e:ExtractedEntity)-[:BELONGS_TO_COMMUNITY]->(c)
WHERE e.delete_at IS NULL
MATCH (s:Statement)-[:REFERENCES_ENTITY]->(e)
WHERE s.end_user_id = $end_user_id
  AND s.delete_at IS NULL
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

# 感知记忆与分块的关联边（Chunk→Perceptual）
# 与 PERCEPTUAL_ENTITY_EDGE_SAVE 共存：前者按对话上下文建边，后者按语义相似度建边。
PERCEPTUAL_CHUNK_EDGE_SAVE = """
UNWIND $edges AS edge
MATCH (p:Perceptual {id: edge.perceptual_id, end_user_id: edge.end_user_id})
MATCH (c:Chunk {id: edge.chunk_id, end_user_id: edge.end_user_id})
WHERE c.delete_at IS NULL
MERGE (c)-[r:HAS_PERCEPTUAL]->(p)
ON CREATE SET r.end_user_id = edge.end_user_id,
    r.run_id = edge.run_id,
    r.created_at = edge.created_at,
    r.perceptual_type = edge.perceptual_type,
    r.perceptual_type_id = edge.perceptual_type_id
RETURN elementId(r) AS uuid
"""

# 感知记忆与实体的语义关联边（ExtractedEntity→Perceptual）
# 与 PERCEPTUAL_CHUNK_EDGE_SAVE 共存，两者采用相同的关系属性 schema。
# 方向：(ExtractedEntity)-[:HAS_PERCEPTUAL]->(Perceptual)
PERCEPTUAL_ENTITY_EDGE_SAVE = """
UNWIND $edges AS edge
MATCH (p:Perceptual {id: edge.perceptual_id, end_user_id: edge.end_user_id})
MATCH (e:ExtractedEntity {id: edge.entity_id, end_user_id: edge.end_user_id})
WHERE e.delete_at IS NULL
MERGE (e)-[r:HAS_PERCEPTUAL]->(p)
ON CREATE SET r.end_user_id = edge.end_user_id,
    r.run_id = edge.run_id,
    r.created_at = edge.created_at,
    r.perceptual_type = edge.perceptual_type,
    r.perceptual_type_id = edge.perceptual_type_id
RETURN elementId(r) AS uuid
"""

# -------------------
# search by user id
# -------------------
SEARCH_PERCEPTUAL_BY_USER_ID = """
MATCH (p:Perceptual)
WHERE p.end_user_id = $end_user_id AND p.id > $last_id
RETURN p.id AS id,
       p.summary_embedding AS embedding
ORDER BY p.id
LIMIT $batch_size
"""

SEARCH_STATEMENTS_BY_USER_ID = """
MATCH (s:Statement)
WHERE s.end_user_id = $end_user_id AND s.id > $last_id
  AND s.delete_at IS NULL
RETURN s.id AS id,
       s.statement_embedding AS embedding
ORDER BY s.id
LIMIT $batch_size
"""

SEARCH_ENTITIES_BY_USER_ID = """
MATCH (e:ExtractedEntity)
WHERE e.end_user_id = $end_user_id AND e.id > $last_id
  AND e.delete_at IS NULL
RETURN e.id AS id,
       e.name_embedding AS embedding
ORDER BY e.id
LIMIT $batch_size
"""

SEARCH_CHUNKS_BY_USER_ID = """
MATCH (c:Chunk)
WHERE c.end_user_id = $end_user_id AND c.id > $last_id
  AND c.delete_at IS NULL
RETURN c.id AS id,
       c.chunk_embedding AS embedding
ORDER BY c.id
LIMIT $batch_size
"""

SEARCH_MEMORY_SUMMARIES_BY_USER_ID = """
MATCH (s:MemorySummary)
WHERE s.end_user_id = $end_user_id AND s.id > $last_id
  AND s.delete_at IS NULL
RETURN s.id AS id,
       s.summary_embedding AS embedding
ORDER BY s.id
LIMIT $batch_size
"""

SEARCH_COMMUNITIES_BY_USER_ID = """
MATCH (c:Community)
WHERE c.end_user_id = $end_user_id AND c.community_id > $last_id
RETURN c.community_id AS id,
       c.summary_embedding AS embedding
ORDER BY c.community_id
LIMIT $batch_size
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
  AND s.delete_at IS NULL
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
  AND c.delete_at IS NULL
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
  AND e.delete_at IS NULL
RETURN e.id AS id,
       e.name AS name,
       COALESCE(e.aliases, []) AS aliases,
       e.end_user_id AS end_user_id,
       e.entity_type AS entity_type,
       e.description AS description,
       e.description_summary AS description_summary,
       e.event_timeline AS event_timeline,
       COALESCE(e.activation_value, e.importance_score, 0.5) AS activation_value,
       COALESCE(e.importance_score, 0.5) AS importance_score,
       e.last_access_time AS last_access_time,
       COALESCE(e.access_count, 0) AS access_count
"""

SEARCH_MEMORY_SUMMARIES_BY_IDS = """
MATCH (m:MemorySummary)
WHERE m.id IN $ids
  AND m.delete_at IS NULL
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
SEARCH_PERCEPTUALS_BY_FULLTEXT = """
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

SEARCH_STATEMENTS_BY_FULLTEXT = """
CALL db.index.fulltext.queryNodes("statementsFulltext", $query) YIELD node AS s, score
WHERE s.end_user_id = $end_user_id
  AND s.delete_at IS NULL
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
       COALESCE(s.access_count, 0) AS access_count,
       score
ORDER BY score DESC
LIMIT $limit
"""

SEARCH_ENTITIES_BY_FULLTEXT = """
CALL db.index.fulltext.queryNodes("entitiesFulltext", $query) YIELD node AS e, score
WHERE e.end_user_id = $end_user_id
  AND e.delete_at IS NULL
RETURN e.id AS id,
       e.name AS name,
       COALESCE(e.aliases, []) AS aliases,
       e.entity_type AS entity_type,
       e.description AS description,
       e.description_summary AS description_summary,
       e.event_timeline AS event_timeline,
       COALESCE(e.activation_value, e.importance_score, 0.5) AS activation_value,
       score
ORDER BY score DESC
LIMIT $limit
"""

SEARCH_CHUNKS_BY_FULLTEXT = """
CALL db.index.fulltext.queryNodes("chunksFulltext", $query) YIELD node AS c, score
WHERE c.end_user_id = $end_user_id
  AND c.delete_at IS NULL
RETURN c.id AS id,
       c.content AS content,
       COALESCE(c.activation_value, 0.5) AS activation_value,
       score
ORDER BY score DESC
LIMIT $limit
"""

# MemorySummary keyword search using fulltext index
SEARCH_MEMORY_SUMMARIES_BY_FULLTEXT = """
CALL db.index.fulltext.queryNodes("summariesFulltext", $query) YIELD node AS m, score
WHERE m.end_user_id = $end_user_id
  AND m.delete_at IS NULL
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
SEARCH_COMMUNITIES_BY_FULLTEXT = """
CALL db.index.fulltext.queryNodes("communitiesFulltext", $query) YIELD node AS c, score
WHERE c.end_user_id = $end_user_id
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
  AND n.delete_at IS NULL
  AND m.delete_at IS NULL
RETURN m.id AS id,
       n.name AS source_name,
       r.predicate AS relation_predicate,
       m.name AS target_name
"""

SEARCH_RELATION_BETWEEN_ENTITIES = """
MATCH (n:ExtractedEntity)-[r]-(m:ExtractedEntity)
WHERE n.end_user_id = $end_user_id AND n.id = $source_id AND m.id = $target_id
  AND n.delete_at IS NULL
  AND m.delete_at IS NULL
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
  AND n.delete_at IS NULL
  AND m.delete_at IS NULL
RETURN n.id AS source_id,
       n.name AS source_name,
       r.predicate AS relation_predicate,
       m.id AS target_id,
       m.name AS target_name
"""

SEARCH_USER_METADATA = """
MATCH (n:ExtractedEntity)
WHERE (n.end_user_id = $end_user_id AND n.entity_type ='用户')
  AND n.delete_at IS NULL
RETURN n.description AS description,
       n.aliases AS aliases,
       n.anchors AS anchors,
       n.beliefs_or_stances AS beliefs_or_stances,
       n.core_facts AS core_facts,
       n.event_timeline AS event_timeline,
       n.goals AS goals,
       n.interests AS interests,
       n.relations AS relations,
       n.traits AS traits,
       n.id AS id
"""

# ── 查询用户实体基本信息（供元数据提取使用） ──
USER_ENTITY_FOR_METADATA = """
MATCH (n:ExtractedEntity)
WHERE n.end_user_id = $end_user_id
  AND n.delete_at IS NULL
  AND (n.entity_type = '用户' OR toLower(n.name) IN ['用户', '我', 'user', 'i'])
RETURN n.id AS entity_id,
       n.name AS entity_name,
       n.description AS description,
       n.end_user_id AS end_user_id
"""

# -------------------
# Dialogue 检索（仅 write_mode='fast'：正写尚未接管的滞后窗口节点）
# -------------------
SEARCH_DIALOGUE_BY_FULLTEXT = """
CALL db.index.fulltext.queryNodes("dialogueFulltext", $query) YIELD node AS d, score
WHERE ($end_user_id IS NULL OR d.end_user_id = $end_user_id)
  AND d.delete_at IS NULL
  AND d.write_mode = 'fast'
RETURN d.id AS id,
       d.content AS content,
       d.created_at AS created_at,
       score
ORDER BY score DESC
LIMIT $limit
"""

# 向量召回的游标分页：注意别名 dialog_embedding AS embedding，
# 对齐 search_by_embedding 读取的 "embedding" 键。
SEARCH_DIALOGUE_BY_USER_ID = """
MATCH (d:Dialogue)
WHERE d.end_user_id = $end_user_id AND d.id > $last_id
  AND d.delete_at IS NULL
  AND d.write_mode = 'fast'
  AND d.dialog_embedding IS NOT NULL
RETURN d.id AS id,
       d.dialog_embedding AS embedding
ORDER BY d.id
LIMIT $batch_size
"""

SEARCH_DIALOGUE_BY_IDS = """
MATCH (d:Dialogue)
WHERE d.id IN $ids
  AND d.delete_at IS NULL
  AND d.write_mode = 'fast'
RETURN d.id AS id,
       d.end_user_id AS end_user_id,
       d.content AS content,
       d.created_at AS created_at
"""

FULLTEXT_QUERY_CYPHER_MAPPING = {
    Neo4jNodeType.STATEMENT: SEARCH_STATEMENTS_BY_FULLTEXT,
    Neo4jNodeType.EXTRACTEDENTITY: SEARCH_ENTITIES_BY_FULLTEXT,
    Neo4jNodeType.CHUNK: SEARCH_CHUNKS_BY_FULLTEXT,
    Neo4jNodeType.MEMORYSUMMARY: SEARCH_MEMORY_SUMMARIES_BY_FULLTEXT,
    Neo4jNodeType.COMMUNITY: SEARCH_COMMUNITIES_BY_FULLTEXT,
    Neo4jNodeType.PERCEPTUAL: SEARCH_PERCEPTUALS_BY_FULLTEXT,
    Neo4jNodeType.DIALOGUE: SEARCH_DIALOGUE_BY_FULLTEXT
}
USER_ID_QUERY_CYPHER_MAPPING = {
    Neo4jNodeType.STATEMENT: SEARCH_STATEMENTS_BY_USER_ID,
    Neo4jNodeType.EXTRACTEDENTITY: SEARCH_ENTITIES_BY_USER_ID,
    Neo4jNodeType.CHUNK: SEARCH_CHUNKS_BY_USER_ID,
    Neo4jNodeType.MEMORYSUMMARY: SEARCH_MEMORY_SUMMARIES_BY_USER_ID,
    Neo4jNodeType.COMMUNITY: SEARCH_COMMUNITIES_BY_USER_ID,
    Neo4jNodeType.PERCEPTUAL: SEARCH_PERCEPTUAL_BY_USER_ID,
    Neo4jNodeType.DIALOGUE: SEARCH_DIALOGUE_BY_USER_ID
}
NODE_ID_QUERY_CYPHER_MAPPING = {
    Neo4jNodeType.STATEMENT: SEARCH_STATEMENTS_BY_IDS,
    Neo4jNodeType.EXTRACTEDENTITY: SEARCH_ENTITIES_BY_IDS,
    Neo4jNodeType.CHUNK: SEARCH_CHUNKS_BY_IDS,
    Neo4jNodeType.MEMORYSUMMARY: SEARCH_MEMORY_SUMMARIES_BY_IDS,
    Neo4jNodeType.COMMUNITY: SEARCH_COMMUNITIES_BY_IDS,
    Neo4jNodeType.PERCEPTUAL: SEARCH_PERCEPTUAL_BY_IDS,
    Neo4jNodeType.DIALOGUE: SEARCH_DIALOGUE_BY_IDS
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
  AND e.delete_at IS NULL
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
WHERE e.delete_at IS NULL
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
  AND e.delete_at IS NULL
RETURN count(e) AS conflict_count
"""

REFLECTION_RENAME_ENTITY = """
MATCH (e:ExtractedEntity {id: $entity_id})
WHERE e.delete_at IS NULL
SET e.name = $new_name
RETURN e.id
"""

REFLECTION_UPDATE_NAME_EMBEDDING = """
MATCH (e:ExtractedEntity {id: $entity_id})
WHERE e.delete_at IS NULL
SET e.name_embedding = $name_embedding
RETURN e.id
"""
# --- Reflection Engine Layer 2: Entity Dedup ---
# 来源一：名称相似度
DEDUP_CANDIDATES_BY_NAME = """
MATCH (e1:ExtractedEntity)
WHERE e1.end_user_id = $end_user_id
  AND e1.delete_at IS NULL
  AND NOT toLower(e1.name) IN ['用户', '我', 'user', 'ai助手', '助手', '助理', 'ai', 'assistant', 'ai回复']
WITH e1
MATCH (e2:ExtractedEntity)
WHERE e2.end_user_id = $end_user_id
  AND e2.delete_at IS NULL
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
       e1.description_summary AS a_desc_summary, e2.description_summary AS b_desc_summary,
       CASE
         WHEN e1.name_embedding IS NULL OR e2.name_embedding IS NULL THEN 0.0
         ELSE vector.similarity.cosine(e1.name_embedding, e2.name_embedding)
       END AS emb_sim
LIMIT $candidate_cap
"""

# 路径B 通过name_embedding 相似度检索
DEDUP_CANDIDATES_BY_EMBED = """
MATCH (e1:ExtractedEntity)
WHERE e1.end_user_id = $end_user_id
  AND e1.delete_at IS NULL
  AND e1.name_embedding IS NOT NULL
  AND NOT toLower(e1.name) IN ['用户', '我', 'user', 'ai助手', '助手', '助理', 'ai', 'assistant', 'ai回复']
CALL db.index.vector.queryNodes('entity_embedding_index', $top_k, e1.name_embedding)
YIELD node AS e2, score
WHERE e2.end_user_id = $end_user_id
  AND e2.delete_at IS NULL
  AND e2.entity_type = e1.entity_type
  AND elementId(e1) < elementId(e2)
  AND score >= $theta_embed_floor
  AND NOT toLower(e2.name) IN ['用户', '我', 'user', 'ai助手', '助手', '助理', 'ai', 'assistant', 'ai回复']
RETURN e1.id AS a_id, e2.id AS b_id,
       e1.name AS a_name, e2.name AS b_name,
       e1.entity_type AS entity_type,
       e1.description AS a_desc, e2.description AS b_desc,
       e1.aliases AS a_aliases, e2.aliases AS b_aliases,
       e1.description_summary AS a_desc_summary, e2.description_summary AS b_desc_summary,
       score AS sim_embed
LIMIT $candidate_cap
"""

# 查两个实体各自度数（用于超级节点保护）
ENTITY_DEGREE_COUNT = """
MATCH (e:ExtractedEntity {end_user_id: $end_user_id})
WHERE e.delete_at IS NULL
  AND e.id IN [$id_a, $id_b]
RETURN e.id AS id, COUNT{ (e)--() } AS degree
"""

# 批量查多个实体度数（方案B 桶内同名直合用）
ENTITY_DEGREES_BY_IDS = """
MATCH (e:ExtractedEntity {end_user_id: $end_user_id})
WHERE e.delete_at IS NULL
  AND e.id IN $ids
RETURN e.id AS id, COUNT{ (e)--() } AS degree
"""

# 去重两个实体合并
DEDUP_MERGE_ENTITIES = """
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
WITH DISTINCT keeper, loser
DETACH DELETE loser
RETURN keeper.id AS merged_id
"""

# 方案B：查出指定类型下所有实体（全量送 LLM 分组判定）
DEDUP_FULL_SCAN_ENTITIES = """
MATCH (e:ExtractedEntity)
WHERE e.end_user_id = $end_user_id
  AND e.delete_at IS NULL
  AND e.entity_type = $entity_type
  AND NOT toLower(e.name) IN ['用户', '我', 'user', 'ai助手', '助手', '助理', 'ai', 'assistant', 'ai回复']
RETURN e.id AS entity_id, e.name AS name, e.entity_type AS entity_type,
       e.description AS description, e.description_summary AS description_summary,
       e.aliases AS aliases, e.created_at AS created_at
ORDER BY e.created_at
"""

# 方案B：查上次扫描后新增的实体数（增量判断，new_count=0 则跳过该类型）
DEDUP_FULL_SCAN_NEW_COUNT = """
MATCH (e:ExtractedEntity)
WHERE e.end_user_id = $end_user_id
  AND e.delete_at IS NULL
  AND e.entity_type = $entity_type
  AND e.created_at > $last_scan_time
  AND NOT toLower(e.name) IN ['用户', '我', 'user', 'ai助手', '助手', '助理', 'ai', 'assistant', 'ai回复']
RETURN count(e) AS new_count
"""

# 方案B：查该用户所有 entity_type 及数量（决定遍历哪些类型）
DEDUP_FULL_SCAN_ENTITY_TYPES = """
MATCH (e:ExtractedEntity)
WHERE e.end_user_id = $end_user_id
  AND e.delete_at IS NULL
  AND NOT toLower(e.name) IN ['用户', '我', 'user', 'ai助手', '助手', '助理', 'ai', 'assistant', 'ai回复']
RETURN DISTINCT e.entity_type AS entity_type, count(e) AS count
"""

# --- Reflection Engine Layer 2: Unresolved Entity (子问题5) ---

UNRESOLVED_STATEMENT_CANDIDATES = """
MATCH (s:Statement)
WHERE s.end_user_id = $end_user_id
  AND s.delete_at IS NULL
  AND s.has_unsolved_reference = true
RETURN s.id AS statement_id,
       s.statement AS statement_text,
       s.dialog_at AS dialog_at,
       s.chunk_id AS chunk_id,
       s.stmt_type AS stmt_type,
       s.temporal_info AS temporal_info,
       s.speaker AS speaker,
       s.valid_at AS valid_at,
       s.invalid_at AS invalid_at,
       s.run_id AS run_id
ORDER BY s.created_at ASC
LIMIT $batch_size
"""

UNRESOLVED_CONTEXT_CHUNKS = """
MATCH (c:Chunk {id: $chunk_id})
WHERE c.delete_at IS NULL
MATCH (nearby:Chunk {end_user_id: $end_user_id})
WHERE nearby.id <> c.id
  AND nearby.delete_at IS NULL
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
RETURN e.id AS entity_id, e.name AS name
"""

UNRESOLVED_UPDATE_NAME_EMBEDDING = """
MATCH (e:ExtractedEntity {id: $entity_id})
WHERE e.delete_at IS NULL
SET e.name_embedding = $name_embedding
RETURN e.id
"""

# 反思未解析消解中：把 LLM 输出的"用户"实体的 description 追加到全局用户节点。
# 用 entity_type='用户' 定位（而非 name='用户'）：兼容用户节点 name 是 "我"/"User" 等
# 历史变体的情况；end_user_id 唯一约束保证只命中一个全局用户节点。
UNRESOLVED_APPEND_USER_INFO = """
MATCH (e:ExtractedEntity {end_user_id: $end_user_id, entity_type: '用户'})
WHERE e.delete_at IS NULL
SET e.description = CASE
    WHEN $description IS NULL OR $description = '' THEN e.description
    WHEN e.description IS NULL OR e.description = '' THEN $description
    ELSE e.description + '；' + $description
END
RETURN e.id AS entity_id
"""

UNRESOLVED_CREATE_RELATIONSHIP = """
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
RETURN r.predicate AS predicate
"""

UNRESOLVED_CREATE_STATEMENT_ENTITY_EDGE = """
MATCH (s:Statement {id: $statement_id})
WHERE s.delete_at IS NULL
MATCH (e:ExtractedEntity {end_user_id: $end_user_id, name: $entity_name})
WHERE e.delete_at IS NULL
MERGE (s)-[r:REFERENCES_ENTITY]->(e)
SET r.end_user_id = $end_user_id,
    r.run_id = $run_id,
    r.created_at = $created_at,
    r.connect_strength = "weak"
RETURN s.id AS statement_id
"""

UNRESOLVED_UPDATE_STATEMENT_FLAG = """
MATCH (s:Statement {id: $statement_id})
WHERE s.delete_at IS NULL
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

# 别名校验候选收集：拉取所有 "别名属于" 边及两端节点上下文，喂给 LLM 判定。
# target 可为任意实体类型（canonical），不限用户实体。
GET_ALIAS_BELONGS_CANDIDATES = """
MATCH (alias:ExtractedEntity {end_user_id: $end_user_id})
      -[r:EXTRACTED_RELATIONSHIP {predicate: '别名属于'}]->
      (target:ExtractedEntity {end_user_id: $end_user_id})
WHERE alias.id <> target.id
  AND alias.delete_at IS NULL
  AND target.delete_at IS NULL
RETURN alias.id   AS alias_id,
       alias.name AS alias_name,
       alias.entity_type AS alias_entity_type,
       alias.description AS alias_description,
       alias.description_summary AS alias_description_summary,
       coalesce(alias.aliases, []) AS alias_aliases,
       target.id   AS target_id,
       target.name AS target_name,
       target.entity_type AS target_entity_type,
       target.description AS target_description,
       target.description_summary AS target_description_summary,
       coalesce(target.aliases, []) AS target_aliases
ORDER BY target.id, alias.id
"""

# 别名归并：将 predicate="别名属于" 的 EXTRACTED_RELATIONSHIP 边的 source.name
# 合并进 target.aliases（去重），并将 source.description 追加到 target.description（分号分隔）
# 仅处理 source.id 在 $alias_ids（LLM 判 merge 的别名）内的边。
MERGE_ALIAS_BELONGS_TO = """
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

RETURN target.name AS target_name, new_aliases AS updated_aliases, size(new_aliases) - size(existing_aliases) AS added_count
"""

# 边重定向：将别名节点（"别名属于"关系的 source，且在 $alias_ids 内）上的其他边重定向到 target。
# 处理两类边：
#   1. EXTRACTED_RELATIONSHIP：其他实体 → 别名节点 或 别名节点 → 其他实体
#   2. STATEMENT_ENTITY：陈述句 → 别名节点
# 三段用独立 CALL () {} 子查询隔离，避免空输入时分组聚合丢行导致后续段被跳过。
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
// 3. 陈述句 → 别名节点，重定向到 target
CALL () {
  MATCH (alias:ExtractedEntity {end_user_id: $end_user_id})-[ar3:EXTRACTED_RELATIONSHIP]->(user3:ExtractedEntity {end_user_id: $end_user_id})
  WHERE ar3.predicate = '别名属于' AND alias.id IN $alias_ids AND alias.delete_at IS NULL AND user3.delete_at IS NULL
  WITH DISTINCT alias, user3
  MATCH (stmt)-[r:STATEMENT_ENTITY]->(alias)
  CREATE (stmt)-[nr:STATEMENT_ENTITY]->(user3)
  SET nr = properties(r)
  DELETE r
  RETURN count(*) AS redirected_stmt
}
RETURN redirected_incoming, redirected_outgoing, redirected_stmt
"""

# 删除别名节点：在别名归并和边重定向完成后，删除 $alias_ids 内 predicate="别名属于" 的 source 节点。
# 此时这些节点的其他边已被 REDIRECT_ALIAS_EDGES 重定向完毕，
# 唯一剩余的边就是 (alias)-[:EXTRACTED_RELATIONSHIP {predicate:'别名属于'}]->(user)，
# 使用 DETACH DELETE 一并删除节点和该关系。
DELETE_ALIAS_NODES = """
MATCH (alias:ExtractedEntity {end_user_id: $end_user_id})-[r:EXTRACTED_RELATIONSHIP]->(user:ExtractedEntity {end_user_id: $end_user_id})
WHERE r.predicate = '别名属于' AND alias.id IN $alias_ids AND alias.delete_at IS NULL AND user.delete_at IS NULL
WITH alias, count(r) AS rel_count
DETACH DELETE alias
RETURN count(alias) AS deleted_count
"""

# drop：删除 LLM 判定为非别名的 "别名属于" 边，仅删边、保留别名节点。
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

# 查询用户实体节点的最新 aliases：用于别名归并完成后，将归并结果同步回 PostgreSQL
# end_user_info.aliases / other_name。判定用户实体的口径与 metadata_extractor.is_user_entity
# 保持一致：name 命中常见用户称呼，或 entity_type 为 '用户'。
GET_USER_ENTITY_ALIASES = """
MATCH (e:ExtractedEntity {end_user_id: $end_user_id})
WHERE e.delete_at IS NULL
  AND (toLower(e.name) IN ['用户', '我', 'user', 'i']
   OR e.entity_type = '用户')
RETURN e.id AS entity_id, e.name AS name, coalesce(e.aliases, []) AS aliases
"""

# ── 查询已有的特殊实体（用户、AI助手）以便复用 ID ──
# 用于 graph_saver 预处理阶段，确保同一个 end_user_id 下只有一个"用户"节点和一个"AI助手"节点
SPECIAL_ENTITY_QUERY = """
MATCH (e:ExtractedEntity)
WHERE e.end_user_id = $end_user_id AND toLower(e.name) IN $names
  AND e.delete_at IS NULL
RETURN e.id AS id, e.name AS name
"""

# --- UserSource: 保存用户规整前的原文节点 ---

USER_SOURCE_NODE_SAVE = """
UNWIND $nodes AS n
MERGE (us:UserSource {id: n.id})
SET us += {
    id: n.id,
    name: n.name,
    end_user_id: n.end_user_id,
    run_id: n.run_id,
    created_at: n.created_at,
    message_seq: n.message_seq,
    original_text: n.original_text,
    pruned_text: n.pruned_text,
    text_embedding: n.text_embedding
}
RETURN n.id AS uuid
"""

USER_SOURCE_ENTITY_EDGE_SAVE = """
UNWIND $edges AS edge
MATCH (us:UserSource {id: edge.source, end_user_id: edge.end_user_id})
MATCH (e:ExtractedEntity {id: edge.target, end_user_id: edge.end_user_id})
MERGE (us)-[r:HAS_ORIGINAL_CONTENT]->(e)
ON CREATE SET r.id = edge.id,
    r.end_user_id = edge.end_user_id,
    r.run_id = edge.run_id,
    r.created_at = edge.created_at
RETURN elementId(r) AS uuid
"""

# ── Entity → UserSource 回溯查询 ──
# 通过 HAS_ORIGINAL_CONTENT 边，从 ExtractedEntity 追溯到 UserSource 节点的原始文本。
# 边方向：UserSource -[HAS_ORIGINAL_CONTENT]-> ExtractedEntity
FETCH_USER_SOURCES_FOR_ENTITIES = """
MATCH (us:UserSource)-[r:HAS_ORIGINAL_CONTENT]->(e:ExtractedEntity)
WHERE e.id IN $entity_ids
  AND us.end_user_id = $end_user_id
RETURN e.id AS entity_id, us.original_text AS original_text
"""

DELETE_NODE_BY_ELEMENT_ID = """
MATCH (n)
WHERE elementId(n) = $element_id AND n.end_user_id = $end_user_id
WITH n, elementId(n) AS node_id, n.content AS content, n.statement AS statement,
     n.name AS name, n.text AS text, labels(n) AS labels
DETACH DELETE n
RETURN count(n) AS deleted, node_id, content, statement, name, text, labels
"""

# ── Forgetting engine (soft-delete) ────────────────────────────────────

FORGET_COUNT_ACTIVE_NODES = """
    MATCH (n {end_user_id: $end_user_id})
    WHERE n.delete_at IS NULL
      AND (n:Statement OR n:Chunk OR n:ExtractedEntity)
    RETURN count(n) AS cnt
"""

FORGET_COUNT_ACTIVE_NODES_BATCH = """
    MATCH (n)
    WHERE n.end_user_id IN $end_user_ids
      AND n.delete_at IS NULL
      AND (n:Statement OR n:Chunk OR n:ExtractedEntity)
    RETURN n.end_user_id AS end_user_id, count(n) AS cnt
"""

FORGET_QUOTA_BREAKDOWN = """
    MATCH (n {end_user_id: $end_user_id})
    WHERE n.delete_at IS NULL
    RETURN
      sum(CASE WHEN n:Statement THEN 1 ELSE 0 END) AS statement,
      sum(CASE WHEN n:Chunk THEN 1 ELSE 0 END) AS chunk,
      sum(CASE WHEN n:ExtractedEntity THEN 1 ELSE 0 END) AS entity,
      sum(CASE WHEN n:MemorySummary THEN 1 ELSE 0 END) AS summary,
      sum(CASE WHEN n:Dialogue THEN 1 ELSE 0 END) AS dialogue
"""

FORGET_COUNT_AUXILIARY_ACTIVE_NODES = """
    MATCH (n {end_user_id: $end_user_id})
    WHERE n.delete_at IS NULL
      AND (n:MemorySummary OR n:Dialogue)
    RETURN count(n) AS cnt
"""

FORGET_SOFT_DELETE_BY_ELEMENT_IDS = """
    MATCH (n {end_user_id: $end_user_id})
    WHERE n.delete_at IS NULL
      AND elementId(n) IN $element_ids
      AND (NOT n:Statement OR coalesce(n.is_permanent, false) = false)
    SET n.delete_at = datetime($now)
    RETURN collect(elementId(n)) AS deleted_element_ids
"""

FORGET_RECOVER_IDEMPOTENT_BY_ELEMENT_ID = """
    MATCH (n)
    WHERE elementId(n) = $element_id
      AND n.end_user_id = $end_user_id
      AND (n:Statement OR n:Chunk OR n:ExtractedEntity OR n:MemorySummary OR n:Dialogue)
    WITH n, n.delete_at IS NOT NULL AS recovered_now
    SET n.delete_at = CASE WHEN recovered_now THEN NULL ELSE n.delete_at END,
        n.last_access_time = CASE WHEN recovered_now THEN $now ELSE n.last_access_time END
    RETURN elementId(n) AS node_id, labels(n) AS labels, recovered_now
"""

FORGET_CORE_CANDIDATES = f"""
CALL () {{
    MATCH (c:Chunk {{end_user_id: $end_user_id}})
    WHERE c.delete_at IS NULL
    WITH c, elementId(c) AS element_id,
         toString(c.created_at) AS created_text,
         toFloat(c.topology_score) AS raw_g
    WHERE created_text =~ $iso_datetime_pattern
    WITH c, element_id, created_text, raw_g,
         CASE
           WHEN coalesce(toString(c.last_access_time) =~ $iso_datetime_pattern, false)
           THEN toString(c.last_access_time) ELSE created_text
         END AS access_text
    WITH c, element_id,
         datetime(created_text).epochMillis AS created_epoch,
         datetime(access_text).epochMillis AS eff_access_ms,
         CASE
           WHEN raw_g IS NULL OR isNaN(raw_g) THEN 0.0
           WHEN raw_g < 0.0 THEN 0.0
           WHEN raw_g > 1.0 THEN 1.0
           ELSE raw_g
         END AS g
    WHERE eff_access_ms < $cutoff_ms
    WITH c, element_id, created_epoch, eff_access_ms,
         g, {T_CYPHER_EXPR} AS t
    WITH c, element_id, created_epoch, eff_access_ms,
         {G_WEIGHT} * g + {T_WEIGHT} * t AS forgetting_activation
    WHERE forgetting_activation < $forgetting_threshold
    RETURN 'Chunk' AS node_type, element_id, coalesce(c.content, '') AS content,
           forgetting_activation, eff_access_ms, created_epoch
    ORDER BY forgetting_activation ASC, eff_access_ms ASC, created_epoch ASC, element_id ASC
    LIMIT $batch_size

    UNION ALL

    MATCH (s:Statement {{end_user_id: $end_user_id}})
    WHERE s.delete_at IS NULL
      AND coalesce(s.is_permanent, false) = false
    WITH s, elementId(s) AS element_id,
         toString(s.created_at) AS created_text,
         toFloat(s.topology_score) AS raw_g
    WHERE created_text =~ $iso_datetime_pattern
    WITH s, element_id, created_text, raw_g,
         CASE
           WHEN coalesce(toString(s.last_access_time) =~ $iso_datetime_pattern, false)
           THEN toString(s.last_access_time) ELSE created_text
         END AS access_text
    WITH s, element_id,
         datetime(created_text).epochMillis AS created_epoch,
         datetime(access_text).epochMillis AS eff_access_ms,
         CASE
           WHEN raw_g IS NULL OR isNaN(raw_g) THEN 0.0
           WHEN raw_g < 0.0 THEN 0.0
           WHEN raw_g > 1.0 THEN 1.0
           ELSE raw_g
         END AS g
    WHERE eff_access_ms < $cutoff_ms
    WITH s, element_id, created_epoch, eff_access_ms,
         g, {T_CYPHER_EXPR} AS t
    WITH s, element_id, created_epoch, eff_access_ms,
         {G_WEIGHT} * g + {T_WEIGHT} * t AS forgetting_activation
    WHERE forgetting_activation < $forgetting_threshold
    RETURN 'Statement' AS node_type, element_id, coalesce(s.statement, '') AS content,
           forgetting_activation, eff_access_ms, created_epoch
    ORDER BY forgetting_activation ASC, eff_access_ms ASC, created_epoch ASC, element_id ASC
    LIMIT $batch_size

    UNION ALL

    MATCH (e:ExtractedEntity {{end_user_id: $end_user_id}})
    WHERE e.delete_at IS NULL
      AND coalesce(e.extraction_count, 0) < $protection_threshold
      AND e.name <> '用户'
    WITH e, elementId(e) AS element_id,
         toString(e.created_at) AS created_text,
         toFloat(e.topology_score) AS raw_g
    WHERE created_text =~ $iso_datetime_pattern
    WITH e, element_id, created_text, raw_g,
         CASE
           WHEN coalesce(toString(e.last_access_time) =~ $iso_datetime_pattern, false)
           THEN toString(e.last_access_time) ELSE created_text
         END AS access_text
    WITH e, element_id,
         datetime(created_text).epochMillis AS created_epoch,
         datetime(access_text).epochMillis AS eff_access_ms,
         CASE
           WHEN raw_g IS NULL OR isNaN(raw_g) THEN 0.0
           WHEN raw_g < 0.0 THEN 0.0
           WHEN raw_g > 1.0 THEN 1.0
           ELSE raw_g
         END AS g
    WHERE eff_access_ms < $cutoff_ms
    WITH e, element_id, created_epoch, eff_access_ms,
         g, {T_CYPHER_EXPR} AS t
    WITH e, element_id, created_epoch, eff_access_ms,
         {G_WEIGHT} * g + {T_WEIGHT} * t AS forgetting_activation
    WHERE forgetting_activation < $forgetting_threshold
    RETURN 'ExtractedEntity' AS node_type, element_id, coalesce(e.name, '') AS content,
           forgetting_activation, eff_access_ms, created_epoch
    ORDER BY forgetting_activation ASC, eff_access_ms ASC, created_epoch ASC, element_id ASC
    LIMIT $batch_size
}}
RETURN *
ORDER BY forgetting_activation ASC, eff_access_ms ASC, created_epoch ASC, element_id ASC
LIMIT $batch_size
"""

FORGET_AUXILIARY_CANDIDATES = """
    MATCH (n {end_user_id: $end_user_id})
    WHERE n.delete_at IS NULL
      AND (n:MemorySummary OR n:Dialogue)
      AND toString(n.created_at) =~ $iso_datetime_pattern
    WITH n, elementId(n) AS element_id,
         datetime(toString(n.created_at)).epochMillis AS created_epoch
    RETURN CASE WHEN n:MemorySummary THEN 'MemorySummary' ELSE 'Dialogue' END AS node_type,
           element_id,
           CASE WHEN n:MemorySummary THEN coalesce(n.content, n.name, '')
                ELSE left(coalesce(n.content, ''), $content_max_len) END AS content,
           created_epoch
    ORDER BY created_epoch ASC, element_id ASC
    LIMIT $batch_size
"""

# 将所有节点的 end_user_id 从 old 改为 new
END_USER_MERGE_REASSIGN_NODES = """
MATCH (n)
WHERE n.end_user_id = $old_id
SET n.end_user_id = $new_id
RETURN count(n) AS updated_nodes
"""

# 将所有关系的 end_user_id 从 old 改为 new
END_USER_MERGE_REASSIGN_EDGES = """
MATCH ()-[r]->()
WHERE r.end_user_id = $old_id
SET r.end_user_id = $new_id
RETURN count(r) AS updated_edges
"""

# 查找指定 end_user 的 User 实体节点
END_USER_MERGE_FIND_USER_ENTITIES = """
MATCH (n:ExtractedEntity {end_user_id: $end_user_id})
WHERE n.name = '用户'
RETURN n, elementId(n) AS elem_id
"""

# 合并更新 User 实体的所有属性
END_USER_MERGE_UPDATE_USER = """
MATCH (n:ExtractedEntity)
WHERE elementId(n) = $elem_id
SET n.description = $description,
    n.description_summary = $description_summary,
    n.aliases = $aliases,
    n.anchors = $anchors,
    n.beliefs_or_stances = $beliefs_or_stances,
    n.core_facts = $core_facts,
    n.description_timeline = $description_timeline,
    n.event_timeline = $event_timeline,
    n.events = $events,
    n.goals = $goals,
    n.interests = $interests,
    n.relations = $relations,
    n.traits = $traits
"""

# 重定向 source User 实体的入边到 target User 实体（保留原始关系类型）
END_USER_MERGE_REDIRECT_INCOMING = """
MATCH (src:ExtractedEntity {end_user_id: $old_id})
WHERE src.name = '用户'
MATCH (tgt:ExtractedEntity)
WHERE (tgt.name = '用户')
  AND tgt.end_user_id = $new_id
WITH src, tgt
MATCH (src)<-[r_in]-(other)
WHERE NOT (other:ExtractedEntity)
WITH other, tgt, r_in
CALL apoc.create.relationship(other, type(r_in), properties(r_in), tgt)
YIELD rel AS r_new
SET r_new.end_user_id = $new_id
DELETE r_in
"""

# 重定向 source User 实体的出边到 target User 实体（保留原始关系类型）
END_USER_MERGE_REDIRECT_OUTGOING = """
MATCH (src:ExtractedEntity {end_user_id: $old_id})
WHERE src.name = '用户'
MATCH (tgt:ExtractedEntity)
WHERE (tgt.name = '用户')
  AND tgt.end_user_id = $new_id
WITH src, tgt
MATCH (src)-[r_out]->(other)
WHERE NOT (other:ExtractedEntity)
WITH other, tgt, r_out
CALL apoc.create.relationship(tgt, type(r_out), properties(r_out), other)
YIELD rel AS r_new
SET r_new.end_user_id = $new_id
DELETE r_out
"""

# 删除 source 的 User 实体节点
END_USER_MERGE_DELETE_USER_ENTITY = """
MATCH (n:ExtractedEntity {end_user_id: $old_id})
WHERE n.name = '用户'
DETACH DELETE n
"""

# 将 source User 实体的 end_user_id 改为 target（仅 source 有 User 时）
END_USER_MERGE_REASSIGN_USER_ENTITY = """
MATCH (n:ExtractedEntity {end_user_id: $old_id})
WHERE n.name = '用户'
SET n.end_user_id = $new_id
"""

GDS_GRAPH_BUILD = """
CALL gds.graph.project.cypher(
    $end_user_id,
    'MATCH (n)
    WHERE n.end_user_id = $endUserId
     AND n.delete_at IS NULL
     AND n.id IS NOT NULL
     AND any(l IN labels(n) WHERE l IN ["Statement","MemorySummary","Chunk","ExtractedEntity","Perceptual"])
     AND (n.name IS NULL OR n.name <> "用户")
    RETURN id(n) AS id',
    'MATCH (a)-[r]-(b)
    WHERE a.end_user_id = $endUserId AND b.end_user_id = $endUserId
     AND a.delete_at IS NULL AND b.delete_at IS NULL
     AND a <> b AND a.id IS NOT NULL AND b.id IS NOT NULL
     AND any(l IN labels(a) WHERE l IN ["Statement","MemorySummary","Chunk","ExtractedEntity","Perceptual"])
     AND any(l IN labels(b) WHERE l IN ["Statement","MemorySummary","Chunk","ExtractedEntity","Perceptual"])
     AND (a.name IS NULL OR a.name <> "用户") AND (b.name IS NULL OR b.name <> "用户")
    WITH a, b, count(r) AS parallel_count
    WITH a, b, parallel_count,
        [[id(a), id(b)], [id(b), id(a)]] AS directed_pairs
    UNWIND directed_pairs AS pair
    RETURN pair[0] AS source, pair[1] AS target, toFloat(parallel_count) AS weight',
    {
        parameters: {endUserId: $end_user_id }
    }
);"""

G_SCORE = """
CALL gds.eigenvector.write($end_user_id, {
      relationshipWeightProperty: 'weight',
      scaler: 'Max',
      writeProperty: 'topology_score',
      maxIterations: 50,
      tolerance: 0.000005
  })
"""

CLEAR_GRAPH = f"CALL gds.graph.drop($end_user_id, false);"
