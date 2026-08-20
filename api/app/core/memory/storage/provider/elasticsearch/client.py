import math
from collections.abc import Mapping
from numbers import Real
from typing import Any, Callable

from elastic_transport import ObjectApiResponse
from elasticsearch import AsyncElasticsearch

from app.core.memory.storage.enums import BackendType, MemoryNodeLabel
from app.core.memory.storage.exceptions import UnsupportedQueryError
from app.core.memory.storage.models import (
    FilterCondition,
    FilterOperator,
    NodeFilter,
    NodeProjection,
    NodeSort,
    ProjectionField,
)
from app.core.memory.storage.provider.base import BaseClient
from app.core.memory.storage.provider.elasticsearch.compiler.filter_compiler import (
    compile_elasticsearch_filter,
)
from app.core.memory.storage.provider.elasticsearch.compiler.projection_compiler import (
    apply_elasticsearch_projection,
    compile_elasticsearch_projection,
)
from app.core.memory.storage.provider.elasticsearch.compiler.sort_compiler import (
    compile_elasticsearch_sort,
)
from app.core.memory.storage.provider.elasticsearch.config import (
    build_elasticsearch_client_config,
)
from app.core.memory.storage.provider.elasticsearch.index import (
    ensure_indices,
    get_index_name,
)
from app.core.memory.storage.provider.elasticsearch.index.definitions import (
    EMBEDDING_FIELDS,
    FULLTEXT_FIELDS,
)
from app.core.utils.datetime_utils import to_iso_z, utcnow

SEARCH_BATCH_SIZE = 1_000
PIT_KEEP_ALIVE = "1m"
MAX_SEARCH_LIMIT = 10_000
MIN_KNN_CANDIDATES = 100
KNN_CANDIDATE_MULTIPLIER = 10
VIRTUAL_SCORE_FIELD = "score"


def _raise_on_response_failures(
        result: ObjectApiResponse[Any],
        operation: str,
) -> None:
    if result.get("timed_out"):
        raise RuntimeError(f"Elasticsearch {operation} timed out")
    failures = result.get("failures") or []
    if failures:
        raise RuntimeError(f"Elasticsearch {operation} failures: {failures!r}")
    version_conflicts = int(result.get("version_conflicts", 0) or 0)
    if version_conflicts:
        raise RuntimeError(
            f"Elasticsearch {operation} had {version_conflicts} version conflicts"
        )
    shards = result.get("_shards") or {}
    if isinstance(shards, Mapping) and int(shards.get("failed", 0) or 0):
        raise RuntimeError(
            f"Elasticsearch {operation} shard failures: "
            f"{shards.get('failures', [])!r}"
        )


def _validate_search_limit(limit: int) -> None:
    if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= MAX_SEARCH_LIMIT
    ):
        raise ValueError(
            f"search limit must be an integer between 1 and {MAX_SEARCH_LIMIT}"
        )


def _normalize_query_vector(embed: list[Any]) -> list[float]:
    if not isinstance(embed, list) or not embed:
        raise ValueError("embedding query vector must be a non-empty list")

    vector: list[float] = []
    for value in embed:
        if (
                not isinstance(value, Real)
                or isinstance(value, bool)
                or not math.isfinite(float(value))
        ):
            raise ValueError("embedding query vector must contain finite numbers")
        vector.append(float(value))
    return vector


def _projection_requests_score(projection: NodeProjection | None) -> bool:
    if projection is None:
        return False
    return any(
        item == VIRTUAL_SCORE_FIELD
        if isinstance(item, str)
        else (
            isinstance(item, ProjectionField)
            and item.field == VIRTUAL_SCORE_FIELD
        )
        for item in projection.fields
    )


def _compile_search_source_options(
        projection: NodeProjection | None,
) -> tuple[dict[str, Any], bool]:
    source_fields = compile_elasticsearch_projection(
        projection,
        virtual_fields={VIRTUAL_SCORE_FIELD},
    )
    if source_fields is None:
        return {}, True
    if source_fields:
        return {"source_includes": source_fields}, True
    return {"source": False}, False


