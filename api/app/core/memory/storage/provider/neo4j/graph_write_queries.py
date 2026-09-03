"""Neo4j queries owned by the memory storage graph writer."""

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
    valid_at: statement.valid_at,
    invalid_at: statement.invalid_at,
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

SPECIAL_ENTITY_QUERY = """
MATCH (e:ExtractedEntity)
WHERE e.end_user_id = $end_user_id AND toLower(e.name) IN $names
  AND e.delete_at IS NULL
RETURN e.id AS id, e.name AS name
"""

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
