"""
Internal Sandbox Callback Controller

Endpoints that sandbox runtimes call back to for operations requiring
database access or external service integration.

Authenticated via x-sandbox-secret header (E2B_CALLBACK_SECRET).

Routes:
    POST /internal/sandbox/tools/execute       - Execute a tool
    POST /internal/sandbox/knowledge/retrieve  - Retrieve from knowledge base
    POST /internal/sandbox/memory/read         - Read user memory
    POST /internal/sandbox/memory/write        - Write user memory
    POST /internal/sandbox/execution/result    - Report execution result
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db
from app.integrations.knowledge.contracts import (
    KnowledgeCallContext,
    KnowledgeContextError,
    KnowledgePrincipal,
    KnowledgeRetrievalSource,
)
from app.integrations.knowledge.runtime import get_knowledge_retriever
from app.schemas.knowledge_retrieval_schema import KnowledgeRetrievalRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/sandbox", tags=["internal-sandbox"])


# ──────────────────────────────────────────────────────────────
# Authentication
# ──────────────────────────────────────────────────────────────

def verify_sandbox_auth(
    x_sandbox_secret: Optional[str] = Header(None),
    x_sandbox_workspace_id: Optional[str] = Header(None),
    x_sandbox_user_id: Optional[str] = Header(None),
    x_sandbox_execution_id: Optional[str] = Header(None),
) -> dict:
    if x_sandbox_secret != settings.E2B_CALLBACK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid sandbox secret")
    return {
        "workspace_id": x_sandbox_workspace_id,
        "user_id": x_sandbox_user_id,
        "execution_id": x_sandbox_execution_id,
    }


# ──────────────────────────────────────────────────────────────
# Request models
# ──────────────────────────────────────────────────────────────

class ToolExecuteRequest(BaseModel):
    tool_name: str
    tool_type: str
    tool_input: dict
    tool_id: Optional[str] = None


class KnowledgeRetrieveRequest(BaseModel):
    query: str
    knowledge_base_ids: list[str] = Field(default_factory=list)
    top_k: int = 5
    score_threshold: float = 0.5


class MemoryReadRequest(BaseModel):
    query: str
    memory_type: str = "long_term"
    config_id: Optional[str] = None


class MemoryWriteRequest(BaseModel):
    content: str
    memory_type: str = "long_term"
    metadata: dict = Field(default_factory=dict)
    config_id: Optional[str] = None


class ExecutionResultRequest(BaseModel):
    status: str = "completed"
    output: dict = Field(default_factory=dict)
    error: Optional[str] = None
    elapsed_time: float = 0
    token_usage: dict = Field(default_factory=dict)


# ──────────────────────────────────────────────────────────────
# Tool Execution
# ──────────────────────────────────────────────────────────────

@router.post("/tools/execute")
async def execute_tool(
    body: ToolExecuteRequest,
    auth: dict = Depends(verify_sandbox_auth),
    db: Session = Depends(get_db),
):
    """Execute a tool on behalf of the sandbox."""
    workspace_id = auth["workspace_id"]
    user_id = auth["user_id"]

    try:
        from app.services.tool_service import ToolService

        tool_service = ToolService(db)

        # Web search is not a DB-backed tool — handle directly
        if body.tool_name == "web_search_tool":
            from app.services.langchain_tool_server import Search
            query = body.tool_input.get("query", "")
            search_result = Search(query)
            return {"output": f"搜索到以下网络信息：\n\n{search_result}", "status": "success"}

        tool_id = body.tool_id
        if not tool_id:
            tool_config = _find_tool_by_name(db, body.tool_name, workspace_id)
            if tool_config:
                tool_id = str(tool_config.id)

        if not tool_id:
            return {"output": f"Tool not found: {body.tool_name}", "status": "error"}

        w_id = uuid.UUID(workspace_id) if workspace_id else None
        u_id = uuid.UUID(user_id) if user_id else None

        from app.models.workspace_model import Workspace
        workspace = db.get(Workspace, w_id)
        tenant_id = workspace.tenant_id if workspace else None
        if not tenant_id:
            return {"output": f"Tenant not found for workspace: {workspace_id}", "status": "error"}

        result = await tool_service.execute_tool(
            tool_id=tool_id,
            parameters=body.tool_input,
            tenant_id=tenant_id,
            user_id=u_id,
            workspace_id=w_id,
        )

        # Format result to match in-process LangchainAdapter._format_result_for_langchain
        from app.core.tools.langchain_adapter import LangchainAdapter
        output = LangchainAdapter._format_result_for_langchain(result)
        return {
            "output": output,
            "status": "success" if result.success else "error",
        }

    except Exception as e:
        logger.error(
            "Tool execution callback failed: %s", body.tool_name,
            extra={"error": str(e), "tool_type": body.tool_type},
            exc_info=True,
        )
        return {"output": f"Tool execution failed: {str(e)}", "status": "error"}


def _find_tool_by_name(db: Session, name: str, workspace_id: str | None):
    """Look up a tool by name within the workspace's tenant."""
    from app.models.tool_model import ToolConfig
    from app.models.workspace_model import Workspace

    w_id = uuid.UUID(workspace_id) if workspace_id else None
    if not w_id:
        return None

    workspace = db.get(Workspace, w_id)
    if not workspace:
        return None

    stmt = (
        select(ToolConfig)
        .where(
            ToolConfig.name == name,
            ToolConfig.tenant_id == workspace.tenant_id,
            ToolConfig.is_active == True,  # noqa: E712
        )
    )
    return db.scalar(stmt)


