"""``SandboxBackend`` on top of Cube: CubeAPI for lifecycle, CubeProxy for code execution.

Error mapping (what the scheduler relies on):

* instance missing (404 / 410 on the control plane, or the control plane confirms it
  after a data-plane failure)  -> ``InstanceGone``
* context missing (404 whose body names the context)               -> ``ContextGone``
* instance paused when code is sent                                  -> ``BackendError``
* substrate out of room (409 on create / resume)                    -> ``CapacityExhausted``
* network failure, timeout, 5xx                                      -> ``BackendUnavailable``
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from ...clock import Clock
from ..base import (
    KIND_DIR,
    KIND_FILE,
    KIND_OTHER,
    META_KEY,
    STATE_PAUSED,
    STATE_RUNNING,
    BackendError,
    BackendUnavailable,
    CapacityExhausted,
    ContextGone,
    FileEntry,
    FileNotFound,
    InstanceGone,
    InstanceHandle,
    InstanceInfo,
    InstanceSpec,
    IsADirectory,
    RunResult,
)
from .client import CubeClient, HttpStatusError, TransportFailure
from .config import CubeConfig

logger = logging.getLogger(__name__)

NEVER_TIMEOUT = -1  # the scheduler owns the lifetime; Cube must not kill on its own
EXEC_GRACE_S = 2.0  # our own deadline sits just above the exec timeout
STREAM_GRACE_S = 5.0  # transport read timeout sits above our own deadline
UNEXPECTED_END = "UnexpectedEndOfExecution"
KERNEL_DIED = "kernel_died"

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_PAUSED_STATES = {"paused", "pausing"}


def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def _mentions_context(body: str) -> bool:
    lowered = body.lower()
    return "context" in lowered and "not found" in lowered


def _parse_ms(value: Any) -> int:
    if not isinstance(value, str):
        return 0
    try:
        return round(datetime.fromisoformat(value).timestamp() * 1000)
    except ValueError:
        return 0


_ENVD_KINDS = {"FILE_TYPE_FILE": KIND_FILE, "FILE_TYPE_DIRECTORY": KIND_DIR}


def _is_missing_path(body: str) -> bool:
    lowered = body.lower()
    return "not_found" in lowered or "does not exist" in lowered or "no such file" in lowered


def _entry_from(raw: Any) -> FileEntry:
    if not isinstance(raw, dict) or not isinstance(raw.get("path"), str):
        raise BackendUnavailable("envd returned a malformed file entry")
    try:
        size = int(raw.get("size") or 0)  # envd sends int64 as a string
    except (TypeError, ValueError):
        size = 0
    path = raw["path"]
    return FileEntry(
        name=str(raw.get("name") or path.rsplit("/", 1)[-1]),
        path=path,
        kind=_ENVD_KINDS.get(str(raw.get("type")), KIND_OTHER),
        size=size,
        mtime_ms=_parse_ms(raw.get("modifiedTime")),
    )


def _status_error(exc: HttpStatusError, conflict: type[BackendError]) -> BackendError:
    if exc.status == 409:
        return conflict(f"HTTP {exc.status}")
    if exc.status >= 500 or exc.status == 429:
        return BackendUnavailable(f"HTTP {exc.status}")
    return BackendError(f"HTTP {exc.status}")


def fold_events(events: list[dict[str, Any]], duration_ms: int) -> RunResult:
    """Turn the execute event stream into the backend's ``RunResult``."""

    out: list[str] = []
    err: list[str] = []
    error: str | None = None
    for event in events:
        kind = event.get("type")
        if kind == "stdout":
            out.append(str(event.get("text", "")))
        elif kind == "stderr":
            err.append(str(event.get("text", "")))
        elif kind == "result":
            text = event.get("text")
            if event.get("is_main_result") and text is not None:
                text = str(text)
                out.append(text if text.endswith("\n") else text + "\n")
        elif kind == "error" and error is None:
            name = str(event.get("name") or "Error")
            value = str(event.get("value") or "")
            if name == UNEXPECTED_END:
                error = KERNEL_DIED
            else:
                error = f"{name}: {value}" if value else name
            trace = _strip_ansi(str(event.get("traceback") or ""))
            if trace:
                err.append(trace if trace.endswith("\n") else trace + "\n")
    return RunResult(
        stdout="".join(out), stderr="".join(err), error=error, duration_ms=duration_ms
    )


