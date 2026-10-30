"""Internal HTTP API of the sandbox service.

Every response uses the project envelope ``{"code", "msg", "data", "error", "time"}``
(success is ``code: 0``). Errors come from ``SandboxError`` and keep the HTTP status
from ``ERROR_TABLE``. Everything except ``/health`` needs the service Bearer token.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ..backends.base import FileEntry
from ..errors import SandboxError
from ..keys import CHANNEL_CODE, CHANNEL_SHELL, KernelKey, SandboxKey
from ..observability import render_prometheus
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


def _download_response(
    download: Any,
    media_type: str | None = None,
    on_complete: Callable[[str, str | None], None] | None = None,
) -> StreamingResponse:
    entry = download.entry

    async def stream() -> AsyncIterator[bytes]:
        try:
            async for chunk in download.stream():
                yield chunk
        except SandboxError as exc:
            if on_complete is not None:
                on_complete("error", exc.kind)
            raise
        except asyncio.CancelledError:
            if on_complete is not None:
                on_complete("cancelled", "client_cancelled")
            raise
        except Exception as exc:
            if on_complete is not None:
                on_complete("error", type(exc).__name__)
            raise
        else:
            if on_complete is not None:
                on_complete("success", None)

    return StreamingResponse(
        stream(),
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
            try:
                if runner is not None:
                    await runner.stop()
            finally:
                if on_close is not None:
                    await on_close()

    app = FastAPI(title="MemoryBear sandbox service", version="0.1.0", lifespan=lifespan)
    auth = Depends(make_auth_dependency(api_token))

    async def observed(
        operation: str,
        sandbox: SandboxKey,
        invoke: Callable[[], Awaitable[Any]],
        *,
        conversation_id: str | None = None,
        channel: str | None = None,
        outcome_of: Callable[[Any], str] | None = None,
        details_of: Callable[[Any], dict[str, object]] | None = None,
        record_success: bool = True,
    ) -> Any:
        """Run an API operation and emit exactly one success/failure audit record."""

        try:
            result = await invoke()
        except SandboxError as exc:
            manager.record_operation(
                operation,
                "error",
                sandbox,
                conversation_id=conversation_id,
                channel=channel,
                reason=exc.kind,
            )
            exc._operation_audited = True  # type: ignore[attr-defined]
            raise
        except Exception as exc:
            manager.record_operation(
                operation,
                "error",
                sandbox,
                conversation_id=conversation_id,
                channel=channel,
                reason=type(exc).__name__,
            )
            raise
        if record_success:
            manager.record_operation(
                operation,
                outcome_of(result) if outcome_of else "success",
                sandbox,
                instance_id=getattr(result, "instance_id", None),
                conversation_id=conversation_id,
                channel=channel,
                details=details_of(result) if details_of else None,
            )
        return result

    def observed_download_response(
        operation: str,
        sandbox: SandboxKey,
        download: Any,
        *,
        conversation_id: str | None = None,
        media_type: str | None = None,
    ) -> StreamingResponse:
        details = {"size": download.entry.size}

        def complete(outcome: str, reason: str | None) -> None:
            manager.record_operation(
                operation,
                outcome,
                sandbox,
                conversation_id=conversation_id,
                reason=reason,
                details=details,
            )

        return _download_response(download, media_type, complete)

    def operation_from_path(path: str) -> str:
        suffix = path.removeprefix("/internal/").strip("/")
        if suffix.startswith("artifacts/"):
            return "artifact_download"
        return suffix.replace("-", "_").replace("/", "_") or "unknown"

    @app.exception_handler(SandboxError)
    async def _sandbox_error(request: Request, exc: SandboxError) -> JSONResponse:
        if not getattr(exc, "_operation_audited", False):
            manager.record_operation(
                operation_from_path(request.url.path), "error", reason=exc.kind
            )
        return _error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = sorted({".".join(str(p) for p in e["loc"][1:]) for e in exc.errors()})
        manager.record_operation(
            operation_from_path(request.url.path), "error", reason="invalid_request"
        )
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

    @app.get("/internal/metrics", dependencies=[auth])
    async def metrics() -> Response:
        return Response(
            content=render_prometheus(manager),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.post("/internal/execute", dependencies=[auth])
    async def execute(body: ExecuteRequest) -> dict[str, Any]:
        sandbox = SandboxKey(body.tenant_id, body.workspace_id, body.end_user_id)
        key = KernelKey(sandbox, body.conversation_id, body.language)
        outcome = await observed(
            "execute",
            sandbox,
            lambda: manager.execute(key, body.code, body.timeout_s),
            conversation_id=body.conversation_id,
            channel=CHANNEL_CODE,
            outcome_of=lambda item: item.verdict.status,
            details_of=lambda item: {
                "duration_ms": item.verdict.duration_ms,
                "path": item.path,
                "stdout_truncated": item.verdict.stdout_truncated,
                "stderr_truncated": item.verdict.stderr_truncated,
                "workspace_reset": item.workspace_reset,
                "kernel_reset": item.kernel_reset,
            },
        )
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
        outcome = await observed(
            "commands",
            sandbox,
            lambda: manager.run_command(
                sandbox,
                body.conversation_id,
                body.cmd,
                cwd=body.cwd,
                env=body.env,
                timeout_s=body.timeout_s,
            ),
            conversation_id=body.conversation_id,
            channel=CHANNEL_SHELL,
            outcome_of=lambda item: item.verdict.status,
            details_of=lambda item: {
                "duration_ms": item.verdict.duration_ms,
                "exit_code": item.exit_code,
                "path": item.path,
                "stdout_truncated": item.verdict.stdout_truncated,
                "stderr_truncated": item.verdict.stderr_truncated,
                "workspace_reset": item.workspace_reset,
            },
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
        closed = await observed(
            "close_conversation",
            sandbox,
            lambda: manager.close_conversation(sandbox, body.conversation_id),
            conversation_id=body.conversation_id,
            details_of=lambda count: {"closed_kernels": count},
        )
        return _envelope(0, "OK", {"closed_kernels": closed})

    @app.post("/internal/destroy-user", dependencies=[auth])
    async def destroy_user(body: DestroyUserRequest) -> dict[str, Any]:
        sandbox = SandboxKey(body.tenant_id, body.workspace_id, body.end_user_id)
        destroyed = await observed(
            "destroy_user",
            sandbox,
            lambda: manager.destroy_user(sandbox),
            details_of=lambda value: {"destroyed": value},
        )
        return _envelope(0, "OK", {"destroyed": destroyed})

    # -- files (design doc 7.3) -------------------------------------------------------
    # Streaming endpoints carry the principal in the query string (the body is the file).

    @app.post("/internal/files/stat", dependencies=[auth])
    async def files_stat(body: FilePathRequest) -> dict[str, Any]:
        sandbox = SandboxKey(body.tenant_id, body.workspace_id, body.end_user_id)
        entry = await observed(
            "files_stat",
            sandbox,
            lambda: manager.files_stat(sandbox, body.path),
            details_of=lambda item: {"kind": item.kind, "size": item.size},
        )
        return _envelope(0, "OK", {"entry": _entry_dict(entry)})

    @app.post("/internal/files/list", dependencies=[auth])
    async def files_list(body: FileListRequest) -> dict[str, Any]:
        sandbox = SandboxKey(body.tenant_id, body.workspace_id, body.end_user_id)
        entries, truncated = await observed(
            "files_list",
            sandbox,
            lambda: manager.files_list(sandbox, body.path, body.depth),
            details_of=lambda result: {
                "entries": len(result[0]),
                "truncated": result[1],
            },
        )
        return _envelope(
            0, "OK", {"entries": [_entry_dict(e) for e in entries], "truncated": truncated}
        )

    @app.post("/internal/files/delete", dependencies=[auth])
    async def files_delete(body: FilePathRequest) -> dict[str, Any]:
        sandbox = SandboxKey(body.tenant_id, body.workspace_id, body.end_user_id)
        await observed(
            "files_delete", sandbox, lambda: manager.files_delete(sandbox, body.path)
        )
        return _envelope(0, "OK", {"deleted": True})

    @app.get("/internal/files/download", dependencies=[auth])
    async def files_download(
        tenant_id: str, workspace_id: str, end_user_id: str, path: str
    ) -> StreamingResponse:
        sandbox = SandboxKey(tenant_id, workspace_id, end_user_id)
        download = await observed(
            "files_download",
            sandbox,
            lambda: manager.open_download(sandbox, path),
            record_success=False,
        )
        return observed_download_response("files_download", sandbox, download)

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
        size = await observed(
            "files_upload",
            sandbox,
            lambda: manager.files_upload(
                sandbox, path, request.stream(), overwrite=overwrite
            ),
            details_of=lambda count: {"size": count, "overwrite": overwrite},
        )
        return _envelope(0, "OK", {"path": path, "size": size})

    @app.post("/internal/artifacts/list", dependencies=[auth])
    async def artifacts_list(body: ArtifactListRequest) -> dict[str, Any]:
        sandbox = SandboxKey(body.tenant_id, body.workspace_id, body.end_user_id)
        artifacts, truncated = await observed(
            "artifacts_list",
            sandbox,
            lambda: manager.artifacts_list(sandbox, body.conversation_id),
            conversation_id=body.conversation_id,
            details_of=lambda result: {
                "artifacts": len(result[0]),
                "truncated": result[1],
            },
        )
        return _envelope(0, "OK", {"artifacts": artifacts, "artifacts_truncated": truncated})

    @app.get("/internal/artifacts/{aid}", dependencies=[auth])
    async def artifact_download(
        aid: str, tenant_id: str, workspace_id: str, end_user_id: str
    ) -> StreamingResponse:
        sandbox = SandboxKey(tenant_id, workspace_id, end_user_id)
        claims, download = await observed(
            "artifact_download",
            sandbox,
            lambda: manager.open_artifact(sandbox, aid),
            record_success=False,
        )
        from ..files.artifact import content_type_for

        return observed_download_response(
            "artifact_download",
            sandbox,
            download,
            media_type=content_type_for(claims.path),
        )

    return app
