"""
Internal Sandbox Callback Controller

Provides endpoints that sandbox runtimes call back to for operations
that require database access or external service integration.

These endpoints are NOT exposed to external users — they are only
accessible from the sandbox network and authenticated via internal secret.

Routes:
    POST /internal/sandbox/tools/execute       - Execute a tool
    POST /internal/sandbox/knowledge/retrieve  - Retrieve from knowledge base
    POST /internal/sandbox/memory/read         - Read user memory
    POST /internal/sandbox/memory/write        - Write user memory
    GET  /internal/sandbox/conversation/history - Get conversation history
    POST /internal/sandbox/execution/result    - Report execution result
"""
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, Header, Depends
from pydantic import BaseModel, Field
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
):
    """Verify sandbox callback authentication"""
    if x_sandbox_secret != settings.E2B_CALLBACK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid sandbox secret")
    return {
        "workspace_id": x_sandbox_workspace_id,
        "user_id": x_sandbox_user_id,
        "execution_id": x_sandbox_execution_id,
    }


# ──────────────────────────────────────────────────────────────
# Request/Response Models
# ──────────────────────────────────────────────────────────────

class ToolExecuteRequest(BaseModel):
    tool_name: str
    tool_type: str
    tool_input: dict
    tool_id: Optional[str] = None


class KnowledgeRetrieveRequest(BaseModel):
    query: str
    knowledge_base_ids: list[str]
    top_k: int = 5
    score_threshold: float = 0.5


class MemoryReadRequest(BaseModel):
    query: str
    memory_type: str = "long_term"


class MemoryWriteRequest(BaseModel):
    content: str
    memory_type: str = "long_term"
    metadata: dict = Field(default_factory=dict)


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
    """Execute a tool on behalf of the sandbox

    This endpoint handles tools that need:
    - Database access
    - External service credentials
    - File system access outside sandbox
    """
    workspace_id = auth["workspace_id"]
    user_id = auth["user_id"]

    try:
        from app.services.tool_service import ToolService

        tool_service = ToolService(db)

        # Execute the tool using existing infrastructure
        result = await tool_service.execute_tool_by_name(
            tool_name=body.tool_name,
            tool_type=body.tool_type,
            tool_input=body.tool_input,
            tool_id=uuid.UUID(body.tool_id) if body.tool_id else None,
            workspace_id=uuid.UUID(workspace_id) if workspace_id else None,
            user_id=user_id,
        )

        return {"output": result, "status": "success"}

    except Exception as e:
        logger.error(
            f"Tool execution callback failed: {body.tool_name}",
            extra={"error": str(e), "tool_type": body.tool_type},
            exc_info=True,
        )
        return {"output": f"Tool execution failed: {str(e)}", "status": "error"}


# ──────────────────────────────────────────────────────────────
# Knowledge Retrieval
# ──────────────────────────────────────────────────────────────

@router.post("/knowledge/retrieve")
async def retrieve_knowledge(
    body: KnowledgeRetrieveRequest,
    auth: dict = Depends(verify_sandbox_auth),
    db: Session = Depends(get_db),
):
    """Retrieve from knowledge base on behalf of sandbox"""
    workspace_id = auth["workspace_id"]
    user_id = auth["user_id"]

    try:
        from app.services.knowledge_service import KnowledgeService

        knowledge_service = KnowledgeService(db)

        results = await knowledge_service.retrieve(
            query=body.query,
            knowledge_base_ids=[uuid.UUID(kid) for kid in body.knowledge_base_ids],
            workspace_id=uuid.UUID(workspace_id) if workspace_id else None,
            user_id=user_id,
            top_k=body.top_k,
            score_threshold=body.score_threshold,
        )

        # Serialize results
        serialized = []
        for r in results:
            serialized.append({
                "content": r.get("content", ""),
                "source": r.get("source", ""),
                "score": r.get("score", 0),
                "metadata": r.get("metadata", {}),
            })

        return {"results": serialized}

    except Exception as e:
        logger.error(f"Knowledge retrieval callback failed: {e}", exc_info=True)
        return {"results": [], "error": str(e)}