class CubeBackend:
    name = "cube"
    # Sandboxes are created without a substrate timeout, so nothing expires underneath us.
    native_ttl_s: float | None = None

    def __init__(self, cfg: CubeConfig, clock: Clock, client: CubeClient | None = None) -> None:
        self._cfg = cfg
        self._clock = clock
        self._client = client or CubeClient(cfg)

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- control plane helpers ------------------------------------------------------

    async def _control(
        self,
        method: str,
        path: str,
        *,
        instance_id: str | None = None,
        conflict: type[BackendError] = BackendUnavailable,
        **kwargs: Any,
    ) -> Any:
        try:
            return await self._client.control(method, path, **kwargs)
        except TransportFailure as exc:
            raise BackendUnavailable(str(exc)) from exc
        except HttpStatusError as exc:
            if instance_id is not None and exc.status in (404, 410):
                raise InstanceGone(instance_id) from exc
            raise _status_error(exc, conflict) from exc

    async def _diagnose(
        self, instance_id: str, cause: Exception, context_id: str | None = None
    ) -> BackendError:
        """Explain a failed data-plane call by asking the control plane."""

        if (
            isinstance(cause, HttpStatusError)
            and context_id is not None
            and cause.status == 404
            and _mentions_context(cause.body)
        ):
            return ContextGone(context_id)
        try:
            detail = await self._client.control("GET", CubeClient.sandbox_path(instance_id))
        except HttpStatusError as exc:
            if exc.status in (404, 410):
                return InstanceGone(instance_id)
            return BackendUnavailable(f"HTTP {exc.status}")
        except TransportFailure as exc:
            return BackendUnavailable(str(exc))
        state = detail.get("state") if isinstance(detail, dict) else None
        if state in _PAUSED_STATES:
            return BackendError("instance is paused")
        if isinstance(cause, HttpStatusError) and cause.status < 500 and cause.status != 429:
            return BackendError(f"HTTP {cause.status}")
        return BackendUnavailable(str(cause))

    async def _data(
        self,
        instance_id: str,
        method: str,
        path: str,
        *,
        body: Any = None,
        context_id: str | None = None,
    ) -> Any:
        try:
            return await self._client.data_json(method, instance_id, path, json_body=body)
        except (HttpStatusError, TransportFailure) as exc:
            raise await self._diagnose(instance_id, exc, context_id) from exc

    # -- instance lifecycle ---------------------------------------------------------

    async def create(self, spec: InstanceSpec) -> InstanceHandle:
        body: dict[str, Any] = {
            "templateID": spec.template,
            "timeout": NEVER_TIMEOUT,
            "metadata": dict(spec.metadata),
        }
        if spec.allow_internet_access is not None:
            # Cube's create contract deliberately uses this snake_case field.
            body["allow_internet_access"] = spec.allow_internet_access
        if spec.network is not None:
            body["network"] = spec.network
        payload = await self._control(
            "POST",
            "/sandboxes",
            conflict=CapacityExhausted,
            json_body=body,
            timeout=self._cfg.create_timeout_s,
        )
        instance_id = payload.get("sandboxID") if isinstance(payload, dict) else None
        if not isinstance(instance_id, str) or not instance_id:
            raise BackendError("create returned no sandbox id")
        return InstanceHandle(instance_id, self._clock.now_ms())

    async def pause(self, instance_id: str) -> None:
        await self._control(
            "POST", CubeClient.sandbox_path(instance_id, "/pause"), instance_id=instance_id
        )

    async def resume(self, instance_id: str) -> None:
        # ``connect`` resumes a paused sandbox and is a no-op for a running one; the
        # dedicated ``resume`` endpoint answers 409 when the sandbox is already running.
        # 409 here means the node has no room (or another lifecycle call is settling).
        await self._control(
            "POST",
            CubeClient.sandbox_path(instance_id, "/connect"),
            instance_id=instance_id,
            conflict=CapacityExhausted,
            json_body={"timeout": NEVER_TIMEOUT},
            timeout=self._cfg.create_timeout_s,
        )

    async def destroy(self, instance_id: str) -> None:
        await self._control(
            "DELETE", CubeClient.sandbox_path(instance_id), instance_id=instance_id
        )

    async def list_owned(self) -> list[InstanceInfo]:
        limit = self._cfg.list_limit
        payload = await self._control("GET", "/v2/sandboxes", params={"limit": limit})
        if not isinstance(payload, list):
            raise BackendUnavailable("sandbox list was not a JSON array")
        if len(payload) >= limit:
            logger.warning(
                "sandbox list reached the page size (%d); raise SANDBOX_CUBE_LIST_LIMIT", limit
            )
        found: list[InstanceInfo] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata") or {}
            instance_id = item.get("sandboxID")
            if not isinstance(instance_id, str) or META_KEY not in metadata:
                continue
            state = item.get("state")
            if state == "running":
                mapped = STATE_RUNNING
            elif state in _PAUSED_STATES:
                mapped = STATE_PAUSED
            else:
                logger.warning("sandbox %s has unusable state %r; skipped", instance_id, state)
                continue
            found.append(
                InstanceInfo(
                    instance_id,
                    {str(k): str(v) for k, v in metadata.items()},
                    mapped,
                    _parse_ms(item.get("startedAt")),
                )
            )
        return found

    # -- code contexts --------------------------------------------------------------

    async def create_context(self, instance_id: str, language: str) -> str:
        payload = await self._data(
            instance_id, "POST", "/contexts", body={"language": language}
        )
        context_id = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(context_id, str) or not context_id:
            raise BackendError("create_context returned no id")
        return context_id

    async def run_code(
        self, instance_id: str, context_id: str, code: str, timeout_s: float
    ) -> RunResult:
        body = {"code": code, "context_id": context_id}
        started = time.monotonic()
        try:
            # A TimeoutError propagates on purpose: the scheduler then restarts the kernel.
            events = await asyncio.wait_for(
                self._client.execute(
                    instance_id, body, read_timeout=timeout_s + EXEC_GRACE_S + STREAM_GRACE_S
                ),
                timeout_s + EXEC_GRACE_S,
            )
        except (HttpStatusError, TransportFailure) as exc:
            raise await self._diagnose(instance_id, exc, context_id) from exc
        return fold_events(events, int((time.monotonic() - started) * 1000))

    async def delete_context(self, instance_id: str, context_id: str) -> None:
        await self._data(
            instance_id,
            "DELETE",
            f"/contexts/{CubeClient.segment(context_id)}",
            context_id=context_id,
        )

    async def restart_context(self, instance_id: str, context_id: str) -> None:
        await self._data(
            instance_id,
            "POST",
            f"/contexts/{CubeClient.segment(context_id)}/restart",
            context_id=context_id,
        )

    # -- files (envd) ----------------------------------------------------------------

    async def _file_error(
        self, instance_id: str, cause: Exception, path: str
    ) -> BackendError:
        if isinstance(cause, HttpStatusError):
            if cause.status == 404 and _is_missing_path(cause.body):
                return FileNotFound(path)
            if cause.status == 400 and "is a directory" in cause.body.lower():
                return IsADirectory(path)
        return await self._diagnose(instance_id, cause)

    async def _rpc(self, instance_id: str, name: str, body: dict[str, Any], path: str) -> Any:
        try:
            return await self._client.envd_rpc(instance_id, name, body)
        except (HttpStatusError, TransportFailure) as exc:
            raise await self._file_error(instance_id, exc, path) from exc

    async def stat(self, instance_id: str, path: str) -> FileEntry:
        payload = await self._rpc(instance_id, "Stat", {"path": path}, path)
        return _entry_from(payload.get("entry") if isinstance(payload, dict) else None)

    async def list_dir(self, instance_id: str, path: str, depth: int = 1) -> list[FileEntry]:
        payload = await self._rpc(instance_id, "ListDir", {"path": path, "depth": depth}, path)
        entries = payload.get("entries") if isinstance(payload, dict) else None
        return [_entry_from(e) for e in entries or []]  # an empty dir answers ``{}``

    async def read_stream(self, instance_id: str, path: str) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._client.envd_read(instance_id, path):
                yield chunk
        except (HttpStatusError, TransportFailure) as exc:
            raise await self._file_error(instance_id, exc, path) from exc

    async def write_stream(
        self, instance_id: str, path: str, chunks: AsyncIterator[bytes]
    ) -> int:
        try:
            return await self._client.envd_write(instance_id, path, chunks)
        except (HttpStatusError, TransportFailure) as exc:
            raise await self._file_error(instance_id, exc, path) from exc

    async def make_dir(self, instance_id: str, path: str) -> None:
        try:
            await self._client.envd_rpc(instance_id, "MakeDir", {"path": path})
        except (HttpStatusError, TransportFailure) as exc:
            if isinstance(exc, HttpStatusError) and (
                exc.status == 409 or "exist" in exc.body.lower()
            ):
                # Already there is fine, as long as it is a directory.
                if (await self.stat(instance_id, path)).kind == KIND_DIR:
                    return
                raise BackendError("a file is in the way") from exc
            raise await self._file_error(instance_id, exc, path) from exc

    async def remove(self, instance_id: str, path: str) -> None:
        try:
            await self._client.envd_rpc(instance_id, "Remove", {"path": path})
        except (HttpStatusError, TransportFailure) as exc:
            err = await self._file_error(instance_id, exc, path)
            if isinstance(err, FileNotFound):
                return  # removing what is not there is not an error
            raise err from exc

    async def health(self) -> bool:
        try:
            await self._client.control("GET", "/health")
        except (HttpStatusError, TransportFailure):
            return False
        return True
