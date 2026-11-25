"""Read-only queries matching the automatic forgetting candidate policy."""

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

FORGET_CORE_CANDIDATES = """
CALL () {
    MATCH (c:Chunk {end_user_id: $end_user_id})
    WHERE c.delete_at IS NULL
      AND c.topology_score IS NOT NULL
      AND (
          NOT $isolated_only OR NOT EXISTS {
              MATCH (c)--(related)
              WHERE related <> c
                AND related.end_user_id = $end_user_id
                AND related.delete_at IS NULL
          }
      )
    WITH c, elementId(c) AS element_id,
         CASE
           WHEN coalesce(toString(c.created_at) =~ $iso_datetime_pattern, false)
           THEN datetime(toString(c.created_at)).epochMillis
           ELSE 0
         END AS created_epoch,
         toFloat(c.topology_score) AS raw_g
    WITH c, element_id, created_epoch,
         CASE
           WHEN raw_g IS NULL OR isNaN(raw_g) THEN 0.0
           WHEN raw_g < 0.0 THEN 0.0
           WHEN raw_g > 1.0 THEN 1.0
           ELSE raw_g
         END AS g
    WITH c, element_id, created_epoch, g
    WHERE g < 1.0
    WITH c, element_id, created_epoch, g, CASE
  WHEN created_epoch >= $evaluated_at_ms THEN 1.0
  ELSE 2.0 ^ (- ($evaluated_at_ms - created_epoch) / 2592000000.0)
END AS t
    WITH c, element_id, created_epoch,
         0.0 * g + 1.0 * t AS forgetting_activation
    RETURN 'Chunk' AS node_type, element_id, coalesce(c.content, '') AS content,
           forgetting_activation, created_epoch
    ORDER BY forgetting_activation ASC, created_epoch ASC, element_id ASC
    LIMIT $batch_size

    UNION ALL

    MATCH (s:Statement {end_user_id: $end_user_id})
    WHERE s.delete_at IS NULL
      AND coalesce(s.is_permanent, false) = false
      AND s.topology_score IS NOT NULL
      AND (
          NOT $isolated_only OR NOT EXISTS {
              MATCH (s)--(related)
              WHERE related <> s
                AND related.end_user_id = $end_user_id
                AND related.delete_at IS NULL
          }
      )
    WITH s, elementId(s) AS element_id,
         CASE
           WHEN coalesce(toString(s.created_at) =~ $iso_datetime_pattern, false)
           THEN datetime(toString(s.created_at)).epochMillis
           ELSE 0
         END AS created_epoch,
         toFloat(s.topology_score) AS raw_g
    WITH s, element_id, created_epoch,
         CASE
           WHEN raw_g IS NULL OR isNaN(raw_g) THEN 0.0
           WHEN raw_g < 0.0 THEN 0.0
           WHEN raw_g > 1.0 THEN 1.0
           ELSE raw_g
         END AS g
    WITH s, element_id, created_epoch, g
    WHERE g < 1.0
    WITH s, element_id, created_epoch, g, CASE
  WHEN created_epoch >= $evaluated_at_ms THEN 1.0
  ELSE 2.0 ^ (- ($evaluated_at_ms - created_epoch) / 2592000000.0)
END AS t
    WITH s, element_id, created_epoch,
         0.0 * g + 1.0 * t AS forgetting_activation
    RETURN 'Statement' AS node_type, element_id, coalesce(s.statement, '') AS content,
           forgetting_activation, created_epoch
    ORDER BY forgetting_activation ASC, created_epoch ASC, element_id ASC
    LIMIT $batch_size

    UNION ALL

    MATCH (e:ExtractedEntity {end_user_id: $end_user_id})
    WHERE e.delete_at IS NULL
      AND coalesce(e.extraction_count, 0) < $protection_threshold
      AND e.name <> '用户'
      AND e.topology_score IS NOT NULL
      AND (
          NOT $isolated_only OR NOT EXISTS {
              MATCH (e)--(related)
              WHERE related <> e
                AND related.end_user_id = $end_user_id
                AND related.delete_at IS NULL
          }
      )
    WITH e, elementId(e) AS element_id,
         CASE
           WHEN coalesce(toString(e.created_at) =~ $iso_datetime_pattern, false)
           THEN datetime(toString(e.created_at)).epochMillis
           ELSE 0
         END AS created_epoch,
         toFloat(e.topology_score) AS raw_g
    WITH e, element_id, created_epoch,
         CASE
           WHEN raw_g IS NULL OR isNaN(raw_g) THEN 0.0
           WHEN raw_g < 0.0 THEN 0.0
           WHEN raw_g > 1.0 THEN 1.0
           ELSE raw_g
         END AS g
    WITH e, element_id, created_epoch, g
    WHERE g < 1.0
    WITH e, element_id, created_epoch, g, CASE
  WHEN created_epoch >= $evaluated_at_ms THEN 1.0
  ELSE 2.0 ^ (- ($evaluated_at_ms - created_epoch) / 2592000000.0)
END AS t
    WITH e, element_id, created_epoch,
         0.0 * g + 1.0 * t AS forgetting_activation
    RETURN 'ExtractedEntity' AS node_type, element_id, coalesce(e.name, '') AS content,
           forgetting_activation, created_epoch
    ORDER BY forgetting_activation ASC, created_epoch ASC, element_id ASC
    LIMIT $batch_size
}
RETURN *
ORDER BY forgetting_activation ASC, created_epoch ASC, element_id ASC
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
ISO_DATETIME_PATTERN = (
    "^\\d{4}-\\d{2}-\\d{2}[T ]\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?(?:Z|[+-]\\d{2}:\\d{2})?$"
)
