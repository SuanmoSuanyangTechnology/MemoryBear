"""Fail-closed stand-in for the monolith long-term memory entry point.

Long-term memory reads, writes and ingestion stay served by the monolith
memory API; the runtime delegation is not wired into this service yet. Every
operation that would touch memory state raises a clear ``BusinessException``
instead of silently degrading, so memory-dependent runs fail visibly rather
than losing memory without a trace.

``create_long_term_memory_tool`` keeps the host contract for disabled configs
(returns ``None`` when memory is off or no end user is given); it only raises
once an enabled configuration actually needs the tool body.
"""

from typing import Any, NoReturn

from ..errors import BizCode, BusinessException

_UNAVAILABLE_MESSAGE = (
    "Long-term memory is served by the memory API and is not wired into this "
    "service yet; disable memory for this run or use the monolith endpoint"
)


class MemoryService:
    """Monolith-compatible facade whose operations are fail-closed here."""

    @classmethod
    async def create(
            cls,
            config_id,
            end_user_id: str,
            workspace_id: str | None = None,
            storage_type: str = "neo4j",
            user_rag_memory_id: str | None = None,
            conversation_id: str | None = None,
            language: str = "zh",
            draft: bool = False,
    ) -> NoReturn:
        raise BusinessException(_UNAVAILABLE_MESSAGE, code=BizCode.INVALID_PARAMETER)

    @staticmethod
    async def ingest_workflow_messages(
        messages: list,
        conversation_id: str,
        end_user_id: str,
        config_id: str,
        workspace_id: str,
        language: str = "zh",
        storage_type: str = "neo4j",
        user_rag_memory_id: str = "",
    ) -> NoReturn:
        raise BusinessException(_UNAVAILABLE_MESSAGE, code=BizCode.INVALID_PARAMETER)

    async def read(
            self,
            query: str,
            search_switch,
            history: list | None = None,
            limit: int = 10,
            includes: list | None = None,
            skip_summary: bool = False,
            enable_rerank: bool = False,
            record_display: bool = False,
    ) -> NoReturn:
        raise BusinessException(_UNAVAILABLE_MESSAGE, code=BizCode.INVALID_PARAMETER)


def create_long_term_memory_tool(
        memory_config: dict[str, Any],
        end_user_id: str,
        workspace_id,
        storage_type: str | None = None,
        user_rag_memory_id: str | None = None,
        memory_name: str | None = None,
        config_id=None,
        db=None,
) -> Any | None:
    """Host contract keeper: disabled memory yields no tool, enabled raises."""
    if not memory_config or not memory_config.get("enabled") or not end_user_id:
        return None
    raise BusinessException(_UNAVAILABLE_MESSAGE, code=BizCode.INVALID_PARAMETER)
