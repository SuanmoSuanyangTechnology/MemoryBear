"""HTTP client for the independent sandbox service (``/internal/*``).

The service wraps every answer in the project envelope ``{"code", "msg", "data",
"error", "time"}``; success is ``code == 0``. Errors keep a non-2xx HTTP status and carry
``data.kind`` / ``data.retryable`` / ``data.retry_after_s``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from .errors import (
    SandboxProtocolError,
    SandboxServiceError,
    SandboxTimeoutError,
    SandboxUnavailableError,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SandboxPrincipal:
    """The verified owner of one sandbox: tenant + workspace + ``end_users.id``."""

    tenant_id: str
    workspace_id: str
    end_user_id: str

    def as_body(self) -> dict[str, str]:
        return {
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "end_user_id": self.end_user_id,
        }


@dataclass(frozen=True)
class ExecuteResult:
    status: str
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    error: str | None
    duration_ms: int
    workspace_reset: bool
    kernel_reset: bool

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True)
class CommandResult:
    status: str
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    error: str | None
    duration_ms: int
    workspace_reset: bool

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True)
class ArtifactInfo:
    """One deliverable file under a conversation's ``out/`` with its signed id."""

    aid: str
    name: str
    path: str
    size: int
    mtime_ms: int
    content_type: str


@dataclass(frozen=True)
class FileDownloadStream:
    """An open streamed download; ``chunks`` is only valid inside the ``async with``."""

    size: int
    mtime_ms: int
    content_type: str | None
    chunks: AsyncIterator[bytes]


