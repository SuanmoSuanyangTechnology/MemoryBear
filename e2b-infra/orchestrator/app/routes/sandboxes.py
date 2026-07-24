"""Sandbox lifecycle API routes."""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.responses import StreamingResponse

from app.services.sandbox_manager import SandboxManager

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_sandbox_manager(request: Request) -> SandboxManager:
    return request.app.state.sandbox_manager


def _check_api_key(request: Request) -> None:
    expected = request.app.state.api_key
    if not expected:
        return
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    else:
        token = request.headers.get("X-API-Key", "")
    if token != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


@router.post("/v1/sandboxes")
async def create_sandbox(
    request: Request,
    mgr: SandboxManager = Depends(_get_sandbox_manager),
) -> dict[str, Any]:
    _check_api_key(request)
    return await mgr.create_sandbox()


@router.delete("/v1/sandboxes/{sandbox_id}")
async def destroy_sandbox(
    sandbox_id: str,
    request: Request,
    mgr: SandboxManager = Depends(_get_sandbox_manager),
) -> dict[str, Any]:
    _check_api_key(request)
    ok = await mgr.destroy_sandbox(sandbox_id)
    if not ok:
        raise HTTPException(status_code=404, detail="sandbox not found")
    return {"status": "destroyed", "sandbox_id": sandbox_id}


@router.get("/v1/sandboxes/{sandbox_id}")
async def get_sandbox(
    sandbox_id: str,
    request: Request,
) -> dict[str, Any]:
    _check_api_key(request)
    from app.services.redis_store import SANDBOX_KEY

    redis = request.app.state.redis_store
    key = SANDBOX_KEY.format(sandbox_id=sandbox_id)
    val = await redis.client.get(key)
    if not val:
        raise HTTPException(status_code=404, detail="sandbox not found")
    return json.loads(val)


@router.post("/v1/sandboxes/{sandbox_id}/exec")
async def exec_agent(
    sandbox_id: str,
    request: Request,
    mgr: SandboxManager = Depends(_get_sandbox_manager),
) -> StreamingResponse:
    _check_api_key(request)
    body = await request.json()
    run_id = body.get("run_id")
    snapshot = body.get("snapshot", {})
    if not run_id:
        raise HTTPException(status_code=400, detail="run_id is required")

    snapshot_json = json.dumps(snapshot, ensure_ascii=False)

    async def sse_stream():
        async for event in mgr.exec_agent(sandbox_id, run_id, snapshot_json):
            event_name = event.get("event", "message")
            data = event.get("data", {})
            yield f"event: {event_name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        sse_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/v1/sandboxes/{sandbox_id}/exec/{run_id}")
async def terminate_exec(
    sandbox_id: str,
    run_id: str,
    request: Request,
    mgr: SandboxManager = Depends(_get_sandbox_manager),
) -> dict[str, Any]:
    _check_api_key(request)
    ok = await mgr.terminate_run(run_id)
    if not ok:
        raise HTTPException(status_code=404, detail="run not found")
    return {"status": "terminated", "run_id": run_id}


@router.get("/v1/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/v1/stats")
async def stats(
    request: Request,
    mgr: SandboxManager = Depends(_get_sandbox_manager),
) -> dict[str, Any]:
    _check_api_key(request)
    return await mgr.get_stats()
