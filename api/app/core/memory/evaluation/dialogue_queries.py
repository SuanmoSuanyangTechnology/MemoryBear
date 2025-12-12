"""
Dialogue search queries for evaluation purposes.
This file contains Cypher queries for searching dialogues, entities, and chunks.
Placed in evaluation directory to avoid circular imports with src modules.
"""

# Entity search queries
SEARCH_ENTITIES_BY_NAME = """
MATCH (e:Entity)
WHERE e.name = $name
RETURN e
"""

SEARCH_ENTITIES_BY_NAME_FALLBACK = """
MATCH (e:Entity)
WHERE e.name CONTAINS $name
RETURN e
"""

# Chunk search queries
SEARCH_CHUNKS_BY_CONTENT = """
MATCH (c:Chunk)
WHERE c.content CONTAINS $content
RETURN c
"""

# Dialogue search queries
SEARCH_DIALOGUE_BY_DIALOG_ID = """
MATCH (d:Dialogue)
WHERE d.dialog_id = $dialog_id
RETURN d
"""

SEARCH_DIALOGUES_BY_CONTENT = """
MATCH (d:Dialogue)
WHERE d.content CONTAINS $q
RETURN d
"""

DIALOGUE_EMBEDDING_SEARCH = """
WITH $embedding AS q
MATCH (d:Dialogue)
WHERE d.dialog_embedding IS NOT NULL
  AND ($group_id IS NULL OR d.group_id = $group_id)
WITH d, q, d.dialog_embedding AS v
WITH d,
     reduce(dot = 0.0, i IN range(0, size(q)-1) | dot + toFloat(q[i]) * toFloat(v[i])) AS dot,
     sqrt(reduce(qs = 0.0, i IN range(0, size(q)-1) | qs + toFloat(q[i]) * toFloat(q[i]))) AS qnorm,
     sqrt(reduce(vs = 0.0, i IN range(0, size(v)-1) | vs + toFloat(v[i]) * toFloat(v[i]))) AS vnorm
WITH d, CASE WHEN qnorm = 0 OR vnorm = 0 THEN 0.0 ELSE dot / (qnorm * vnorm) END AS score
WHERE score > $threshold
RETURN d.id AS dialog_id,
       d.group_id AS group_id,
       d.content AS content,
       d.created_at AS created_at,
       d.expired_at AS expired_at,
       score
ORDER BY score DESC
LIMIT $limit
"""
