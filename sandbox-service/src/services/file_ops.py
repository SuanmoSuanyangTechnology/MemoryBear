"""File operations of the manager (design doc 7.3 / 7.4), mixed into ``SandboxManager``.

Rules shared by every operation here:

* Paths are normalised and must stay under ``/workspace`` (``files.paths``).
* Reads never create an instance: a user without one gets ``file_not_found`` (or
  ``artifact_gone`` for artifacts). An upload counts as using the sandbox and allocates.
* The instance lock is held only while the instance is looked up / resumed / created.
  ``rec.busy`` stays raised for the whole transfer so pause, expiry and eviction cannot
  take the instance away mid-stream; kernels (and a running ``exec``) are not touched.

The mixin relies on attributes the manager owns: ``_locks``, ``_registry``, ``_backend``,
``_clock``, ``_files_cfg``, ``_signer``, ``metrics`` and the helpers ``_ensure_running``
and ``_drop_lost``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from ..backends.base import (
    KIND_DIR,
    KIND_FILE,
    STATE_PAUSED,
    BackendError,
    BackendUnavailable,
    FileEntry,
    FileNotFound,
    InstanceGone,
    IsADirectory,
)
from ..errors import SandboxError, invalid_request
from ..files.artifact import ArtifactClaims, content_type_for
from ..files.paths import WORKSPACE_ROOT, normalize, out_dir
from ..keys import SandboxKey
from .registry import STATE_CREATING, InstanceRecord

logger = logging.getLogger(__name__)

LIST_DEPTH_MAX = 5
LIST_ENTRIES_MAX = 1000
ARTIFACT_LIST_DEPTH = 3  # out/ plus two levels of sub-directories


class FileDownload:
    """An open download: ``entry`` is known, ``stream()`` yields the bytes.

    The instance stays ``busy`` until ``aclose`` (called when the stream ends, fails or is
    abandoned; safe to call more than once).
    """

    def __init__(
        self,
        entry: FileEntry,
        first: bytes | None,
        source: AsyncIterator[bytes],
        release: Callable[[], None],
        translate: Callable[[Exception], SandboxError],
    ) -> None:
        self.entry = entry
        self._first = first
        self._source = source
        self._release = release
        self._translate = translate
        self._closed = False

    async def stream(self) -> AsyncIterator[bytes]:
        """Yield the file; a size that differs from ``entry.size`` aborts the transfer."""

        sent = 0
        try:
            if self._first is not None:
                sent += len(self._first)
                yield self._first
            async for chunk in self._source:
                sent += len(chunk)
                if sent > self.entry.size:
                    break
                yield chunk
            if sent != self.entry.size:
                # Raising after the response started makes the server drop the connection,
                # so the caller sees a failed transfer instead of a truncated file.
                raise SandboxError("internal_error", "the file changed during the transfer")
        except SandboxError:
            raise
        except Exception as exc:
            raise self._translate(exc) from exc
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        closer = getattr(self._source, "aclose", None)
        try:
            if closer is not None:
                await closer()
        except Exception:  # noqa: BLE001 - releasing must not fail
            logger.debug("closing a file stream failed", exc_info=True)
        finally:
            self._release()


class FileOpsMixin:
    # -------------------------------------------------------------- bookkeeping

    def _translate_file_error(self, exc: Exception, rec: InstanceRecord | None) -> SandboxError:
        if isinstance(exc, SandboxError):
            return exc
        if isinstance(exc, InstanceGone):
            if rec is not None:
                self._drop_lost(rec)  # type: ignore[attr-defined]
            return SandboxError(
                "instance_lost", "the sandbox instance disappeared", retryable=True
            )
        if isinstance(exc, FileNotFound):
            return SandboxError("file_not_found", "no such file or directory")
        if isinstance(exc, IsADirectory):
            return invalid_request("the path is a directory", field="path")
        if isinstance(exc, BackendUnavailable):
            return SandboxError(
                "backend_unavailable", str(exc) or "backend unavailable", retryable=True
            )
        return SandboxError("internal_error", f"backend error: {exc}")

    async def _acquire_files(self, sandbox: SandboxKey, *, create: bool) -> InstanceRecord:
        """A running instance with ``busy`` already raised.

        ``create=False`` never allocates: no instance (or one that vanished while paused)
        raises ``file_not_found``.
        """

        async with self._locks.hold(sandbox.digest):  # type: ignore[attr-defined]
            if not create:
                rec = self._registry.get(sandbox.digest)  # type: ignore[attr-defined]
                if rec is None or rec.instance_id is None or rec.state == STATE_CREATING:
                    raise SandboxError("file_not_found", "no sandbox instance for this end user")
                if rec.state == STATE_PAUSED and not await self._resume_locked(rec):  # type: ignore[attr-defined]
                    raise SandboxError("file_not_found", "the sandbox instance is gone")
                rec.busy += 1
                return rec
            for attempt in range(2):
                rec = None
                try:
                    rec, _path = await self._ensure_running(sandbox)  # type: ignore[attr-defined]
                    rec.busy += 1
                    return rec
                except InstanceGone as exc:
                    if rec is not None:
                        self._drop_lost(rec)  # type: ignore[attr-defined]
                    if attempt == 1:
                        raise SandboxError(
                            "instance_lost", "the sandbox instance disappeared", retryable=True
                        ) from exc
        raise AssertionError("unreachable")  # pragma: no cover

    def _release_files(self, rec: InstanceRecord) -> None:
        rec.busy -= 1
        rec.last_active_ms = self._clock.now_ms()  # type: ignore[attr-defined]

    @asynccontextmanager
    async def _file_session(
        self, sandbox: SandboxKey, *, create: bool
    ) -> AsyncIterator[InstanceRecord]:
        try:
            rec = await self._acquire_files(sandbox, create=create)
        except BackendError as exc:
            raise self._translate_file_error(exc, None) from exc
        try:
            yield rec
        except Exception as exc:
            translated = self._translate_file_error(exc, rec)
            if translated is exc:
                raise
            raise translated from exc
        finally:
            self._release_files(rec)

    # ---------------------------------------------------------------- directory ops

    async def files_stat(self, sandbox: SandboxKey, path: str) -> FileEntry:
        path = normalize(path)
        async with self._file_session(sandbox, create=False) as rec:
            return await self._backend.stat(rec.instance_id, path)  # type: ignore[attr-defined]

    async def files_list(
        self, sandbox: SandboxKey, path: str, depth: int = 1
    ) -> tuple[list[FileEntry], bool]:
        path = normalize(path)
        if isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= LIST_DEPTH_MAX:
            raise invalid_request(f"depth must be 1..{LIST_DEPTH_MAX}", field="depth")
        async with self._file_session(sandbox, create=False) as rec:
            entries = await self._backend.list_dir(rec.instance_id, path, depth)  # type: ignore[attr-defined]
        entries = sorted(entries, key=lambda e: e.path)
        return entries[:LIST_ENTRIES_MAX], len(entries) > LIST_ENTRIES_MAX

    async def files_delete(self, sandbox: SandboxKey, path: str) -> None:
        path = normalize(path)
        if path == WORKSPACE_ROOT:
            raise SandboxError("path_forbidden", f"{WORKSPACE_ROOT} itself cannot be deleted")
        try:
            async with self._file_session(sandbox, create=False) as rec:
                await self._backend.remove(rec.instance_id, path)  # type: ignore[attr-defined]
        except SandboxError as exc:
            if exc.kind != "file_not_found":  # nothing to delete is a success
                raise

    # -------------------------------------------------------------------- transfers

    async def files_upload(
        self,
        sandbox: SandboxKey,
        path: str,
        chunks: AsyncIterator[bytes],
        *,
        overwrite: bool = False,
    ) -> int:
        """Stream ``chunks`` into ``path`` (parents are created); returns the byte count."""

        path = normalize(path)
        if path == WORKSPACE_ROOT:
            raise SandboxError("path_forbidden", f"{WORKSPACE_ROOT} is a directory")
        limit = self._files_cfg.max_file_bytes  # type: ignore[attr-defined]
        async with self._file_session(sandbox, create=True) as rec:
            instance_id = rec.instance_id
            if not overwrite:
                try:
                    await self._backend.stat(instance_id, path)  # type: ignore[attr-defined]
                except FileNotFound:
                    pass
                else:
                    raise SandboxError("file_exists", "the target already exists")
            received = 0

            async def limited() -> AsyncIterator[bytes]:
                nonlocal received
                async for chunk in chunks:
                    received += len(chunk)
                    if received > limit:
                        raise SandboxError(
                            "file_too_large",
                            f"the file exceeds {limit} bytes",
                            params={"limit": limit},
                        )
                    yield chunk

            try:
                size = await self._backend.write_stream(instance_id, path, limited())  # type: ignore[attr-defined]
            except Exception:
                try:  # do not leave a half-written file behind
                    await self._backend.remove(instance_id, path)  # type: ignore[attr-defined]
                except BackendError:
                    pass
                raise
        self.metrics["files_uploaded"] += 1  # type: ignore[attr-defined]
        return size

    async def _open_download(
        self,
        sandbox: SandboxKey,
        path: str,
        *,
        gone_kind: str = "file_not_found",
        expect: ArtifactClaims | None = None,
    ) -> FileDownload:
        try:
            rec = await self._acquire_files(sandbox, create=False)
        except SandboxError as exc:
            if exc.kind == "file_not_found" and gone_kind != "file_not_found":
                raise SandboxError(gone_kind, exc.message) from exc
            raise
        except BackendError as exc:
            raise self._translate_file_error(exc, None) from exc
        released = False

        def release() -> None:
            nonlocal released
            if not released:
                released = True
                self._release_files(rec)

        def translate(exc: Exception) -> SandboxError:
            return self._translate_file_error(exc, rec)

        source: AsyncIterator[bytes] | None = None
        try:
            backend = self._backend  # type: ignore[attr-defined]
            try:
                entry = await backend.stat(rec.instance_id, path)
            except FileNotFound as exc:
                raise SandboxError(gone_kind, "no such file") from exc
            if entry.kind == KIND_DIR:
                raise invalid_request("the path is a directory", field="path")
            if entry.kind != KIND_FILE:
                raise invalid_request("only regular files can be downloaded", field="path")
            if expect is not None and (
                entry.size != expect.size or entry.mtime_ms != expect.mtime_ms
            ):
                raise SandboxError("artifact_gone", "the file changed after it was listed")
            limit = self._files_cfg.max_file_bytes  # type: ignore[attr-defined]
            if entry.size > limit:
                raise SandboxError(
                    "file_too_large",
                    f"the file is {entry.size} bytes; the limit is {limit}",
                    params={"limit": limit, "size": entry.size},
                )
            source = backend.read_stream(rec.instance_id, path)
            # Pull the first chunk now: a failure then still becomes a proper error
            # response instead of an aborted body after the headers went out.
            try:
                first: bytes | None = await source.__anext__()
            except StopAsyncIteration:
                first = None
        except Exception as exc:
            closer = getattr(source, "aclose", None)
            if closer is not None:
                try:
                    await closer()
                except Exception:  # noqa: BLE001
                    pass
            release()
            translated = self._translate_file_error(exc, rec)
            if translated is exc:
                raise
            raise translated from exc
        self.metrics["files_downloaded"] += 1  # type: ignore[attr-defined]
        return FileDownload(entry, first, source, release, translate)

    async def open_download(self, sandbox: SandboxKey, path: str) -> FileDownload:
        return await self._open_download(sandbox, normalize(path))

    # ---------------------------------------------------------------------- artifacts

    async def artifacts_list(
        self, sandbox: SandboxKey, conversation_id: str
    ) -> tuple[list[dict[str, Any]], bool]:
        """Files under the conversation's ``out/`` with a signed id each.

        A user without an instance (or without an ``out/``) has no artifacts: empty list.
        """

        directory = out_dir(conversation_id)
        try:
            async with self._file_session(sandbox, create=False) as rec:
                entries = await self._backend.list_dir(  # type: ignore[attr-defined]
                    rec.instance_id, directory, ARTIFACT_LIST_DEPTH
                )
        except SandboxError as exc:
            if exc.kind == "file_not_found":
                return [], False
            raise
        files = sorted((e for e in entries if e.kind == KIND_FILE), key=lambda e: e.path)
        cfg = self._files_cfg  # type: ignore[attr-defined]
        expires_at = int(self._clock.time() + cfg.artifact_ttl_s)  # type: ignore[attr-defined]
        listed = []
        for e in files[: cfg.max_artifacts]:
            aid = self._signer.issue(  # type: ignore[attr-defined]
                ArtifactClaims(sandbox.digest, e.path, e.size, e.mtime_ms, expires_at)
            )
            listed.append(
                {
                    "aid": aid,
                    "name": e.name,
                    "path": e.path,
                    "size": e.size,
                    "mtime_ms": e.mtime_ms,
                    "content_type": content_type_for(e.name),
                }
            )
        return listed, len(files) > cfg.max_artifacts

    async def open_artifact(self, sandbox: SandboxKey, aid: str) -> tuple[ArtifactClaims, FileDownload]:
        claims = self._signer.verify(aid, now_s=self._clock.time())  # type: ignore[attr-defined]
        if claims.digest != sandbox.digest:
            raise SandboxError("unauthorized", "the artifact belongs to another end user")
        download = await self._open_download(
            sandbox, claims.path, gone_kind="artifact_gone", expect=claims
        )
        return claims, download
