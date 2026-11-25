"""Scoped, atomic graph operations for the forgetting context."""

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from src.infrastructure.persistence.neo4j.forgetting_queries import MERGE_PAIR


class Neo4jForgettingSource:
    def __init__(self, neo4j) -> None:
        self.neo4j = neo4j

    def knowledge_count(self, user_id: str) -> int:
        rows = self.neo4j.execute_query(
            """
        MATCH (n {end_user_id:$uid})
        WHERE n.delete_at IS NULL AND (n:Statement OR n:ExtractedEntity OR n:MemorySummary)
        RETURN count(n) AS count
        """,
            uid=user_id,
        )
        return int(rows[0]["count"])

    def active_count(self, user_id: str) -> int:
        rows = self.neo4j.execute_query(
            """
        MATCH (n {end_user_id:$uid}) WHERE n.delete_at IS NULL
          AND (n:Statement OR n:Chunk OR n:ExtractedEntity) RETURN count(n) AS count
        """,
            uid=user_id,
        )
        return int(rows[0]["count"])

    def forgetting_pairs(self, user_id: str, threshold: float, days: int, limit: int):
        return self.neo4j.execute_query(
            """
        MATCH (s:Statement {end_user_id:$uid})-[r]-(e:ExtractedEntity {end_user_id:$uid})
        WHERE s.delete_at IS NULL AND e.delete_at IS NULL
          AND s.activation_value < $threshold AND e.activation_value < $threshold
          AND s.last_access_time < $cutoff AND e.last_access_time < $cutoff
          AND (e.entity_type IS NULL OR e.entity_type <> 'Person')
        RETURN s.id AS statement_id, s.statement AS statement_text,
               s.activation_value AS statement_activation,
               s.importance_score AS statement_importance,
               e.id AS entity_id, e.name AS entity_name, e.entity_type AS entity_type,
               e.activation_value AS entity_activation, e.importance_score AS entity_importance,
               (s.activation_value+e.activation_value)/2.0 AS avg_activation
        ORDER BY avg_activation ASC LIMIT $limit
        """,
            uid=user_id,
            threshold=threshold,
            cutoff=(datetime.now(UTC) - timedelta(days=days)).isoformat().replace("+00:00", "Z"),
            limit=limit,
        )

    def merge_pair(self, context, pair, summary, job_id):
        identity = f"{job_id}:{context['end_user_id']}:{pair['statement_id']}:{pair['entity_id']}"
        summary_id = "summary_" + uuid5(NAMESPACE_URL, identity).hex
        # A previous attempt may have committed before PG reconciliation failed.
        # Existing graph results are scoped by user and task identity.
        done = self.neo4j.execute_query(
            "MATCH (s:MemorySummary {id:$id,end_user_id:$uid}) RETURN s.id AS id",
            id=summary_id,
            uid=context["end_user_id"],
        )
        if done:
            return True
        params = {
            **pair,
            "end_user_id": context["end_user_id"],
            "job_id": job_id,
            "summary_id": summary_id,
            "summary_text": summary["content"],
            "title": summary["title"],
            "episodic_type": summary["episodic_type"],
            "inherited_activation": max(pair["statement_activation"], pair["entity_activation"]),
            "inherited_importance": max(
                pair.get("statement_importance") or 0, pair.get("entity_importance") or 0
            ),
            "current_time": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        with self.neo4j.session(access_mode="WRITE") as session:
            rows = session.execute_write(lambda tx: tx.run(MERGE_PAIR, **params).data())
        return bool(rows)

    def activation_stats(self, user_id: str, threshold: float):
        rows = self.neo4j.execute_query(
            """
            MATCH (n {end_user_id:$uid}) WHERE n.delete_at IS NULL
              AND (n:Statement OR n:ExtractedEntity OR n:MemorySummary OR n:Chunk)
            RETURN avg(n.activation_value) AS average_activation,
                   sum(CASE WHEN n.activation_value < $threshold THEN 1 ELSE 0 END)
                       AS low_activation_nodes
            """,
            uid=user_id,
            threshold=threshold,
        )
        return rows[0]