def _parse_search_hits(
        result: ObjectApiResponse[Any],
        projection: NodeProjection | None,
        operation: str,
        *,
        source_required: bool,
        score_transform: Callable[[float], float] | None = None,
) -> list[dict[str, Any]]:
    _raise_on_response_failures(result, operation)
    hits_container = result.get("hits")
    if not isinstance(hits_container, Mapping):
        raise RuntimeError(f"Elasticsearch {operation} returned invalid hits")
    hits = hits_container.get("hits")
    if not isinstance(hits, (list, tuple)):
        raise RuntimeError(f"Elasticsearch {operation} returned invalid hit list")

    include_score = _projection_requests_score(projection)
    nodes: list[dict[str, Any]] = []
    for index, hit in enumerate(hits):
        if not isinstance(hit, Mapping):
            raise RuntimeError(
                f"Elasticsearch {operation} returned invalid hit at index {index}"
            )

        source_value = hit.get("_source")
        if source_value is None and not source_required:
            source: Mapping[str, Any] = {}
        elif isinstance(source_value, Mapping):
            source = source_value
        else:
            raise RuntimeError(
                f"Elasticsearch {operation} returned invalid _source at index {index}"
            )

        virtual_fields: dict[str, Any] = {}
        if include_score:
            raw_score = hit.get("_score")
            if (
                    not isinstance(raw_score, Real)
                    or isinstance(raw_score, bool)
                    or not math.isfinite(float(raw_score))
            ):
                raise RuntimeError(
                    f"Elasticsearch {operation} returned invalid _score at index {index}"
                )
            score = float(raw_score)
            virtual_fields[VIRTUAL_SCORE_FIELD] = (
                score_transform(score) if score_transform is not None else score
            )

        nodes.append(
            apply_elasticsearch_projection(
                source,
                projection,
                virtual_fields=virtual_fields,
            )
        )
    return nodes