# ──────────────────────────────────────────────────────────────
# Knowledge Retrieval
# ──────────────────────────────────────────────────────────────

@router.post("/knowledge/retrieve")
async def retrieve_knowledge(
    body: KnowledgeRetrieveRequest,
    auth: dict = Depends(verify_sandbox_auth),
    db: Session = Depends(get_db),
):
    """Retrieve from knowledge base on behalf of sandbox."""
    workspace_id = auth["workspace_id"]
    user_id = auth["user_id"]

    try:
        if not body.knowledge_base_ids:
            return {"results": []}

        if not workspace_id or not user_id:
            raise KnowledgeContextError("Sandbox knowledge identity is incomplete")
        w_id = uuid.UUID(workspace_id)
        kb_ids = [uuid.UUID(kid) for kid in body.knowledge_base_ids]

        from app.models.workspace_model import Workspace
        workspace = db.get(Workspace, w_id) if w_id else None
        tenant_id = workspace.tenant_id if workspace else None

        request = KnowledgeRetrievalRequest(
            query=body.query,
            kb_ids=kb_ids,
            top_k=min(body.top_k, 100),
            similarity_threshold=body.score_threshold,
            source=KnowledgeRetrievalSource.SANDBOX,
        )
        if tenant_id is None:
            raise KnowledgeContextError("Sandbox knowledge tenant is unavailable")
        context = KnowledgeCallContext(
            principal=KnowledgePrincipal(
                actor_id=uuid.UUID(user_id),
                actor_name=None,
                tenant_id=tenant_id,
                workspace_id=w_id,
            ),
            source=KnowledgeRetrievalSource.SANDBOX,
            trace_id=auth.get("execution_id") or uuid.uuid4().hex,
        )
        db.close()
        result = await get_knowledge_retriever().retrieve(
            request,
            context,
        )

        results = []
        citations = []

        for chunk in result.chunks:
            meta = getattr(chunk, "metadata", {}) or {}
            results.append({
                "content": getattr(chunk, "page_content", "") or "",
                "source": meta.get("document_id", ""),
                "score": meta.get("score", 0),
                "metadata": meta,
            })
            doc_id = meta.get("document_id", "")
            if doc_id:
                citations.append({
                    "document_id": str(doc_id),
                    "file_name": meta.get("file_name", ""),
                    "knowledge_id": str(meta.get("knowledge_id", "")),
                    "score": meta.get("score", 0),
                })

        return {"results": results, "citations": citations}

    except Exception as e:
        logger.error("Knowledge retrieval callback failed: %s", e, exc_info=True)
        return {"results": [], "error": str(e)}


# ──────────────────────────────────────────────────────────────
# Memory Operations
# ──────────────────────────────────────────────────────────────

def _resolve_memory_config(db: Session, workspace_id: str | None, user_id: str | None, config_id: str | None) -> tuple[str | None, uuid.UUID | None]:
    """Resolve end_user_id and config_id for memory operations.

    user_id from the sandbox is treated as the end_user_id.
    Falls back to looking up config_id from EndUser record.
    """
    end_user_id = user_id
    if not end_user_id or not workspace_id:
        return None, None

    try:
        from app.models.end_user_model import EndUser
        e_id = uuid.UUID(end_user_id)
        end_user = db.get(EndUser, e_id)
    except (ValueError, AttributeError):
        return None, None

    # user_id 来自沙箱头，必须与工作空间头一致：否则可借沙箱回调
    # 写入/读取其它空间宿主的记忆（RAG 索引同样只按 end_user_id 隔离）
    if (
        end_user is None
        or not end_user.is_active
        or str(end_user.workspace_id) != str(workspace_id)
    ):
        return None, None

    cid: uuid.UUID | None = uuid.UUID(config_id) if config_id else None
    if cid:
        return end_user_id, cid
    if end_user.memory_config_id:
        cid = end_user.memory_config_id

    return end_user_id, cid


def _resolve_sandbox_storage_type(db: Session, workspace_id: str | None) -> str:
    """按工作空间路由存储引擎；解析失败保持 neo4j 兼容行为。"""
    if not workspace_id:
        return "neo4j"
    try:
        from app.services.workspace_service import (
            get_workspace_storage_type_without_auth,
        )

        return get_workspace_storage_type_without_auth(
            db, uuid.UUID(workspace_id)
        ) or "neo4j"
    except Exception:
        logger.warning(
            "Unable to resolve sandbox workspace storage type: %s",
            workspace_id,
            exc_info=True,
        )
        return "neo4j"


