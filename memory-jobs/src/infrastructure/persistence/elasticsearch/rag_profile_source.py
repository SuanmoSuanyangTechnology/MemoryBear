"""RAG source messages from the existing synchronous ES client."""


class ElasticsearchRAGProfileSource:
    def __init__(self, elasticsearch) -> None:
        self.elasticsearch = elasticsearch

    def rag_messages(self, user_id: str, limit: int) -> list[str]:
        result = self.elasticsearch.client().search(
            index="rag_memory_current",
            size=limit,
            query={
                "bool": {
                    "filter": [{"term": {"end_user_id": user_id}}],
                    "must_not": [{"exists": {"field": "delete_at"}}],
                }
            },
            sort=[
                {"dialog_at": {"order": "desc", "missing": "_last"}},
                {"role_rank": {"order": "desc", "missing": "_last"}},
                {"id": "desc"},
            ],
            source_includes=["content"],
            allow_partial_search_results=False,
        )
        if result.get("timed_out") or result.get("_shards", {}).get("failed"):
            raise RuntimeError("RAG evidence query incomplete")
        return [
            hit["_source"]["content"]
            for hit in reversed(result["hits"]["hits"])
            if str(hit.get("_source", {}).get("content") or "").strip()
        ]