class ElasticClient(BaseClient):
    name = BackendType.ELASTIC

    def __init__(self) -> None:
        self.client: AsyncElasticsearch | None = None

    @classmethod
    async def create(cls) -> "ElasticClient":
        self = cls()
        self.client = await self.connect()
        try:
            await ensure_indices(self.client)
        except Exception:
            await self.close()
            raise
        return self

    def _require_client(self) -> AsyncElasticsearch:
        if self.client is None:
            raise RuntimeError("Elasticsearch client is not connected")
        return self.client

    async def health(self) -> bool:
        return await self._require_client().ping()

    async def connect(self) -> AsyncElasticsearch:
        return AsyncElasticsearch(**build_elasticsearch_client_config())

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()
            self.client = None

    async def save_node(self, label: MemoryNodeLabel, data: dict) -> None:
        node_id = self.verify_input(label, data)
        await self._require_client().index(
            index=get_index_name(label),
            id=str(node_id),
            document=data,
            refresh="wait_for",
        )

    async def update_node(
            self,
            label: MemoryNodeLabel,
            data: dict,
            node_filter: NodeFilter,
    ) -> list[dict[str, int]]:
        self.verify_label(label)
        result = await self._require_client().update_by_query(
            index=get_index_name(label),
            query=compile_elasticsearch_filter(node_filter),
            script={
                "lang": "painless",
                "source": "ctx._source.putAll(params.properties)",
                "params": {"properties": data},
            },
            conflicts="abort",
            refresh=True,
        )
        _raise_on_response_failures(result, "update_by_query")
        return [{"updated": int(result.get("updated", 0))}]

    async def delete_node(
            self,
            label: MemoryNodeLabel,
            node_filter: NodeFilter,
            draft: bool = False,
    ) -> list[dict[str, int]]:
        self.verify_label(label)
        client = self._require_client()
        index_name = get_index_name(label)

        if draft:
            active_filter = NodeFilter.all_of(
                node_filter,
                FilterCondition(
                    field="delete_at",
                    operator=FilterOperator.EXISTS,
                    value=False,
                ),
            )
            result = await client.update_by_query(
                index=index_name,
                query=compile_elasticsearch_filter(active_filter),
                script={
                    "lang": "painless",
                    "source": "ctx._source.delete_at = params.delete_at",
                    "params": {"delete_at": to_iso_z(utcnow())},
                },
                conflicts="abort",
                refresh=True,
            )
            _raise_on_response_failures(result, "draft delete update_by_query")
            deleted = result.get("updated", 0)
        else:
            result = await client.delete_by_query(
                index=index_name,
                query=compile_elasticsearch_filter(node_filter),
                conflicts="abort",
                refresh=True,
            )
            _raise_on_response_failures(result, "delete_by_query")
            deleted = result.get("deleted", 0)

        return [{"deleted": int(deleted)}]

    async def get_node(
            self,
            label: MemoryNodeLabel,
            node_filter: NodeFilter,
            projection: NodeProjection | None = None,
            node_sort: NodeSort | None = None,
    ) -> list[dict[str, Any]]:
        self.verify_label(label)
        source_includes = compile_elasticsearch_projection(projection)
        sort = [
            *compile_elasticsearch_sort(node_sort),
            {"_shard_doc": "asc"},
        ]
        search_options: dict[str, Any] = {
            "query": compile_elasticsearch_filter(node_filter),
            "size": SEARCH_BATCH_SIZE,
            "sort": sort,
        }
        if source_includes is not None:
            search_options["source_includes"] = source_includes

        client = self._require_client()
        pit_result = await client.open_point_in_time(
            index=get_index_name(label),
            keep_alive=PIT_KEEP_ALIVE,
            allow_partial_search_results=False,
        )
        pit_id = pit_result.get("id")
        if not isinstance(pit_id, str) or not pit_id:
            raise RuntimeError(
                "Elasticsearch open_point_in_time returned no PIT id"
            )

        nodes: list[dict[str, Any]] = []
        try:
            _raise_on_response_failures(pit_result, "open_point_in_time")
            while True:
                search_options["pit"] = {
                    "id": pit_id,
                    "keep_alive": PIT_KEEP_ALIVE,
                }
                result = await client.search(**search_options)
                next_pit_id = result.get("pit_id")
                if isinstance(next_pit_id, str) and next_pit_id:
                    pit_id = next_pit_id

                _raise_on_response_failures(result, "search")
                hits_container = result.get("hits") or {}
                hits = (
                    hits_container.get("hits", [])
                    if isinstance(hits_container, Mapping)
                    else []
                )
                nodes.extend(
                    apply_elasticsearch_projection(source, projection)
                    for hit in hits
                    if isinstance(hit, Mapping)
                    and isinstance((source := hit.get("_source")), Mapping)
                )
                if len(hits) < SEARCH_BATCH_SIZE:
                    break

                last_hit = hits[-1]
                search_after = (
                    last_hit.get("sort")
                    if isinstance(last_hit, Mapping)
                    else None
                )
                if not isinstance(search_after, (list, tuple)) or not search_after:
                    raise RuntimeError(
                        "Elasticsearch search returned a full page without sort values"
                    )
                search_options["search_after"] = list(search_after)
        finally:
            await client.close_point_in_time(id=pit_id)
        return nodes

    async def search_by_embedding(
            self,
            label: MemoryNodeLabel,
            node_filter: NodeFilter,
            embed: list,
            limit: int,
            projection: NodeProjection | None = None,
    ) -> list[dict[str, Any]]:
        self.verify_label(label)
        embedding_field = EMBEDDING_FIELDS.get(label)
        if embedding_field is None:
            raise UnsupportedQueryError(self.name, label, "embedding")
        _validate_search_limit(limit)
        query_vector = _normalize_query_vector(embed)
        vector_norm = math.hypot(*query_vector)
        if not math.isfinite(vector_norm):
            raise ValueError("embedding query vector norm must be finite")
        if vector_norm == 0:
            return []

        source_options, source_required = _compile_search_source_options(
            projection
        )
        num_candidates = min(
            MAX_SEARCH_LIMIT,
            max(
                MIN_KNN_CANDIDATES,
                limit,
                limit * KNN_CANDIDATE_MULTIPLIER,
            ),
        )
        result = await self._require_client().search(
            index=get_index_name(label),
            knn={
                "field": embedding_field,
                "query_vector": query_vector,
                "k": limit,
                "num_candidates": num_candidates,
                "filter": compile_elasticsearch_filter(node_filter),
            },
            size=limit,
            allow_partial_search_results=False,
            **source_options,
        )
        return _parse_search_hits(
            result,
            projection,
            "embedding search",
            source_required=source_required,
            score_transform=lambda score: (2.0 * score) - 1.0,
        )

    async def search_by_fulltext(
            self,
            label: MemoryNodeLabel,
            node_filter: NodeFilter,
            text: str,
            limit: int,
            projection: NodeProjection | None = None,
    ) -> list[dict[str, Any]]:
        self.verify_label(label)
        fulltext_fields = FULLTEXT_FIELDS.get(label)
        if fulltext_fields is None:
            raise UnsupportedQueryError(self.name, label, "fulltext")
        _validate_search_limit(limit)
        if not isinstance(text, str):
            raise ValueError("fulltext query must be a string")
        normalized_text = text.strip()
        if not normalized_text:
            return []

        source_options, source_required = _compile_search_source_options(
            projection
        )
        result = await self._require_client().search(
            index=get_index_name(label),
            query={
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": normalized_text,
                                "fields": list(fulltext_fields),
                            }
                        }
                    ],
                    "filter": [compile_elasticsearch_filter(node_filter)],
                }
            },
            size=limit,
            allow_partial_search_results=False,
            **source_options,
        )
        return _parse_search_hits(
            result,
            projection,
            "fulltext search",
            source_required=source_required,
        )
