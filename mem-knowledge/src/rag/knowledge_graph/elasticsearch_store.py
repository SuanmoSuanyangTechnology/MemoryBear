"""Read and clear graph documents using the legacy Elasticsearch shapes."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from elasticsearch import NotFoundError

ENTITY_PROJECTION = "entity_projection"
RELATION_PROJECTION = "relation_projection"


def graph_index_name(workspace_id: str) -> str:
    return f"graphrag_{workspace_id}"


class GraphElasticsearchStore:
    def __init__(self, client: Any):
        self.client = client

    @staticmethod
    def _query(knowledge_id: str, document_type: str) -> dict[str, Any]:
        return {
            "bool": {
                "filter": [
                    {"term": {"kb_id": knowledge_id}},
                    {"term": {"knowledge_graph_kwd": document_type}},
                ]
            }
        }

    async def load_projection_graph(
        self,
        index_name: str,
        knowledge_id: str,
        *,
        node_limit: int = 256,
        edge_limit: int = 128,
    ) -> dict[str, Any]:
        empty = {
            "directed": True,
            "multigraph": True,
            "graph": {"source_id": []},
            "nodes": [],
            "edges": [],
        }
        try:
            node_result, edge_result = await asyncio.gather(
                self.client.search(
                    index=index_name,
                    size=max(1, node_limit),
                    query=self._query(knowledge_id, ENTITY_PROJECTION),
                    sort=[{"pagerank_flt": {"order": "desc", "unmapped_type": "float"}}],
                    allow_partial_search_results=False,
                ),
                self.client.search(
                    index=index_name,
                    size=max(1, edge_limit * 4),
                    query=self._query(knowledge_id, RELATION_PROJECTION),
                    sort=[{"evidence_count_int": {"order": "desc", "unmapped_type": "long"}}],
                    allow_partial_search_results=False,
                ),
            )
        except NotFoundError:
            return empty

        nodes = []
        source_ids: set[str] = set()
        for hit in (node_result.get("hits") or {}).get("hits", []):
            source = hit.get("_source") or {}
            key = str(source.get("entity_key_kwd") or "")
            if not key:
                continue
            item_source_ids = sorted(
                str(value) for value in source.get("source_id") or [] if str(value).strip()
            )
            source_ids.update(item_source_ids)
            nodes.append(
                {
                    "id": key,
                    "entity_name": str(source.get("entity_name_kwd") or ""),
                    "entity_type": str(source.get("entity_type_kwd") or ""),
                    "description": str(source.get("description") or ""),
                    "pagerank": float(source.get("pagerank_flt") or 0),
                    "source_id": item_source_ids,
                    "aliases": list(source.get("aliases_kwd") or []),
                    "evidence_count": int(source.get("evidence_count_int") or 0),
                    "document_count": int(source.get("document_count_int") or 0),
                    "degree": int(source.get("degree_int") or 0),
                }
            )
            if len(nodes) >= node_limit:
                break
        node_ids = {node["id"] for node in nodes}
        edges = []
        for hit in (edge_result.get("hits") or {}).get("hits", []):
            source = hit.get("_source") or {}
            relation_key = str(source.get("relation_key_kwd") or "")
            from_key = str(source.get("from_entity_key_kwd") or "")
            to_key = str(source.get("to_entity_key_kwd") or "")
            if (
                not relation_key
                or not from_key
                or not to_key
                or from_key == to_key
                or from_key not in node_ids
                or to_key not in node_ids
            ):
                continue
            evidence_count = source.get("evidence_count_int")
            weight = source.get("weight_flt", evidence_count if evidence_count is not None else 1)
            edge_source_ids = sorted(
                str(value) for value in source.get("source_id") or [] if str(value).strip()
            )
            source_ids.update(edge_source_ids)
            predicate = str(source.get("predicate_kwd") or "")
            keywords = [str(value) for value in source.get("keywords_kwd") or []]
            edges.append(
                {
                    "id": relation_key,
                    "src_id": from_key,
                    "tgt_id": to_key,
                    "source": from_key,
                    "target": to_key,
                    "description": str(source.get("description") or ""),
                    "keywords": keywords or ([predicate] if predicate else []),
                    "weight": float(weight),
                    "source_id": edge_source_ids,
                    "directed": bool(source.get("directed_int")),
                    "document_count": int(source.get("document_count_int") or 0),
                }
            )
            if len(edges) >= edge_limit:
                break
        return {**empty, "graph": {"source_id": sorted(source_ids)}, "nodes": nodes, "edges": edges}

    async def load_legacy_graph(self, index_name: str, knowledge_id: str) -> dict[str, Any]:
        empty = {"graph": {}, "mind_map": {}}
        try:
            response = await self.client.search(
                index=index_name,
                size=1,
                query=self._query(knowledge_id, "graph"),
                allow_partial_search_results=False,
            )
        except NotFoundError:
            return empty
        hits = (response.get("hits") or {}).get("hits", [])
        if not hits:
            return empty
        source = hits[0].get("_source") or {}
        raw = source.get("page_content")
        try:
            graph = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except (TypeError, ValueError):
            return empty
        nodes = sorted(
            graph.get("nodes") or [], key=lambda item: item.get("pagerank", 0), reverse=True
        )[:256]
        node_ids = {item.get("id") for item in nodes}
        edges = [
            edge
            for edge in graph.get("edges") or []
            if edge.get("source") != edge.get("target")
            and edge.get("source") in node_ids
            and edge.get("target") in node_ids
        ]
        graph["nodes"] = nodes
        graph["edges"] = sorted(edges, key=lambda item: item.get("weight", 0), reverse=True)[:128]
        return {"graph": graph, "mind_map": {}}

    async def clear_legacy_graph(self, index_name: str, knowledge_id: str) -> None:
        try:
            await self.client.delete_by_query(
                index=index_name,
                conflicts="abort",
                ignore_unavailable=True,
                refresh=True,
                wait_for_completion=True,
                query={
                    "bool": {
                        "filter": [
                            {"term": {"kb_id": knowledge_id}},
                            {
                                "terms": {
                                    "knowledge_graph_kwd": [
                                        "graph",
                                        "subgraph",
                                        "entity",
                                        "relation",
                                    ]
                                }
                            },
                        ]
                    }
                },
            )
        except NotFoundError:
            return


__all__ = ["GraphElasticsearchStore", "graph_index_name"]