@router.post("/memory/read")
async def read_memory(
    body: MemoryReadRequest,
    auth: dict = Depends(verify_sandbox_auth),
    db: Session = Depends(get_db),
):
    """Read user memory on behalf of sandbox."""
    workspace_id = auth["workspace_id"]
    user_id = auth["user_id"]

    try:
        end_user_id, config_id = _resolve_memory_config(db, workspace_id, user_id, body.config_id)

        if not end_user_id:
            return {"memories": [], "error": "invalid user_id"}

        if not config_id:
            return {"memories": [], "error": "no memory_config_id — configure memory for this end_user first"}

        from app.core.memory.memory_service import MemoryService
        from app.core.memory.enums import SearchStrategy

        memory_service = MemoryService(
            config_id=config_id,
            end_user_id=end_user_id,
            workspace_id=workspace_id,
            storage_type=_resolve_sandbox_storage_type(db, workspace_id),
        )
        result = await memory_service.read(
            query=body.query,
            search_switch=SearchStrategy.QUICK,
            limit=5,
            record_display=True,
        )

        return {"memories": [{"content": result.content, "count": result.count}]}

    except Exception as e:
        logger.error("Memory read callback failed: %s", e, exc_info=True)
        return {"memories": [], "error": str(e)}


@router.post("/memory/write")
async def write_memory(
    body: MemoryWriteRequest,
    auth: dict = Depends(verify_sandbox_auth),
    db: Session = Depends(get_db),
):
    """Write user memory on behalf of sandbox."""
    workspace_id = auth["workspace_id"]
    user_id = auth["user_id"]

    try:
        end_user_id, config_id = _resolve_memory_config(db, workspace_id, user_id, body.config_id)

        if not end_user_id:
            return {"status": "error", "error": "invalid user_id"}

        if not config_id:
            return {"status": "error", "error": "no memory_config_id — configure memory for this end_user first"}

        storage_type = _resolve_sandbox_storage_type(db, workspace_id)

        if str(storage_type or "").strip().lower() == "rag":
            # RAG 空间的记忆存储只有 ES 独立索引，WritePipeline 只写图：
            # 必须改走 RAG 派发，否则会出现「返回 success 但召回永远为空」。
            # 语义与 MCP 写入一致：只写单条 user 消息，无对话上下文。
            text = str(body.content or "").strip()
            if not text:
                return {"status": "error", "error": "content is required"}

            from app.core.exceptions import RateLimitException
            from app.core.memory.enums import MemoryMessageSource
            from app.core.memory.pipelines.dispatcher import push_rag_messages

            # 沙箱工具没有消息 id / 对话时间：用宿主 + 内容派生稳定标识，
            # 同一内容重复写入覆盖同一条记忆（幂等），不会每次生成新文档
            rag_message = {
                "role": "user",
                "content": text,
                "original_message_id": "sandbox:"
                + hashlib.sha1(
                    f"{end_user_id}:{text}".encode("utf-8")
                ).hexdigest(),
            }
            try:
                msg_ids = await push_rag_messages(
                    messages=[rag_message],
                    end_user_id=end_user_id,
                    config_id=str(config_id),
                    source=MemoryMessageSource.AGENT.value,
                )
            except RateLimitException as exc:
                # 与 MCP 工具同口径：限流不报失败（报失败会诱发模型立即重试，
                # 反而加剧限流），返回 skipped 让调用方稍后再写
                logger.warning(
                    "Sandbox RAG write rate limited: user=%s, err=%s",
                    end_user_id,
                    exc,
                )
                return {"status": "skipped", "msg_id": ""}
            return {
                "status": "success",
                "msg_id": msg_ids[0] if msg_ids else "",
            }

        from app.core.memory.memory_service import MemoryService

        memory_service = MemoryService(
            config_id=config_id,
            end_user_id=end_user_id,
            workspace_id=workspace_id,
            storage_type=storage_type,
        )
        target_message: dict = body.metadata or {}
        target_message.setdefault("role", "user")
        target_message.setdefault("content", body.content)

        result = await memory_service.write(target_message=target_message, source="sandbox")

        return {
            "status": result.status,
            "elapsed_seconds": result.elapsed_seconds,
            "extraction": result.extraction,
        }

    except Exception as e:
        logger.error("Memory write callback failed: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}


# ──────────────────────────────────────────────────────────────
# Execution Result Reporting
# ──────────────────────────────────────────────────────────────

@router.post("/execution/result")
async def report_execution_result(
    body: ExecutionResultRequest,
    auth: dict = Depends(verify_sandbox_auth),
):
    """Report final execution result from sandbox."""
    logger.info(
        "Sandbox execution result reported",
        extra={
            "execution_id": auth["execution_id"],
            "status": body.status,
            "elapsed_time": body.elapsed_time,
            "has_error": bool(body.error),
        },
    )
    return {"status": "received"}
