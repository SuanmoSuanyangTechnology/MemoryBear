"""Emotion evidence from the existing synchronous graph client."""

from datetime import UTC, datetime, timedelta


class Neo4jEmotionSource:
    def __init__(self, neo4j) -> None:
        self.neo4j = neo4j

    def emotion_context(self, user_id: str):
        from src.domain.shared.emotion_vocabulary import normalize_emotion_type_or_raw

        rows = []
        time_range = "30d"
        for days in (30, 90):
            rows = self.neo4j.execute_query(
                """
                MATCH (s:Statement {end_user_id:$uid})
                WHERE s.delete_at IS NULL AND s.emotion_type IS NOT NULL
                  AND s.created_at IS NOT NULL AND datetime(s.created_at) >= datetime($start)
                RETURN s.emotion_type AS emotion_type, s.emotion_intensity AS emotion_intensity,
                       toString(s.created_at) AS created_at
                ORDER BY datetime(s.created_at) ASC
                """,
                uid=user_id,
                start=(datetime.now(UTC) - timedelta(days=days)).isoformat(),
            )
            if rows:
                time_range = f"{days}d"
                break
        for row in rows:
            row["emotion_type"] = normalize_emotion_type_or_raw(row["emotion_type"])
        counts = {}
        if not rows:
            counts = {
                row["type"]: int(row["count"])
                for row in self.neo4j.execute_query(
                    """
                MATCH (s:Statement {end_user_id:$uid})
                WHERE s.delete_at IS NULL AND s.emotion_type IS NOT NULL
                RETURN s.emotion_type AS type, count(*) AS count
                """,
                    uid=user_id,
                )
            }
        entities = self.neo4j.execute_query(
            """
            MATCH (e:ExtractedEntity {end_user_id:$uid}) WHERE e.delete_at IS NULL
            RETURN e.name AS name, e.entity_type AS type ORDER BY e.created_at DESC LIMIT 20
            """,
            uid=user_id,
        )
        return {
            "emotions": rows,
            "time_range": time_range,
            "all_counts": counts,
            "user_profile": {
                "interests": [
                    row["name"] for row in entities if row["type"] in ("INTEREST", "HOBBY")
                ][:5]
                or ["未知"]
            },
        }
