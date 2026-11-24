"""Scoped, atomic and deterministic legacy pair fusion."""

MERGE_PAIR = """OPTIONAL MATCH (done:MemorySummary {id: $summary_id, end_user_id: $end_user_id})
CALL (done) {
 WITH done WHERE done IS NULL

            // 首先检查节点是否存在
            OPTIONAL MATCH (s:Statement {id: $statement_id, end_user_id: $end_user_id})
            OPTIONAL MATCH (e:ExtractedEntity {id: $entity_id, end_user_id: $end_user_id})

            // 如果任一节点不存在，直接返回 null（不执行后续操作）
            WITH s, e
            WHERE s IS NOT NULL AND e IS NOT NULL AND s.delete_at IS NULL AND e.delete_at IS NULL
 AND (e.entity_type IS NULL OR e.entity_type <> 'Person')

            // 创建 MemorySummary 节点
            CREATE (ms:MemorySummary {
                id: $summary_id,
                summary: $summary_text,
                name: $title,
                memory_type: $episodic_type,
                original_statement_id: $statement_id,
                migration_job_id: $job_id,
                audit_statement: $statement_text,
                audit_entity: $entity_name,
                original_entity_id: $entity_id,
                activation_value: $inherited_activation,
                importance_score: $inherited_importance,
                access_history: [$current_time],
                last_access_time: $current_time,
                access_count: 1,
                version: 1,
                end_user_id: $end_user_id,
                created_at: datetime($current_time),
                merged_at: datetime($current_time)
            })

            // 转移 Statement 的出边到 MemorySummary（只转移目标节点仍存在的边）
            WITH ms, s, e
            CALL (ms, s, e) {
                OPTIONAL MATCH (s)-[r_out]->(target)
                WHERE target <> e AND r_out IS NOT NULL AND target IS NOT NULL
                  AND target.end_user_id = $end_user_id
                FOREACH (_ IN CASE WHEN target IS NOT NULL THEN [1] ELSE [] END |
                    MERGE (ms)-[new_rel:DERIVED_FROM]->(target)
                    ON CREATE SET
                        new_rel = properties(r_out),
                        new_rel.original_relationship_type = type(r_out),
                        new_rel.merged_from_statement = true,
                        new_rel.merge_count = 1
                    ON MATCH SET
                        new_rel.merge_count = coalesce(new_rel.merge_count, 0) + 1
                )
            }

            // 转移 Statement 的入边到 MemorySummary（只转移源节点仍存在的边）
            WITH ms, s, e
            CALL (ms, s, e) {
                OPTIONAL MATCH (source)-[r_in]->(s)
                WHERE r_in IS NOT NULL AND source IS NOT NULL
                  AND source.end_user_id = $end_user_id
                FOREACH (_ IN CASE WHEN source IS NOT NULL THEN [1] ELSE [] END |
                    MERGE (source)-[new_rel:DERIVED_FROM]->(ms)
                    ON CREATE SET
                        new_rel = properties(r_in),
                        new_rel.original_relationship_type = type(r_in),
                        new_rel.merged_from_statement = true,
                        new_rel.merge_count = 1
                    ON MATCH SET
                        new_rel.merge_count = coalesce(new_rel.merge_count, 0) + 1
                )
            }

            // 转移 Entity 的出边到 MemorySummary（只转移目标节点仍存在的边）
            WITH ms, s, e
            CALL (ms, s, e) {
                OPTIONAL MATCH (e)-[r_out]->(target)
                WHERE target <> s AND r_out IS NOT NULL AND target IS NOT NULL
                  AND target.end_user_id = $end_user_id
                FOREACH (_ IN CASE WHEN target IS NOT NULL THEN [1] ELSE [] END |
                    MERGE (ms)-[new_rel:DERIVED_FROM]->(target)
                    ON CREATE SET
                        new_rel = properties(r_out),
                        new_rel.original_relationship_type = type(r_out),
                        new_rel.merged_from_entity = true,
                        new_rel.merge_count = 1
                    ON MATCH SET
                        new_rel.merge_count = coalesce(new_rel.merge_count, 0) + 1
                )
            }

            // 转移 Entity 的入边到 MemorySummary（只转移源节点仍存在的边）
            WITH ms, s, e
            CALL (ms, s, e) {
                OPTIONAL MATCH (source)-[r_in]->(e)
                WHERE source <> s AND r_in IS NOT NULL AND source IS NOT NULL
                  AND source.end_user_id = $end_user_id
                FOREACH (_ IN CASE WHEN source IS NOT NULL THEN [1] ELSE [] END |
                    MERGE (source)-[new_rel:DERIVED_FROM]->(ms)
                    ON CREATE SET
                        new_rel = properties(r_in),
                        new_rel.original_relationship_type = type(r_in),
                        new_rel.merged_from_entity = true,
                        new_rel.merge_count = 1
                    ON MATCH SET
                        new_rel.merge_count = coalesce(new_rel.merge_count, 0) + 1
                )
            }

            // 删除原始节点
            WITH ms, s, e
            DETACH DELETE s, e

            RETURN ms.id as summary_id

 UNION ALL
 WITH done WHERE done IS NOT NULL RETURN done.id AS summary_id
}
RETURN summary_id
"""
