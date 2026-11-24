"""Relation search: the graph agent plus its pure result builders.

Ported from ``Neo4jSearchService``'s relation-agent half in
``api/app/core/memory/read_services/search_engine/content_search.py`` and its
tool factories. The agent loop and tool set are unchanged; only the storage
access (through :class:`RetrievalQueries`) and the output model (domain
``RelationItem``) differ.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Sequence
from typing import Any

import json_repair
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.core.adapters.retrieval.constants import (
    RELATIONSHIP_LOOP_LIMIT,
    TRIPLET_PREDICATES,
)
from src.core.adapters.retrieval.queries import RetrievalQueries
from src.core.domain.errors import (
    MemoryRetrievalBusinessError,
    RetrievalImpact,
    RetrievalModelType,
    RetrievalStage,
)
from src.core.domain.models import EntityPair, RelationItem, RelationPairs
from src.core.domain.ports import ChatClient, PromptRenderer

logger = logging.getLogger(__name__)

AgentMessage = SystemMessage | HumanMessage | AIMessage | ToolMessage


class EntitySearchInput(BaseModel):
    name: str = Field(description="Entity name to search for, supports partial matching")


class RelationSearchInput(BaseModel):
    source_id: str | None = Field(
        default=None, description="Starting node ID for relation node query"
    )
    relation_predicates: list[int] = Field(
        description=(
            "Relational predicate IDs to retrieve, supports searching multiple relations at "
            "once. Must use numeric IDs: 1=别名属于, 2=属于类型, 3=位于, 4=前往, 5=组成部分, "
            "6=拥有, 7=使用, 8=创建了, 9=了解, 10=偏好, 11=负责, 12=沟通于, 13=关联于"
        )
    )


class UserSourceLookupInput(BaseModel):
    entity_ids: list[str] = Field(
        description=(
            "List of ExtractedEntity IDs to trace back to their original user messages "
            "(UserSource nodes)"
        )
    )


def _report(
    on_error: Callable[[MemoryRetrievalBusinessError], None] | None,
    error: MemoryRetrievalBusinessError,
) -> None:
    if on_error is None:
        return
    try:
        on_error(error)
    except Exception:  # noqa: BLE001 - diagnostics must not break retrieval
        logger.warning("[Retrieval] on_error callback failed, ignored", exc_info=True)


def parse_relation_pairs(message: AIMessage | str) -> RelationPairs:
    """Structured output with the host's repaired-JSON fallback."""

    text = message.text if isinstance(message, AIMessage) else str(message)
    fixed = json_repair.repair_json(text, return_objects=True)
    return RelationPairs.model_validate(fixed)


def extract_pairs_from_messages(messages: Sequence[AgentMessage]) -> RelationPairs:
    """Fallback: derive pairs from successful relation tool results."""

    tool_results: dict[str, list[dict]] = {}
    for message in messages:
        if not isinstance(message, ToolMessage) or not message.tool_call_id:
            continue
        try:
            parsed = json.loads(message.content)
        except (TypeError, ValueError):
            parsed = []
        tool_results[message.tool_call_id] = parsed if isinstance(parsed, list) else []

    pairs: list[EntityPair] = []
    seen: set[tuple[str, str]] = set()
    for message in messages:
        if not isinstance(message, AIMessage) or not message.tool_calls:
            continue
        for tool_call in message.tool_calls:
            if tool_call["name"] != "relation_search_tool":
                continue
            source_id = str(tool_call.get("args", {}).get("source_id") or "__user__")
            for item in tool_results.get(tool_call["id"], []):
                target_id = str(item.get("id", "")) if isinstance(item, dict) else ""
                if not target_id:
                    continue
                key = (source_id, target_id)
                if key not in seen:
                    seen.add(key)
                    pairs.append(EntityPair(source_id=source_id, target_id=target_id))
    return RelationPairs(pairs=pairs)


def build_relation_items(
    relation_records: Sequence[dict[str, Any]],
    entity_records: Sequence[dict[str, Any]],
) -> list[RelationItem]:
    """Map relationship rows to deduplicated domain relation items."""

    name_map = {
        record.get("id", ""): record.get("name", "") for record in entity_records
    }
    desc_map = {
        record.get("id", ""): record.get("description", "")
        for record in entity_records
    }
    relations: list[RelationItem] = []
    seen: set[tuple[str, str, str]] = set()
    for record in relation_records:
        source_id = str(record.get("source_id", ""))
        target_id = str(record.get("target_id", ""))
        relation = str(record.get("relation_predicate", ""))
        key = (source_id, relation, target_id)
        if key in seen:
            continue
        seen.add(key)
        relations.append(
            RelationItem(
                source=name_map.get(source_id, record.get("source_name", "")),
                relation=relation,
                target=name_map.get(target_id, record.get("target_name", "")),
                target_desc=desc_map.get(target_id, ""),
                target_id=target_id,
            )
        )
    return relations


