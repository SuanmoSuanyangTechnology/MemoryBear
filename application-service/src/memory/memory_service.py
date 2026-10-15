"""Delegating facade for the monolith long-term memory entry point.

Long-term memory reads, writes and ingestion stay served by the monolith
memory API during the coexistence period; this facade forwards every
operation to the internal memory endpoints over the internal network (never
through the gateway) so memory state keeps exactly one writer. An
unconfigured channel fails closed with a clear ``BusinessException`` instead
of silently degrading, so memory-dependent runs fail visibly rather than
losing memory without a trace.

``create_long_term_memory_tool`` keeps the host contract for disabled configs
(returns ``None`` when memory is off or no end user is given), and the tool
name / description / argument schema / result strings mirror the monolith so
LLM behavior stays identical.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from ..errors import BizCode, BusinessException, as_biz_code
from ..infrastructure.upstream import UpstreamUnavailableError, get_monolith_upstream
from .enums import SearchStrategy

logger = logging.getLogger(__name__)

_UNAVAILABLE_MESSAGE = (
    "Long-term memory is served by the memory API and is not wired into this "
    "service yet; disable memory for this run or use the monolith endpoint"
)

_READ_PATH = "/api/internal/app-service/memory/read"
_INGEST_WORKFLOW_PATH = "/api/internal/app-service/memory/ingest-workflow"


class LongTermMemoryInput(BaseModel):
    """长期记忆工具输入参数"""

    question: str = Field(
        description="经过优化重写的查询问题。请将用户的原始问题重写为更合适的检索形式，包含关键词，上下文和具体描述，注意错词检查并且改写")
    search_mode: str = Field(
        default="express",
        description="检索模式（默认为 express）。"
                    "deep=深度检索：拆解复杂问题并做关系(图)检索，返回汇总答案，适合复杂/关系类问题；"
                    "normal=普通检索：拆解问题做混合检索，返回汇总答案，适合一般性问题；"
                    "quick=快速检索：单次混合检索直接返回原始数据(不拆解/不汇总)，需自行据此推断，适合简单查询或需要原始证据；"
                    "express=极速检索：单次关键词检索直接返回原始数据(不拆解/不汇总)，需自行据此推断，适合简单查询或需要原始证据；"
                    "meta=元数据检索：仅返回用户画像、偏好、习惯等元数据，不做图检索，适合'我的偏好是什么'、'我是什么职业'等用户档案类问题。"
    )


class _MemoryRecord:
    """Read-result item wrapper whose ``model_dump`` matches the host model."""

    __slots__ = ("_payload",)

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def model_dump(self) -> dict[str, Any]:
        return dict(self._payload)


class _MemorySearchResult:
    """Subset of the host ``MemorySearchResult`` consumed by callers here."""

    __slots__ = ("content", "memories")

    def __init__(self, content: str, memories: list[dict[str, Any]]) -> None:
        self.content = content
        self.memories = [_MemoryRecord(item) for item in memories]


async def _forward_memory_call(
        path: str,
        *,
        workspace_id: str | None,
        json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call an internal memory endpoint and return its ``data`` payload.

    Fail-closed: an unconfigured channel raises the stand-in message, and a
    rejected or unreachable upstream raises the envelope error.
    """

    upstream = get_monolith_upstream()
    if upstream is None or not upstream.configured:
        raise BusinessException(_UNAVAILABLE_MESSAGE, code=BizCode.SERVICE_UNAVAILABLE)
    headers = {"X-Workspace-Id": str(workspace_id)} if workspace_id else None
    try:
        response = await upstream.forward_async(
            "POST", path, json_body=json_body, headers=headers
        )
    except UpstreamUnavailableError as exc:
        raise BusinessException(
            f"forward to memory API failed: {exc}",
            code=BizCode.SERVICE_UNAVAILABLE,
        ) from exc
    body = response.body if isinstance(response.body, dict) else {}
    if response.status_code != 200 or body.get("code") != 0:
        raise BusinessException(
            str(body.get("msg") or body.get("error") or "memory API rejected the request"),
            code=as_biz_code(body.get("code")),
        )
    data = body.get("data")
    return data if isinstance(data, dict) else {}