class SandboxServiceClient:
    """One process-level HTTP pool talking to sandbox-service."""

    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        timeout: httpx.Timeout,
        limits: httpx.Limits,
        health_timeout: float = 3.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=timeout,
            limits=limits,
            transport=transport,
            trust_env=False,  # never route the private call through an env proxy
        )
        self._health_timeout = health_timeout

    @classmethod
    def from_settings(cls, settings: Any) -> SandboxServiceClient:
        return cls(
            base_url=settings.sandbox_service_base_url,
            api_token=settings.sandbox_service_api_token.get_secret_value(),
            timeout=httpx.Timeout(
                connect=settings.sandbox_service_connect_timeout_seconds,
                pool=settings.sandbox_service_pool_timeout_seconds,
                read=settings.sandbox_service_read_timeout_seconds,
                write=settings.sandbox_service_write_timeout_seconds,
            ),
            limits=httpx.Limits(max_connections=settings.sandbox_service_max_connections),
            health_timeout=settings.sandbox_service_health_timeout_seconds,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def ready(self) -> bool:
        try:
            resp = await self._client.get("health", timeout=self._health_timeout)
        except httpx.HTTPError:
            return False
        return resp.status_code == 200

    async def execute(
        self,
        principal: SandboxPrincipal,
        *,
        conversation_id: str,
        code: str,
        language: str = "python3",
        timeout_s: float | None = None,
    ) -> ExecuteResult:
        body: dict[str, Any] = {
            **principal.as_body(),
            "conversation_id": conversation_id,
            "code": code,
            "language": language,
        }
        if timeout_s is not None:
            body["timeout_s"] = timeout_s
        data = await self._post("internal/execute", body)
        try:
            return ExecuteResult(
                status=str(data["status"]),
                stdout=str(data.get("stdout") or ""),
                stderr=str(data.get("stderr") or ""),
                stdout_truncated=bool(data.get("stdout_truncated")),
                stderr_truncated=bool(data.get("stderr_truncated")),
                error=data.get("error"),
                duration_ms=int(data.get("duration_ms") or 0),
                workspace_reset=bool(data.get("workspace_reset")),
                kernel_reset=bool(data.get("kernel_reset")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SandboxProtocolError("sandbox execute response is malformed") from exc

    async def run_command(
        self,
        principal: SandboxPrincipal,
        *,
        conversation_id: str,
        cmd: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_s: float | None = None,
    ) -> CommandResult:
        body: dict[str, Any] = {
            **principal.as_body(),
            "conversation_id": conversation_id,
            "cmd": cmd,
        }
        if cwd:
            body["cwd"] = cwd
        if env:
            body["env"] = env
        if timeout_s is not None:
            body["timeout_s"] = timeout_s
        data = await self._post("internal/commands", body)
        try:
            exit_code = data.get("exit_code")
            return CommandResult(
                status=str(data["status"]),
                exit_code=int(exit_code) if exit_code is not None else None,
                stdout=str(data.get("stdout") or ""),
                stderr=str(data.get("stderr") or ""),
                stdout_truncated=bool(data.get("stdout_truncated")),
                stderr_truncated=bool(data.get("stderr_truncated")),
                error=data.get("error"),
                duration_ms=int(data.get("duration_ms") or 0),
                workspace_reset=bool(data.get("workspace_reset")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SandboxProtocolError("sandbox command response is malformed") from exc

    async def close_conversation(self, principal: SandboxPrincipal, conversation_id: str) -> int:
        data = await self._post(
            "internal/close-conversation",
            {**principal.as_body(), "conversation_id": conversation_id},
        )
        return int(data.get("closed_kernels") or 0)

    async def destroy_user(self, principal: SandboxPrincipal) -> bool:
        data = await self._post("internal/destroy-user", principal.as_body())
        return bool(data.get("destroyed"))

    # -- files (design doc 7.3 / 7.4) ---------------------------------------------------

    async def list_files(
        self, principal: SandboxPrincipal, path: str, depth: int = 1
    ) -> tuple[list[dict[str, Any]], bool]:
        """Entries under ``path`` (``name``, ``path``, ``kind``, ``size``, ``mtime_ms``)."""

        data = await self._post(
            "internal/files/list", {**principal.as_body(), "path": path, "depth": depth}
        )
        entries = data.get("entries")
        if not isinstance(entries, list):
            raise SandboxProtocolError("sandbox file list is malformed")
        return entries, bool(data.get("truncated"))

    async def list_artifacts(
        self, principal: SandboxPrincipal, conversation_id: str
    ) -> tuple[list[ArtifactInfo], bool]:
        data = await self._post(
            "internal/artifacts/list", {**principal.as_body(), "conversation_id": conversation_id}
        )
        try:
            items = [
                ArtifactInfo(
                    aid=str(a["aid"]),
                    name=str(a["name"]),
                    path=str(a["path"]),
                    size=int(a["size"]),
                    mtime_ms=int(a["mtime_ms"]),
                    content_type=str(a.get("content_type") or "application/octet-stream"),
                )
                for a in data.get("artifacts") or []
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise SandboxProtocolError("sandbox artifact list is malformed") from exc
        return items, bool(data.get("artifacts_truncated"))

    @asynccontextmanager
    async def open_artifact(
        self, principal: SandboxPrincipal, aid: str
    ) -> AsyncIterator[FileDownloadStream]:
        """Stream one artifact. Mid-transfer network errors surface as client errors."""

        path = f"internal/artifacts/{quote(aid, safe='')}"
        try:
            async with self._client.stream("GET", path, params=principal.as_body()) as resp:
                if not resp.is_success:
                    await resp.aread()
                    self._unwrap(resp)  # always raises for a non-2xx answer
                try:
                    size = int(resp.headers.get("x-file-size") or resp.headers["content-length"])
                    mtime_ms = int(resp.headers.get("x-file-mtime-ms") or 0)
                except (KeyError, ValueError) as exc:
                    raise SandboxProtocolError("artifact download lacks a valid size") from exc
                yield FileDownloadStream(
                    size=size,
                    mtime_ms=mtime_ms,
                    content_type=resp.headers.get("content-type"),
                    chunks=resp.aiter_bytes(64 * 1024),
                )
        except httpx.TimeoutException as exc:
            logger.warning("sandbox_http_timeout path=%s error=%s", path, type(exc).__name__)
            raise SandboxTimeoutError("sandbox service request timed out") from exc
        except httpx.HTTPError as exc:
            logger.warning("sandbox_http_failed path=%s error=%s", path, type(exc).__name__)
            raise SandboxUnavailableError("sandbox service is unreachable") from exc

    async def read_file(self, principal: SandboxPrincipal, path: str, *, max_bytes: int) -> bytes:
        """Read a whole file from the sandbox; refuses files larger than ``max_bytes``.

        The size is checked from the response headers before the body is read, so an
        oversized file is never pulled into memory. Raises ``SandboxServiceError`` with
        ``kind == "file_too_large"`` (status 413) for that case.
        """

        route = "internal/files/download"
        params = {**principal.as_body(), "path": path}
        try:
            async with self._client.stream("GET", route, params=params) as resp:
                if not resp.is_success:
                    await resp.aread()
                    self._unwrap(resp)  # always raises for a non-2xx answer
                try:
                    size = int(resp.headers.get("x-file-size") or resp.headers["content-length"])
                except (KeyError, ValueError) as exc:
                    raise SandboxProtocolError("file download lacks a valid size") from exc
                if size > max_bytes:
                    raise SandboxServiceError(
                        status_code=413,
                        code=-1,
                        kind="file_too_large",
                        message=f"file is {size} bytes, limit {max_bytes}",
                        retryable=False,
                        retry_after_s=None,
                    )
                buf = bytearray()
                async for chunk in resp.aiter_bytes(64 * 1024):
                    buf.extend(chunk)
                    if len(buf) > max_bytes:  # the file grew while we were reading it
                        raise SandboxServiceError(
                            status_code=413,
                            code=-1,
                            kind="file_too_large",
                            message=f"file exceeds {max_bytes} bytes",
                            retryable=False,
                            retry_after_s=None,
                        )
                return bytes(buf)
        except httpx.TimeoutException as exc:
            logger.warning("sandbox_http_timeout path=%s error=%s", route, type(exc).__name__)
            raise SandboxTimeoutError("sandbox service request timed out") from exc
        except httpx.HTTPError as exc:
            logger.warning("sandbox_http_failed path=%s error=%s", route, type(exc).__name__)
            raise SandboxUnavailableError("sandbox service is unreachable") from exc

    async def upload_file(
        self,
        principal: SandboxPrincipal,
        path: str,
        chunks: AsyncIterator[bytes],
        *,
        overwrite: bool = True,
    ) -> int:
        """Stream ``chunks`` into ``path`` inside the sandbox; returns the stored size."""

        params = {**principal.as_body(), "path": path, "overwrite": "true" if overwrite else "false"}
        resp = await self._request("PUT", "internal/files/upload", params=params, content=chunks)
        data = self._unwrap(resp)
        try:
            return int(data["size"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SandboxProtocolError("sandbox upload response is malformed") from exc

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            logger.warning("sandbox_http_timeout path=%s error=%s", path, type(exc).__name__)
            raise SandboxTimeoutError("sandbox service request timed out") from exc
        except httpx.RequestError as exc:
            logger.warning("sandbox_http_failed path=%s error=%s", path, type(exc).__name__)
            raise SandboxUnavailableError("sandbox service is unreachable") from exc

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._unwrap(await self._request("POST", path, json=body))

    @staticmethod
    def _unwrap(resp: httpx.Response) -> dict[str, Any]:
        """The ``data`` object of a success envelope; raises for anything else."""

        try:
            payload = resp.json()
        except ValueError as exc:
            raise SandboxProtocolError(
                f"sandbox service returned non-JSON (HTTP {resp.status_code})"
            ) from exc
        if not isinstance(payload, dict):
            raise SandboxProtocolError("sandbox service returned a non-object body")

        code = payload.get("code")
        data = payload.get("data")
        if resp.is_success and code == 0:
            return data if isinstance(data, dict) else {}

        info = data if isinstance(data, dict) else {}
        retry_after = info.get("retry_after_s")
        raise SandboxServiceError(
            status_code=resp.status_code,
            code=code if isinstance(code, int) else -1,
            kind=str(info.get("kind") or payload.get("msg") or "unknown"),
            message=str(payload.get("error") or payload.get("msg") or ""),
            retryable=bool(info.get("retryable")),
            retry_after_s=float(retry_after) if isinstance(retry_after, (int, float)) else None,
        )