def append_user_source_context(
    relations: Sequence[RelationItem],
    user_source_map: dict[str, str],
) -> None:
    """Append the original user message behind an entity, when it was looked up."""

    for relation in relations:
        source_text = user_source_map.get(relation.target_id)
        if source_text:
            relation.target_desc = (
                f"{relation.target_desc}\n"
                f"<original-context>{source_text}</original-context>"
            )


class RelationAgent:
    """Run the relation-search tool-calling loop for one query."""

    def __init__(
        self,
        *,
        queries: RetrievalQueries,
        prompts: PromptRenderer,
        end_user_id: str,
    ) -> None:
        self._queries = queries
        self._prompts = prompts
        self._end_user_id = end_user_id
        self._user_source_looked_up_ids: set[str] = set()
        self._tools = self._build_tools()

    # ── tool factories (host ``search_engine/tools.py``) ────────

    def _build_tools(self) -> list[Any]:
        queries = self._queries
        end_user_id = self._end_user_id

        @tool(args_schema=EntitySearchInput)
        async def entity_search_tool(name: str) -> list[dict]:
            """Search for entities by name in the knowledge graph.

            Use this to look up entity IDs by name before using them as source_id
            in relation_search_tool.

            Args:
                name: Entity name or partial name to search for.

            Returns:
                [{"id": "entity id", "name": "entity name", "entity_type": ...}, ...]
            """

            return await queries.search_entities_by_name(end_user_id, name)

        @tool(args_schema=RelationSearchInput)
        async def relation_search_tool(
            relation_predicates: list[int], source_id: str | None = None
        ) -> list[dict]:
            """Query the knowledge graph for entities connected to a source node.

            Omit source_id to start from the user's own entity node.

            Args:
                source_id: Starting entity node ID, defaults to the user entity.
                relation_predicates: Numeric predicate IDs to query.

            Returns:
                [{"id": ..., "source_name": ..., "relation_predicate": ...,
                "target_name": ...}, ...]
            """

            if not relation_predicates:
                return []
            resolved_source = source_id
            if not resolved_source:
                resolved_source = await queries.user_entity_id(end_user_id)
                if resolved_source is None:
                    return []
            records = await queries.search_related_entities(
                end_user_id, resolved_source, relation_predicates
            )
            logger.info(
                "[relation_search_tool] source:%s, predicates:%s, res:%s",
                resolved_source,
                relation_predicates,
                records,
            )
            return records

        @tool(args_schema=UserSourceLookupInput)
        async def user_source_lookup_tool(entity_ids: list[str]) -> list[dict]:
            """Trace ExtractedEntity nodes back to their original user messages.

            Args:
                entity_ids: List of ExtractedEntity IDs to trace back.

            Returns:
                [{"entity_id": ..., "original_text": ...}, ...]
            """

            if not entity_ids:
                return []
            result = await queries.user_sources_for_entities(end_user_id, entity_ids)
            logger.info(
                "[user_source_lookup_tool] entity_ids:%s, found:%s",
                entity_ids,
                len(result),
            )
            return result

        # Order matters for the prompt: relation, entity, user-source lookup.
        return [relation_search_tool, entity_search_tool, user_source_lookup_tool]

    # ── agent loop (host ``Neo4jSearchService._run_relation_agent``) ─

    async def run(
        self,
        query: str,
        llm: ChatClient,
        on_error: Callable[[MemoryRetrievalBusinessError], None] | None,
    ) -> list[RelationItem]:
        """Resolve relations for ``query``; never raises for a degraded branch."""

        pairs = await self._run_pairs(query, llm, on_error)
        if not pairs.pairs:
            return []
        relation_records, entity_records = await self._fetch_relation_data(pairs.pairs)
        relations = build_relation_items(relation_records, entity_records)
        if self._user_source_looked_up_ids:
            try:
                user_source_map = await self._queries.user_sources_map(
                    self._end_user_id, self._user_source_looked_up_ids
                )
            except Exception as exc:  # noqa: BLE001 - enrichment is best effort
                logger.warning("[RelationSearch] UserSource lookup failed: %s", exc)
                user_source_map = {}
            append_user_source_context(relations, user_source_map)
        logger.info(
            "[RelationSearch] resolved %s relations from %s pairs",
            len(relations),
            len(pairs.pairs),
        )
        return relations

    async def _run_pairs(
        self,
        query: str,
        llm: ChatClient,
        on_error: Callable[[MemoryRetrievalBusinessError], None] | None,
    ) -> RelationPairs:
        system_prompt = self._prompts.render(
            "relation_search",
            predicates=[dict(item) for item in TRIPLET_PREDICATES],
            loop_limit=RELATIONSHIP_LOOP_LIMIT - 1,
        )
        tool_map = {tool_item.name: tool_item for tool_item in self._tools}
        llm_with_tools = llm.bind_tools(self._tools)
        messages: list[AgentMessage] = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"<user-query>{query}</user-query>"),
        ]
        for _ in range(RELATIONSHIP_LOOP_LIMIT):
            try:
                response = await llm_with_tools.ainvoke(
                    messages, config={"callbacks": []}
                )
            except Exception as exc:
                _report(
                    on_error,
                    MemoryRetrievalBusinessError.model_call_failed(
                        RetrievalStage.RELATION_SEARCH,
                        exc,
                        model_type=RetrievalModelType.LLM,
                    ),
                )
                raise
            messages.append(response)
            if not response.tool_calls:
                break
            tool_messages = await asyncio.gather(
                *(self._run_tool(tool_map, tool_call) for tool_call in response.tool_calls)
            )
            messages.extend(tool_messages)

        self._collect_user_source_lookup_ids(messages)
        final_message = next(
            (message for message in reversed(messages) if isinstance(message, AIMessage)),
            messages[-1],
        )
        try:
            return parse_relation_pairs(final_message)
        except Exception as exc:
            logger.debug(
                "[RelationSearch] structured parse failed, extracting pairs from tools",
                exc_info=True,
            )
            _report(
                on_error,
                MemoryRetrievalBusinessError.structured_result_parse_failed(
                    RetrievalStage.RELATION_SEARCH,
                    exc,
                    model_type=RetrievalModelType.LLM,
                    impact=RetrievalImpact.INCOMPLETE,
                ),
            )
            return extract_pairs_from_messages(messages)

    @staticmethod
    async def _run_tool(tool_map: dict[str, Any], tool_call: dict[str, Any]) -> ToolMessage:
        try:
            tool_item = tool_map[tool_call["name"]]
            result = await tool_item.ainvoke(tool_call["args"])
            return ToolMessage(
                content=json.dumps(result, ensure_ascii=False),
                tool_call_id=tool_call["id"],
            )
        except Exception as exc:  # noqa: BLE001 - surface as tool error content
            return ToolMessage(
                content=json.dumps({"error": str(exc)}),
                tool_call_id=tool_call.get("id", ""),
            )

    def _collect_user_source_lookup_ids(self, messages: Sequence[AgentMessage]) -> None:
        looked_up: set[str] = set()
        for message in messages:
            if not isinstance(message, AIMessage) or not message.tool_calls:
                continue
            for tool_call in message.tool_calls:
                if tool_call.get("name") != "user_source_lookup_tool":
                    continue
                entity_ids = tool_call.get("args", {}).get("entity_ids", [])
                if isinstance(entity_ids, list):
                    looked_up.update(str(entity_id) for entity_id in entity_ids)
        self._user_source_looked_up_ids = looked_up

    async def _fetch_relation_data(
        self, pairs: Sequence[EntityPair]
    ) -> tuple[list[dict], list[dict]]:
        user_entity_id = await self._queries.user_entity_id(self._end_user_id) or ""
        batch_pairs = [
            {
                "source_id": (
                    user_entity_id if pair.source_id == "__user__" else pair.source_id
                ),
                "target_id": pair.target_id,
            }
            for pair in pairs
        ]
        relation_records = await self._queries.entity_pair_relations(
            self._end_user_id, batch_pairs
        )
        entity_ids = {user_entity_id}
        for record in relation_records:
            entity_ids.add(str(record.get("source_id", "")))
            entity_ids.add(str(record.get("target_id", "")))
        for pair in batch_pairs:
            entity_ids.add(str(pair["source_id"]))
            entity_ids.add(str(pair["target_id"]))
        entity_ids.discard("")
        entity_records = await self._queries.active_entities_by_ids(
            self._end_user_id, list(entity_ids)
        )
        return relation_records, entity_records


__all__ = [
    "EntitySearchInput",
    "RelationAgent",
    "RelationSearchInput",
    "UserSourceLookupInput",
    "append_user_source_context",
    "build_relation_items",
    "extract_pairs_from_messages",
    "parse_relation_pairs",
]