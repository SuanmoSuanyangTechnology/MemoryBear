"""SceneCommunity page queries and counts, with ES-first Neo4j fallback."""

from elasticsearch import NotFoundError

from app.core.config import settings
from app.core.memory.storage.enums import BackendType, MemoryNodeType
from app.core.memory.storage.provider.elasticsearch.index import get_index_name
from app.core.memory.storage.provider.elasticsearch.client import ElasticClient
from app.core.memory.storage.provider.neo4j.client import Neo4jClient

COMMUNITY_FIELDS = ("id", "topic_name", "topic_scope", "summary", "category_l1",
                    "member_count", "created_at", "updated_at")
SCENE_FIELDS = ("id", "topic_scope", "content", "scene_community_id",
                "ended_at", "started_at", "created_at")


COMMUNITY_OVERVIEW_QUERY = """
MATCH (c:SceneCommunity {end_user_id: $uid})
WHERE c.delete_at IS NULL
WITH collect(c) AS communities
CALL {
    WITH communities
    UNWIND communities AS c
    WITH c
    WHERE ($category IS NULL OR c.category_l1 = $category)
      AND ($keyword = ''
           OR toLower(coalesce(c.topic_name, '')) CONTAINS $keyword
           OR toLower(coalesce(c.topic_scope, '')) CONTAINS $keyword
           OR toLower(coalesce(c.summary, '')) CONTAINS $keyword
           OR EXISTS {
               MATCH (s:SceneSummary {end_user_id: $uid})
               WHERE s.scene_community_id = c.id AND s.delete_at IS NULL
                 AND toLower(coalesce(s.content, '')) CONTAINS $keyword
           })
    WITH c, coalesce(c.member_count, 0) AS scene_count
    ORDER BY
        CASE WHEN $sort = 'scene_count_desc' THEN scene_count END DESC,
        CASE WHEN $sort IN ['scene_count_desc', 'updated_at_desc']
             THEN c.updated_at IS NULL END ASC,
        CASE WHEN $sort IN ['scene_count_desc', 'updated_at_desc'] THEN c.updated_at END DESC,
        CASE WHEN $sort = 'created_at_desc' THEN c.created_at IS NULL END ASC,
        CASE WHEN $sort = 'created_at_desc' THEN c.created_at END DESC,
        c.id ASC
    RETURN collect({community: properties(c), scene_count: scene_count}) AS matched
}
RETURN size(communities) AS total, size(matched) AS filtered_total,
       matched[$skip..($skip + $limit)] AS items
"""

COMMUNITY_DETAILS_QUERY = """
MATCH (c:SceneCommunity {end_user_id: $uid, id: $id})
WHERE c.delete_at IS NULL
CALL {
    WITH c
    MATCH (s:SceneSummary {end_user_id: $uid})
    WHERE s.scene_community_id = c.id AND s.delete_at IS NULL
    RETURN count(s) AS scene_count
}
CALL {
    WITH c
    MATCH (s:SceneSummary {end_user_id: $uid})
    WHERE s.scene_community_id = c.id AND s.delete_at IS NULL
    WITH s, coalesce(s.ended_at, s.started_at, s.created_at) AS scene_time
    ORDER BY scene_time IS NULL ASC, scene_time DESC, s.id ASC
    SKIP $skip LIMIT $limit
    RETURN collect(properties(s)) AS items
}
RETURN properties(c) AS community, items, scene_count
"""

COMMUNITY_COUNT_QUERY = """
MATCH (c:SceneCommunity)
WHERE c.end_user_id IN $ids AND c.delete_at IS NULL
RETURN count(c) AS total
"""


SCENE_SORT = [
    {"_script": {
        "type": "number",
        "order": "desc",
        "script": {
            "lang": "painless",
            "source": """
for (def field : params.fields) {
    if (doc.containsKey(field) && doc[field].size() > 0) {
        return doc[field].value.toInstant().toEpochMilli();
    }
}
return Long.MIN_VALUE;
""",
            "params": {"fields": ["ended_at", "started_at", "created_at"]},
        },
    }},
    {"id": "asc"},
]


class SceneCommunityNotFoundError(ValueError):
    """The requested community is absent from the authorized user's data."""


