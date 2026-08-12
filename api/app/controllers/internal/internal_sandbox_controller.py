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

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db

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

        w_id = uuid.UUID(workspace_id) if workspace_id else None
        kb_ids = [uuid.UUID(kid) for kid in body.knowledge_base_ids]

        from app.models.workspace_model import Workspace
        workspace = db.get(Workspace, w_id) if w_id else None
        tenant_id = workspace.tenant_id if workspace else None

        from app.schemas.knowledge_retrieval_schema import KnowledgeRetrievalRequest
        from app.core.rag.retrieval.models import RetrievalPrincipal
        from app.services.knowledge_retrieval_service import KnowledgeRetrievalService

        request = KnowledgeRetrievalRequest(
            query=body.query,
            kb_ids=kb_ids,
            top_k=min(body.top_k, 100),
            similarity_threshold=body.score_threshold,
        )
        principal = RetrievalPrincipal(
            id=uuid.UUID(user_id) if user_id else None,
            username=None,
            tenant_id=tenant_id,
            current_workspace_id=w_id,
            is_superuser=False,
        )
        result = await KnowledgeRetrievalService.retrieve_async(request, principal=principal)

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

    cid: uuid.UUID | None = uuid.UUID(config_id) if config_id else None
    if cid:
        return end_user_id, cid

    try:
        from app.models.end_user_model import EndUser
        e_id = uuid.UUID(end_user_id)
        end_user = db.get(EndUser, e_id)
        if end_user and end_user.memory_config_id:
            cid = end_user.memory_config_id
    except (ValueError, AttributeError):
        pass

    return end_user_id, cid


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

        from app.core.memory.memory_service import MemoryService

        memory_service = MemoryService(
            config_id=config_id,
            end_user_id=end_user_id,
            workspace_id=workspace_id,
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
