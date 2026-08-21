"""Asynchronous graph retrieval bridge for the two legacy graph pipelines."""

from __future__ import annotations

import json
from typing import Any

from ..knowledge_graph.config import GraphPipeline
from ..knowledge_graph.elasticsearch_store import GraphElasticsearchStore
from ..models.chunk import DocumentChunk
from .models import GraphRetrievalSnapshot


class GraphRetrievalBridge:
    @staticmethod
    async def retrieve(
        client: Any,
        snapshot: GraphRetrievalSnapshot,
        *,
        top_k: int,
    ) -> tuple[list[DocumentChunk], list[dict[str, Any]], list[dict[str, Any]]]:
        chunks: list[DocumentChunk] = []
        entities: list[dict[str, Any]] = []
        relationships: list[dict[str, Any]] = []
        store = GraphElasticsearchStore(client)
        for target in snapshot.targets:
            if snapshot.pipeline is GraphPipeline.LEGACY:
                data = await store.load_legacy_graph(
                    target.graph_index_name,
                    str(target.knowledge_id),
                )
                graph = data.get("graph") or {}
                if graph:
                    chunks.append(
                        DocumentChunk(
                            page_content=json.dumps(graph, ensure_ascii=False),
                            metadata={
                                "doc_id": f"graph:{target.knowledge_id}",
                                "knowledge_id": str(target.knowledge_id),
                                "chunk_type": "graph",
                                "retrieval_source": "graph",
                                "score": 1.0,
                            },
                        )
                    )
                continue
            result = await client.search(
                index=target.graph_index_name,
                size=max(top_k * 4, 20),
                query={
                    "bool": {
                        "must": {
                            "multi_match": {
                                "query": snapshot.query,
                                "fields": [
                                    "entity_name_kwd^3",
                                    "description^2",
                                    "predicate_kwd",
                                    "keywords_kwd",
                                ],
                            }
                        },
                        "filter": [
                            {"term": {"kb_id": str(target.knowledge_id)}},
                            {
                                "terms": {
                                    "knowledge_graph_kwd": [
                                        "entity_projection",
                                        "relation_projection",
                                    ]
                                }
                            },
                        ],
                    }
                },
                allow_partial_search_results=False,
            )
            max_score = float((result.get("hits") or {}).get("max_score") or 1)
            for hit in (result.get("hits") or {}).get("hits", []):
                source = hit.get("_source") or {}
                score = float(hit.get("_score") or 0) / max_score
                if source.get("knowledge_graph_kwd") == "entity_projection":
                    key = str(source.get("entity_key_kwd") or "")
                    if key:
                        entities.append(
                            {
                                "entity_key": key,
                                "entity_name": source.get("entity_name_kwd") or key,
                                "description": source.get("description") or "",
                                "source_chunk_ids": list(source.get("source_chunk_ids_kwd") or []),
                                "score": score,
                            }
                        )
                else:
                    key = str(source.get("relation_key_kwd") or "")
                    if key:
                        relationships.append(
                            {
                                "relation_key": key,
                                "from_entity_key": source.get("from_entity_key_kwd") or "",
                                "to_entity_key": source.get("to_entity_key_kwd") or "",
                                "predicate": source.get("predicate_kwd") or "",
                                "description": source.get("description") or "",
                                "source_chunk_ids": list(source.get("source_chunk_ids_kwd") or []),
                                "score": score,
                            }
                        )
        entities.sort(key=lambda item: item["score"], reverse=True)
        relationships.sort(key=lambda item: item["score"], reverse=True)
        return chunks, entities[:top_k], relationships[:top_k]


__all__ = ["GraphRetrievalBridge"]
