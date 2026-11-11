"""Legacy short-term response assembly."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.repositories.short import ShortRepository

if TYPE_CHECKING:
    from src.runtime import ProfileRuntime

api_logger = logging.getLogger(__name__)


class ShortService:
    _ROLE_LABELS = {"user": "用户", "assistant": "助手", "system": "系统"}

    def __init__(self, runtime: ProfileRuntime):
        self.repository = ShortRepository(runtime)

    async def get_short_databasets(self, end_user_id: str) -> list[dict]:
        short_memories = await self.repository.latest(end_user_id)
        short_result = []
        for memory in short_memories:
            deep_expanded = {}
            messages = memory.messages
            aimessages = memory.aimessages
            retrieved_content = memory.retrieved_content or []

            api_logger.debug(f"Retrieved content: {retrieved_content}")

            retrieval_source = []
            for item in retrieved_content:
                if isinstance(item, dict):
                    for key, values in item.items():
                        retrieval_source.append(
                            {"query": key, "retrieval": values, "source": "上下文记忆"}
                        )

            deep_expanded["retrieval"] = retrieval_source
            deep_expanded["message"] = messages
            deep_expanded["answer"] = aimessages
            short_result.append(deep_expanded)
        return short_result

    async def get_long_databasets(self, end_user_id: str) -> list[dict]:
        groups = await self.repository.pending_messages(end_user_id)
        long_result = []
        for messages in groups:
            if not messages:
                continue

            # Build a single entry per conversation: grouped messages
            lines = []
            for msg in messages:
                role_label = self._ROLE_LABELS.get(msg.role, msg.role)
                lines.append(f"[{role_label}] {msg.content}")

            retrieval = "\n\n".join(lines)
            # Use the first user message content as the query/title
            first_user = next((m for m in messages if m.role == "user"), messages[0])
            preview = first_user.content[:80] + ("..." if len(first_user.content) > 80 else "")
            query = f"[会话] {preview}"

            long_result.append(
                {
                    "query": query,
                    "retrieval": retrieval,
                }
            )

        return long_result

    async def short_term(self, end_user_id: str) -> dict:
        short_result = await self.get_short_databasets(end_user_id)
        short_count = await self.repository.count(end_user_id)
        long_result = await self.get_long_databasets(end_user_id)
        entity_count = await self.repository.entity_count(end_user_id)
        return {
            "short_term": short_result,
            "long_term": long_result,
            "entity": entity_count,
            "retrieval_number": short_count,
            "long_term_number": len(long_result),
        }

    async def active_user(self, end_user_id, workspace_id):
        return await self.repository.active_user(end_user_id, workspace_id)