class SceneCommunityStorage:
    def __init__(self, elastic_client: ElasticClient | None, neo4j_client: Neo4jClient):
        self._elastic_client = elastic_client
        self._neo4j_client = neo4j_client

    async def count(self, user_ids):
        return await count_scene_communities(
            self._elastic_client._require_client() if self._elastic_client is not None else None,
            self._neo4j_client, user_ids,
        )

    @staticmethod
    def _ensure_complete_response(result):
        if result.get("timed_out") or result.get("_shards", {}).get("failed", 0):
            raise RuntimeError("Incomplete Elasticsearch response")

    @staticmethod
    def _user_query(uid):
        return {"bool": {"filter": [{"term": {"end_user_id": uid}}],
                         "must_not": [{"exists": {"field": "delete_at"}}]}}

    async def overview(self, uid, category, keyword, sort, page, pagesize):
        if BackendType(settings.MEMORY_READ_BACKEND) == BackendType.NEO4J:
            return await self._neo_overview(uid, category, keyword, sort, page, pagesize)
        try:
            result = await self._es_overview(self._elastic_client._require_client(), uid, category, keyword, sort, page, pagesize)
        except NotFoundError:
            result = None
        if result is not None:
            return result
        return await self._neo_overview(uid, category, keyword, sort, page, pagesize)

    async def _es_overview(self, client, uid, category, keyword, sort, page, pagesize):
        """Filter, sort and read the community page with its totals."""
        query = self._user_query(uid)
        if category:
            query["bool"]["filter"].append({"term": {"category_l1": category}})
        if keyword:
            query["bool"]["filter"].append(await self._keyword_filter(client, uid, keyword))
        sorting = []
        if sort == "scene_count_desc":
            sorting.append({"member_count": {"order": "desc", "missing": 0}})
        time_field = "created_at" if sort == "created_at_desc" else "updated_at"
        sorting.extend([{time_field: {"order": "desc", "missing": "_last"}}, {"id": "asc"}])
        response = await client.search(
            index=get_index_name(MemoryNodeType.SCENE_COMMUNITY),
            query=query,
            from_=(page - 1) * pagesize,
            size=pagesize,
            sort=sorting,
            track_total_hits=True,
            source_includes=list(COMMUNITY_FIELDS),
            aggregations={
                "all_communities": {
                    "global": {},
                    "aggs": {"user": {"filter": self._user_query(uid)}},
                },
            },
        )
        self._ensure_complete_response(response)
        total = response["aggregations"]["all_communities"]["user"]["doc_count"]
        filtered = response["hits"]["total"]["value"]
        if not filtered:
            return None
        items = [dict(hit["_source"], scene_count=hit["_source"].get("member_count") or 0)
                 for hit in response["hits"]["hits"]]
        return dict(total=total, items=items,
                    page=dict(page=page, pagesize=pagesize, total=filtered,
                              hasnext=page * pagesize < filtered))


    async def _keyword_filter(self, client, uid, keyword):
        """Combine community text matches with parent IDs of matching scenes."""
        escaped = keyword.replace("\\", "\\\\").replace("*", "\\*").replace("?", "\\?")
        def wildcard(field):
            return {"wildcard": {field + ".literal": {
                "value": "*" + escaped + "*", "case_insensitive": True}}}
        scene_query = self._user_query(uid)
        scene_query["bool"]["filter"].append(wildcard("content"))
        parent_ids = []
        composite = {
            "size": 1000,
            "sources": [{"community": {"terms": {"field": "scene_community_id"}}}],
        }
        while True:
            result = await client.search(
                index=get_index_name(MemoryNodeType.SCENE_SUMMARY),
                query=scene_query, size=0, track_total_hits=False,
                aggregations={"parents": {"composite": composite}},
            )
            self._ensure_complete_response(result)
            parents = result["aggregations"]["parents"]
            parent_ids.extend(
                bucket["key"]["community"] for bucket in parents["buckets"]
                if bucket["key"].get("community")
            )
            after = parents.get("after_key")
            if not parents["buckets"] or not after:
                break
            composite["after"] = after
        should = [wildcard(field) for field in ("topic_name", "topic_scope", "summary")]
        for offset in range(0, len(parent_ids), 65536):
            should.append({"terms": {"id": parent_ids[offset:offset + 65536]}})
        return {"bool": {"should": should, "minimum_should_match": 1}}

    @staticmethod
    def _details_result(community, items, total, page, pagesize):
        return dict(
            community, items=items, scene_count=total,
            page=dict(page=page, pagesize=pagesize, total=total,
                      hasnext=page * pagesize < total),
        )

    async def details(self, uid, community_id, page=1, pagesize=20):
        if BackendType(settings.MEMORY_READ_BACKEND) == BackendType.NEO4J:
            return await self._neo_details(uid, community_id, page, pagesize)
        client = self._elastic_client._require_client()
        try:
            query = self._user_query(uid)
            query["bool"]["filter"].append({"term": {"id": community_id}})
            community_result = await client.search(
                index=get_index_name(MemoryNodeType.SCENE_COMMUNITY),
                query=query, size=1, source_includes=list(COMMUNITY_FIELDS),
            )
            self._ensure_complete_response(community_result)
            communities = community_result["hits"]["hits"]
            if communities:
                community = communities[0]["_source"]
                query = self._user_query(uid)
                query["bool"]["filter"].append({"term": {"scene_community_id": community_id}})
                options = {
                    "index": get_index_name(MemoryNodeType.SCENE_SUMMARY),
                    "query": query,
                    "sort": SCENE_SORT,
                    "track_total_hits": True,
                    "from_": (page - 1) * pagesize,
                    "size": pagesize,
                    "source_includes": list(SCENE_FIELDS),
                }
                result = await client.search(**options)
                self._ensure_complete_response(result)
                total = result["hits"]["total"]["value"]
                items = [hit["_source"] for hit in result["hits"]["hits"]]
                return self._details_result(community, items, total, page, pagesize)
        except NotFoundError:
            pass
        return await self._neo_details(uid, community_id, page, pagesize)

    async def _neo_details(self, uid, community_id, page=1, pagesize=20):
        rows = await self._neo4j_client.execute_query(
            COMMUNITY_DETAILS_QUERY, uid=uid, id=community_id,
            skip=(page - 1) * pagesize, limit=pagesize,
        )
        if not rows:
            raise SceneCommunityNotFoundError("事项不存在")
        row = rows[0]
        return self._details_result(row["community"], row["items"], row["scene_count"], page, pagesize)

    async def _neo_overview(self, uid, category, keyword, sort, page, pagesize):
        rows = await self._neo4j_client.execute_query(
            COMMUNITY_OVERVIEW_QUERY, uid=uid, category=category,
            keyword=keyword.lower(), sort=sort,
            skip=(page - 1) * pagesize, limit=pagesize,
        )
        row = rows[0]
        items = [dict(item["community"], scene_count=item["scene_count"])
                 for item in row["items"]]
        return dict(total=row["total"], items=items,
                    page=dict(page=page, pagesize=pagesize, total=row["filtered_total"],
                              hasnext=page * pagesize < row["filtered_total"]))