# ──────────────────────────────────────────────────────────────
# Memory Operations
# ──────────────────────────────────────────────────────────────

@router.post("/memory/read")
async def read_memory(
    body: MemoryReadRequest,
    auth: dict = Depends(verify_sandbox_auth),
    db: Session = Depends(get_db),
):
    """Read user memory on behalf of sandbox"""
    workspace_id = auth["workspace_id"]
    user_id = auth["user_id"]

    try:
        from app.services.memory_service import MemoryService

        memory_service = MemoryService(db)

        memories = await memory_service.search_memories(
            query=body.query,
            workspace_id=uuid.UUID(workspace_id) if workspace_id else None,
            user_id=user_id,
            memory_type=body.memory_type,
        )

        serialized = []
        for m in memories:
            serialized.append({
                "content": m.get("content", ""),
                "timestamp": m.get("timestamp", ""),
                "importance": m.get("importance", 0),
                "memory_type": m.get("memory_type", ""),
            })

        return {"memories": serialized}

    except Exception as e:
        logger.error(f"Memory read callback failed: {e}", exc_info=True)
        return {"memories": [], "error": str(e)}


@router.post("/memory/write")
async def write_memory(
    body: MemoryWriteRequest,
    auth: dict = Depends(verify_sandbox_auth),
    db: Session = Depends(get_db),
):
    """Write user memory on behalf of sandbox"""
    workspace_id = auth["workspace_id"]
    user_id = auth["user_id"]

    try:
        from app.services.memory_service import MemoryService

        memory_service = MemoryService(db)

        await memory_service.write_memory(
            content=body.content,
            workspace_id=uuid.UUID(workspace_id) if workspace_id else None,
            user_id=user_id,
            memory_type=body.memory_type,
            metadata=body.metadata,
        )

        return {"status": "success"}

    except Exception as e:
        logger.error(f"Memory write callback failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


# ──────────────────────────────────────────────────────────────
# Conversation History
# ──────────────────────────────────────────────────────────────

@router.get("/conversation/history")
async def get_conversation_history(
    limit: int = 20,
    auth: dict = Depends(verify_sandbox_auth),
    db: Session = Depends(get_db),
):
    """Get conversation history for the sandbox execution"""
    execution_id = auth["execution_id"]
    workspace_id = auth["workspace_id"]
    user_id = auth["user_id"]

    try:
        from app.services.conversation_service import ConversationService

        conv_service = ConversationService(db)

        # Get the conversation associated with this execution
        messages = conv_service.get_recent_messages(
            workspace_id=uuid.UUID(workspace_id) if workspace_id else None,
            user_id=user_id,
            limit=limit,
        )

        serialized = []
        for m in messages:
            serialized.append({
                "role": m.get("role", ""),
                "content": m.get("content", ""),
                "timestamp": m.get("created_at", ""),
            })

        return {"messages": serialized}

    except Exception as e:
        logger.error(f"Conversation history callback failed: {e}", exc_info=True)
        return {"messages": [], "error": str(e)}


# ──────────────────────────────────────────────────────────────
# Execution Result Reporting
# ──────────────────────────────────────────────────────────────

@router.post("/execution/result")
async def report_execution_result(
    body: ExecutionResultRequest,
    auth: dict = Depends(verify_sandbox_auth),
):
    """Report final execution result from sandbox

    Used by the sandbox to report completion status, which the API
    can use to update execution logs, billing, etc.
    """
    execution_id = auth["execution_id"]

    logger.info(
        "Sandbox execution result reported",
        extra={
            "execution_id": execution_id,
            "status": body.status,
            "elapsed_time": body.elapsed_time,
            "has_error": bool(body.error),
        },
    )

    # TODO: Persist execution log, update billing, etc.

    return {"status": "received"}
