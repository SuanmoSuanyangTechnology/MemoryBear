"""Sandbox lifecycle routes - compatible with E2B SDK protocol"""
import logging
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, Header, Query
from fastapi.responses import StreamingResponse

from app.config import get_settings
from app.models import (
    CreateSandboxRequest,
    CommandRequest,
    CommandResult,
    FileWriteRequest,
    FileReadResponse,
    SandboxInfo,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _verify_api_key(x_api_key: Optional[str] = Header(None)):
    """Verify API key"""
    settings = get_settings()
    if x_api_key != settings.API_SECRET:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ──────────────────────────────────────────────────────────────
# Sandbox Lifecycle
# ──────────────────────────────────────────────────────────────

@router.post("/sandboxes", response_model=SandboxInfo)
async def create_sandbox(
    request: Request,
    body: CreateSandboxRequest,
    x_api_key: Optional[str] = Header(None),
):
    """Create a new sandbox instance"""
    _verify_api_key(x_api_key)
    
    sandbox_manager = request.app.state.sandbox_manager
    settings = get_settings()
    
    # Validate limits
    if body.timeout > settings.MAX_SANDBOX_TIMEOUT:
        raise HTTPException(400, f"Timeout exceeds maximum of {settings.MAX_SANDBOX_TIMEOUT}s")
    if body.cpu_count > settings.MAX_VCPU_COUNT:
        raise HTTPException(400, f"CPU count exceeds maximum of {settings.MAX_VCPU_COUNT}")
    if body.memory_mb > settings.MAX_MEM_SIZE_MB:
        raise HTTPException(400, f"Memory exceeds maximum of {settings.MAX_MEM_SIZE_MB}MB")
    
    try:
        sandbox = await sandbox_manager.create_sandbox(body)
        return sandbox
    except Exception as e:
        logger.error(f"Failed to create sandbox: {e}", exc_info=True)
        raise HTTPException(500, f"Failed to create sandbox: {str(e)}")


@router.get("/sandboxes", response_model=list[SandboxInfo])
async def list_sandboxes(
    request: Request,
    x_api_key: Optional[str] = Header(None),
    status: Optional[str] = Query(None),
):
    """List all sandboxes"""
    _verify_api_key(x_api_key)
    sandbox_manager = request.app.state.sandbox_manager
    sandboxes = await sandbox_manager.list_sandboxes(status=status)
    return sandboxes


@router.get("/sandboxes/{sandbox_id}", response_model=SandboxInfo)
async def get_sandbox(
    request: Request,
    sandbox_id: str,
    x_api_key: Optional[str] = Header(None),
):
    """Get sandbox details"""
    _verify_api_key(x_api_key)
    sandbox_manager = request.app.state.sandbox_manager
    sandbox = await sandbox_manager.get_sandbox(sandbox_id)
    if not sandbox:
        raise HTTPException(404, "Sandbox not found")
    return sandbox


@router.delete("/sandboxes/{sandbox_id}")
async def kill_sandbox(
    request: Request,
    sandbox_id: str,
    x_api_key: Optional[str] = Header(None),
):
    """Kill and remove a sandbox"""
    _verify_api_key(x_api_key)
    sandbox_manager = request.app.state.sandbox_manager
    success = await sandbox_manager.kill_sandbox(sandbox_id)
    if not success:
        raise HTTPException(404, "Sandbox not found")
    return {"status": "killed", "sandbox_id": sandbox_id}


@router.post("/sandboxes/{sandbox_id}/keepalive")
async def keepalive(
    request: Request,
    sandbox_id: str,
    x_api_key: Optional[str] = Header(None),
    timeout: int = Query(default=300, ge=10, le=3600),
):
    """Extend sandbox timeout"""
    _verify_api_key(x_api_key)
    sandbox_manager = request.app.state.sandbox_manager
    success = await sandbox_manager.keepalive(sandbox_id, timeout)
    if not success:
        raise HTTPException(404, "Sandbox not found")
    return {"status": "ok", "sandbox_id": sandbox_id, "new_timeout": timeout}


# ──────────────────────────────────────────────────────────────
# Command Execution
# ──────────────────────────────────────────────────────────────

@router.post("/sandboxes/{sandbox_id}/commands", response_model=CommandResult)
async def run_command(
    request: Request,
    sandbox_id: str,
    body: CommandRequest,
    x_api_key: Optional[str] = Header(None),
):
    """Run a command in the sandbox (blocking, waits for completion)"""
    _verify_api_key(x_api_key)
    sandbox_manager = request.app.state.sandbox_manager
    
    sandbox = await sandbox_manager.get_sandbox(sandbox_id)
    if not sandbox:
        raise HTTPException(404, "Sandbox not found")
    
    try:
        result = await sandbox_manager.run_command(sandbox_id, body)
        return result
    except TimeoutError:
        raise HTTPException(408, "Command timed out")
    except Exception as e:
        logger.error(f"Command execution failed: {e}", exc_info=True)
        raise HTTPException(500, f"Command failed: {str(e)}")


@router.post("/sandboxes/{sandbox_id}/commands/stream")
async def run_command_stream(
    request: Request,
    sandbox_id: str,
    body: CommandRequest,
    x_api_key: Optional[str] = Header(None),
):
    """Run a command with streaming output (Server-Sent Events)"""
    _verify_api_key(x_api_key)
    sandbox_manager = request.app.state.sandbox_manager
    
    sandbox = await sandbox_manager.get_sandbox(sandbox_id)
    if not sandbox:
        raise HTTPException(404, "Sandbox not found")
    
    async def event_stream():
        async for event in sandbox_manager.run_command_stream(sandbox_id, body):
            yield f"data: {event}\n\n"
    
    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


# ──────────────────────────────────────────────────────────────
# File Operations
# ──────────────────────────────────────────────────────────────

@router.post("/sandboxes/{sandbox_id}/files")
async def write_file(
    request: Request,
    sandbox_id: str,
    body: FileWriteRequest,
    x_api_key: Optional[str] = Header(None),
):
    """Write a file into the sandbox"""
    _verify_api_key(x_api_key)
    sandbox_manager = request.app.state.sandbox_manager
    
    sandbox = await sandbox_manager.get_sandbox(sandbox_id)
    if not sandbox:
        raise HTTPException(404, "Sandbox not found")
    
    await sandbox_manager.write_file(sandbox_id, body.path, body.content, body.is_base64)
    return {"status": "ok", "path": body.path}


@router.get("/sandboxes/{sandbox_id}/files", response_model=FileReadResponse)
async def read_file(
    request: Request,
    sandbox_id: str,
    path: str = Query(...),
    x_api_key: Optional[str] = Header(None),
):
    """Read a file from the sandbox"""
    _verify_api_key(x_api_key)
    sandbox_manager = request.app.state.sandbox_manager
    
    sandbox = await sandbox_manager.get_sandbox(sandbox_id)
    if not sandbox:
        raise HTTPException(404, "Sandbox not found")
    
    try:
        content = await sandbox_manager.read_file(sandbox_id, path)
        return FileReadResponse(content=content)
    except FileNotFoundError:
        raise HTTPException(404, f"File not found: {path}")