async def count_scene_communities(client, neo4j, user_ids):
    if not user_ids:
        return 0
    if BackendType(settings.MEMORY_READ_BACKEND) == BackendType.NEO4J:
        rows = await neo4j.execute_query(COMMUNITY_COUNT_QUERY, ids=user_ids)
        return rows[0]["total"] if rows else 0
    counts = {}
    after = None
    while True:
        composite = {"size": 1000, "sources": [{"user": {"terms": {"field": "end_user_id"}}}]}
        if after:
            composite["after"] = after
        response = await client.search(index=get_index_name(MemoryNodeType.SCENE_COMMUNITY),
            size=0, query={"bool": {"filter": [{"terms": {"end_user_id": user_ids}}],
                                  "must_not": [{"exists": {"field": "delete_at"}}]}},
            aggregations={"users": {"composite": composite}})
        if response.get("timed_out") or response.get("_shards", {}).get("failed", 0):
            raise RuntimeError("Incomplete ES community count")
        aggregation = response["aggregations"]["users"]
        counts.update({bucket["key"]["user"]: bucket["doc_count"]
                       for bucket in aggregation["buckets"]})
        after = aggregation.get("after_key")
        if not after or not aggregation["buckets"]:
            break
    missing = [uid for uid in user_ids if uid not in counts]
    fallback = 0
    if missing:
        rows = await neo4j.execute_query(COMMUNITY_COUNT_QUERY, ids=missing)
        fallback = rows[0]["total"] if rows else 0
    return sum(counts.values()) + fallback


