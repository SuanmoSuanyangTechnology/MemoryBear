"""Internal HTTP API of the sandbox service.

Every response uses the project envelope ``{"code", "msg", "data", "error", "time"}``
(success is ``code: 0``). Errors come from ``SandboxError`` and keep the HTTP status
from ``ERROR_TABLE``. Everything except ``/health`` needs the service Bearer token.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from ..backends.base import FileEntry
from ..errors import SandboxError
from ..keys import KernelKey, SandboxKey
from ..services.manager import SandboxManager
from ..services.runner import BackgroundRunner
from .auth import make_auth_dependency
from .schemas import (
    ArtifactListRequest,
    CloseConversationRequest,
    CommandRequest,
    DestroyUserRequest,
    ExecuteRequest,
    FileListRequest,
    FilePathRequest,
)


def _entry_dict(e: FileEntry) -> dict[str, Any]:
    return {"name": e.name, "path": e.path, "kind": e.kind, "size": e.size, "mtime_ms": e.mtime_ms}


def _download_response(download: Any, media_type: str | None = None) -> StreamingResponse:
    entry = download.entry
    return StreamingResponse(
        download.stream(),
        media_type=media_type or "application/octet-stream",
        headers={
            "Content-Length": str(entry.size),
            "X-File-Size": str(entry.size),
            "X-File-Mtime-Ms": str(entry.mtime_ms),
        },
    )


def _envelope(code: int, msg: str, data: Any, error: str = "") -> dict[str, Any]:
    return {"code": code, "msg": msg, "data": data, "error": error, "time": int(time.time() * 1000)}


def _error_response(exc: SandboxError) -> JSONResponse:
    data: dict[str, Any] = {"kind": exc.kind, "retryable": exc.retryable}
    if exc.retry_after_s is not None:
        data["retry_after_s"] = exc.retry_after_s
    if exc.params:
        data["params"] = exc.params
    headers = {}
    if exc.retry_after_s is not None:
        headers["Retry-After"] = str(max(1, round(exc.retry_after_s)))
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(exc.code, exc.kind, data, exc.message),
        headers=headers,
    )


def create_app(
    manager: SandboxManager,
    *,
    api_token: str,
    runner: BackgroundRunner | None = None,
    on_close: Callable[[], Awaitable[None]] | None = None,
) -> FastAPI:
    """Build the app around an already configured manager.

    ``runner`` (optional) is started and stopped with the app's lifespan.
    ``on_close`` (optional) is awaited last on shutdown, e.g. to close HTTP clients.
    """

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if runner is not None:
            runner.start()
        try:
            yield
        finally:
            if runner is not None:
                await runner.stop()
            if on_close is not None:
                await on_close()

    app = FastAPI(title="MemoryBear sandbox service", version="0.1.0", lifespan=lifespan)
    auth = Depends(make_auth_dependency(api_token))

    @app.exception_handler(SandboxError)
    async def _sandbox_error(_request: Request, exc: SandboxError) -> JSONResponse:
        return _error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = sorted({".".join(str(p) for p in e["loc"][1:]) for e in exc.errors()})
        return _error_response(
            SandboxError("invalid_request", "malformed request body", params={"fields": fields})
        )

    @app.get("/health")
    async def health() -> JSONResponse:
        ok = await manager._backend.health()  # noqa: SLF001 - liveness of the substrate
        if ok:
            return JSONResponse(status_code=200, content=_envelope(0, "OK", {"ok": True}))
        return JSONResponse(
            status_code=503,
            content=_envelope(11005, "backend_unavailable", {"ok": False}),
        )

    @app.get("/internal/stats", dependencies=[auth])
    async def stats() -> dict[str, Any]:
        return _envelope(0, "OK", manager.stats())

    @app.post("/internal/execute", dependencies=[auth])
    async def execute(body: ExecuteRequest) -> dict[str, Any]:
        sandbox = SandboxKey(body.tenant_id, body.workspace_id, body.end_user_id)
        key = KernelKey(sandbox, body.conversation_id, body.language)
        outcome = await manager.execute(key, body.code, body.timeout_s)
        data = outcome.verdict.to_dict()
        data.update(
            path=outcome.path,
            instance_id=outcome.instance_id,
            context_id=outcome.context_id,
            workspace_reset=outcome.workspace_reset,
            kernel_reset=outcome.kernel_reset,
        )
        return _envelope(0, "OK", data)

    @app.post("/internal/commands", dependencies=[auth])
    async def commands(body: CommandRequest) -> dict[str, Any]:
        sandbox = SandboxKey(body.tenant_id, body.workspace_id, body.end_user_id)
        outcome = await manager.run_command(
            sandbox,
            body.conversation_id,
            body.cmd,
            cwd=body.cwd,
            env=body.env,
            timeout_s=body.timeout_s,
        )
        data = outcome.verdict.to_dict()
        data.update(
            exit_code=outcome.exit_code,
            path=outcome.path,
            instance_id=outcome.instance_id,
            workspace_reset=outcome.workspace_reset,
        )
        return _envelope(0, "OK", data)

    @app.post("/internal/close-conversation", dependencies=[auth])
    async def close_conversation(body: CloseConversationRequest) -> dict[str, Any]:
        sandbox = SandboxKey(body.tenant_id, body.workspace_id, body.end_user_id)
        closed = await manager.close_conversation(sandbox, body.conversation_id)
        return _envelope(0, "OK", {"closed_kernels": closed})

    @app.post("/internal/destroy-user", dependencies=[auth])
    async def destroy_user(body: DestroyUserRequest) -> dict[str, Any]:
        sandbox = SandboxKey(body.tenant_id, body.workspace_id, body.end_user_id)
        destroyed = await manager.destroy_user(sandbox)
        return _envelope(0, "OK", {"destroyed": destroyed})

    # -- files (design doc 7.3) -------------------------------------------------------
    # Streaming endpoints carry the principal in the query string (the body is the file).

    @app.post("/internal/files/stat", dependencies=[auth])
    async def files_stat(body: FilePathRequest) -> dict[str, Any]:
        sandbox = SandboxKey(body.tenant_id, body.workspace_id, body.end_user_id)
        return _envelope(0, "OK", {"entry": _entry_dict(await manager.files_stat(sandbox, body.path))})

    @app.post("/internal/files/list", dependencies=[auth])
    async def files_list(body: FileListRequest) -> dict[str, Any]:
        sandbox = SandboxKey(body.tenant_id, body.workspace_id, body.end_user_id)
        entries, truncated = await manager.files_list(sandbox, body.path, body.depth)
        return _envelope(
            0, "OK", {"entries": [_entry_dict(e) for e in entries], "truncated": truncated}
        )

    @app.post("/internal/files/delete", dependencies=[auth])
    async def files_delete(body: FilePathRequest) -> dict[str, Any]:
        sandbox = SandboxKey(body.tenant_id, body.workspace_id, body.end_user_id)
        await manager.files_delete(sandbox, body.path)
        return _envelope(0, "OK", {"deleted": True})

    @app.get("/internal/files/download", dependencies=[auth])
    async def files_download(
        tenant_id: str, workspace_id: str, end_user_id: str, path: str
    ) -> StreamingResponse:
        sandbox = SandboxKey(tenant_id, workspace_id, end_user_id)
        return _download_response(await manager.open_download(sandbox, path))

    @app.put("/internal/files/upload", dependencies=[auth])
    async def files_upload(
        request: Request,
        tenant_id: str,
        workspace_id: str,
        end_user_id: str,
        path: str,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        sandbox = SandboxKey(tenant_id, workspace_id, end_user_id)
        size = await manager.files_upload(sandbox, path, request.stream(), overwrite=overwrite)
        return _envelope(0, "OK", {"path": path, "size": size})

    @app.post("/internal/artifacts/list", dependencies=[auth])
    async def artifacts_list(body: ArtifactListRequest) -> dict[str, Any]:
        sandbox = SandboxKey(body.tenant_id, body.workspace_id, body.end_user_id)
        artifacts, truncated = await manager.artifacts_list(sandbox, body.conversation_id)
        return _envelope(0, "OK", {"artifacts": artifacts, "artifacts_truncated": truncated})

    @app.get("/internal/artifacts/{aid}", dependencies=[auth])
    async def artifact_download(
        aid: str, tenant_id: str, workspace_id: str, end_user_id: str
    ) -> StreamingResponse:
        sandbox = SandboxKey(tenant_id, workspace_id, end_user_id)
        claims, download = await manager.open_artifact(sandbox, aid)
        from ..files.artifact import content_type_for

        return _download_response(download, content_type_for(claims.path))

    return app