class MemoryService:
    """Monolith-compatible facade backed by the internal memory endpoints."""

    def __init__(
            self,
            *,
            config_id: Any,
            end_user_id: str,
            workspace_id: Any,
            storage_type: str,
            user_rag_memory_id: str | None,
            conversation_id: str | None,
            language: str,
            draft: bool,
    ) -> None:
        self._config_id = str(config_id) if config_id else None
        self._end_user_id = end_user_id
        self._workspace_id = str(workspace_id) if workspace_id else None
        self._storage_type = storage_type
        self._user_rag_memory_id = user_rag_memory_id
        self._conversation_id = conversation_id
        self._language = language
        self._draft = draft

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
    ) -> MemoryService:
        """Build the delegation handle; no upstream call happens here."""

        return cls(
            config_id=config_id,
            end_user_id=end_user_id,
            workspace_id=workspace_id,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
            conversation_id=conversation_id,
            language=language,
            draft=draft,
        )

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
    ) -> _MemorySearchResult:
        data = await _forward_memory_call(
            _READ_PATH,
            workspace_id=self._workspace_id,
            json_body={
                "end_user_id": self._end_user_id,
                "query": query,
                "search_switch": str(search_switch),
                "config_id": self._config_id,
                "history": history or [],
                "limit": limit,
                "includes": [str(item) for item in includes] if includes else None,
                "skip_summary": skip_summary,
                "enable_rerank": enable_rerank,
                "record_display": record_display,
                "storage_type": self._storage_type,
                "user_rag_memory_id": self._user_rag_memory_id or "",
                "conversation_id": self._conversation_id,
                "language": self._language,
                "draft": self._draft,
            },
        )
        return _MemorySearchResult(
            content=str(data.get("content") or ""),
            memories=list(data.get("memories") or []),
        )

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
    ) -> None:
        await _forward_memory_call(
            _INGEST_WORKFLOW_PATH,
            workspace_id=workspace_id,
            json_body={
                "messages": messages,
                "conversation_id": conversation_id,
                "end_user_id": end_user_id,
                "config_id": str(config_id),
                "language": language,
                "storage_type": storage_type,
                "user_rag_memory_id": user_rag_memory_id,
            },
        )


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
    """Host-parity long-term memory tool factory; disabled memory yields no tool."""

    if not memory_config or not memory_config.get("enabled") or not end_user_id:
        return None

    from langchain.tools import tool

    logger.info(
        "Creating long-term memory tool: end_user_id=%s, workspace_id=%s, "
        "config_id=%s, storage_type=%s",
        end_user_id,
        workspace_id,
        config_id,
        storage_type,
    )

    @tool(args_schema=LongTermMemoryInput)
    async def long_term_memory(question: str, search_mode: str) -> str:
        """检索用户的历史记忆，用于了解其背景、偏好和过往对话。

        适用：用户询问历史/个人信息或偏好，或需基于历史上下文做个性化判断。
        不适用：寒暄、纯任务请求(写代码/翻译)、用户已给全信息、纯创作类任务。
        """
        try:
            memory_service = await MemoryService.create(
                config_id,
                end_user_id,
                workspace_id=workspace_id,
                storage_type=storage_type or "neo4j",
                user_rag_memory_id=user_rag_memory_id,
            )
            search_result = await memory_service.read(
                question,
                SearchStrategy(search_mode),
                record_display=True,
            )
            return f"检索到以下历史记忆：\n\n{search_result.content}"
        except Exception as e:
            logger.error(
                "长期记忆检索失败: %s", e, exc_info=True
            )
            return f"记忆检索失败: {str(e)}"

    long_term_memory._tool_meta = {
        "tool_type": "long_term_memory",
        "sources": [{"id": str(config_id), "name": memory_name or str(config_id)}],
    }
    return long_term_memory
