"""Sandbox manager: lazy allocation, admission, eviction and TTL (design doc 3.1).

Nothing is allocated until ``execute`` is called, i.e. until the model really
produced a sandbox tool call. Two lazy layers: the microVM per ``SandboxKey`` and
one kernel (code context) per ``KernelKey``.

Concurrency rules (single event loop):

* ``_locks`` (per instance digest) is held while an instance is created, resumed,
  paused, destroyed or gets a new kernel. It is released before user code runs.
* ``rec.busy`` counts in-flight executions; it is only changed under the instance
  lock, so eviction / pause / expiry (which use ``try_hold`` and re-check ``busy``)
  can never touch an instance that is serving a request.
* ``_kernel_locks`` serialises executions on one kernel; different kernels of the
  same instance run in parallel.
* Eviction never waits for another instance's lock (``try_hold``), so a request
  that holds its own lock while evicting cannot deadlock with another one.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections import Counter, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..backends.base import (
    META_KEY,
    META_TENANT,
    STATE_PAUSED,
    STATE_RUNNING,
    BackendError,
    BackendUnavailable,
    CapacityExhausted,
    ContextGone,
    InstanceGone,
    InstanceSpec,
    RunResult,
    SandboxBackend,
)
from ..clock import Clock
from ..egress.policy import StrictEgressPolicy
from ..errors import SandboxError, invalid_request
from ..files.artifact import ArtifactSigner
from ..files.config import FilesConfig
from ..keys import CHANNEL_CODE, CHANNEL_SHELL, KernelKey, SandboxKey
from ..observability import (
    NullOperationAuditSink,
    OperationAuditEvent,
    OperationAuditSink,
)
from .eviction import is_candidate, select_victims
from .file_ops import FileOpsMixin
from .keyed_lock import KeyedLocks
from .registry import STATE_CREATING, InstanceRecord, KernelRecord, Registry
from .scheduler_config import SchedulerConfig
from .shell import (
    build_chdir_code,
    build_reaper,
    build_wrapper,
    fold_result,
    resolve_cwd,
    validate_command,
    validate_env,
)
from .verdict import OutputBudget, Verdict, judge

logger = logging.getLogger(__name__)

PATH_HIT = "hit"
PATH_RESUME = "resume"
PATH_CREATE = "create"

RUN_GRACE_S = 5.0  # extra wall-clock budget on top of the exec timeout
REAP_TIMEOUT_S = 10.0  # budget for killing a terminal kernel's leftover process groups
INIT_TIMEOUT_S = 10.0  # budget for moving a fresh code kernel into its conversation directory
BUSY_RETRY_AFTER_S = 5.0


def _ms(seconds: float) -> int:
    return int(seconds * 1000)


@dataclass
class ExecOutcome:
    result: RunResult
    path: str  # hit | resume | create
    instance_id: str
    context_id: str
    verdict: Verdict  # the single terminal status, output clamped (verdict.py)
    workspace_reset: bool = False  # files are gone: the workspace was destroyed before
    kernel_reset: bool = False  # variables are gone: the kernel was rebuilt
    exit_code: int | None = None  # terminal commands only; None for code or when killed


@dataclass
class TickReport:
    paused: int = 0
    expired: int = 0  # paused longer than B
    lifetime_expired: int = 0  # older than C
    evicted: int = 0
    kernels_reaped: int = 0  # idle kernels deleted (kernel_idle_s)
    errors: int = 0


@dataclass
class ReconcileReport:
    orphans_destroyed: int = 0
    lost: int = 0
    errors: int = 0


class SandboxManager(FileOpsMixin):
    def __init__(
        self,
        backend: SandboxBackend,
        config: SchedulerConfig | None = None,
        *,
        clock: Clock | None = None,
        registry: Registry | None = None,
        budget: OutputBudget | None = None,
        files: FilesConfig | None = None,
        audit_sink: OperationAuditSink | None = None,
        egress_policy: StrictEgressPolicy | None = None,
    ) -> None:
        self._backend = backend
        self._budget = budget or OutputBudget()
        self._files_cfg = files or FilesConfig()
        # Without a configured key the ids only live as long as this process.
        self._signer = ArtifactSigner(self._files_cfg.artifact_key or secrets.token_urlsafe(32))
        self._cfg = config or SchedulerConfig()
        self._clock = clock or Clock()
        self._registry = registry or Registry()
        self._locks = KeyedLocks()
        self._kernel_locks = KeyedLocks()
        self._create_sem = asyncio.Semaphore(self._cfg.create_concurrency)
        self._create_log: dict[str, deque[float]] = {}
        self._waiting = 0
        self.metrics: Counter[str] = Counter()
        self._audit_sink = audit_sink or NullOperationAuditSink()
        self._egress_policy = egress_policy

    # ------------------------------------------------------------------ public

    @property
    def registry(self) -> Registry:
        return self._registry

    def stats(self) -> dict[str, object]:
        return {
            "instances": self._registry.count(),
            "by_state": self._registry.counts_by_state(),
            "queue_depth": self._waiting,
            "metrics": dict(self.metrics),
        }

    def record_operation(
        self,
        operation: str,
        outcome: str,
        sandbox: SandboxKey | None = None,
        *,
        instance_id: str | None = None,
        conversation_id: str | None = None,
        channel: str | None = None,
        reason: str | None = None,
        details: dict[str, object] | None = None,
        tenant_id: str | None = None,
        sandbox_digest: str | None = None,
    ) -> None:
        """Record one bounded metric and one identity-rich durable audit event."""

        self.metrics[f"operation.{operation}.{outcome}"] += 1
        event = OperationAuditEvent(
            ts_ms=self._clock.now_ms(),
            operation=operation,
            outcome=outcome,
            tenant_id=sandbox.tenant_id if sandbox else tenant_id,
            workspace_id=sandbox.workspace_id if sandbox else None,
            end_user_id=sandbox.end_user_id if sandbox else None,
            sandbox_digest=sandbox.digest if sandbox else sandbox_digest,
            instance_id=instance_id,
            conversation_id=conversation_id,
            channel=channel,
            reason=reason,
            details=details,
        )
        try:
            self._audit_sink.record(event)
        except Exception:  # noqa: BLE001 - audit failure must not hide the operation result
            self.metrics["audit_write_failures"] += 1
            logger.exception("operation audit write failed: operation=%s", operation)

    def _record_backend_error(
        self, operation: str, exc: BackendError, rec: InstanceRecord | None = None
    ) -> None:
        # The same backend exception may cross several manager layers. Count it once,
        # at the most specific layer that first observes it.
        if getattr(exc, "_sandbox_observed", False):
            return
        exc._sandbox_observed = True
        kind = type(exc).__name__
        self.metrics[f"backend_error.{operation}.{kind}"] += 1
        self.record_operation(
            f"backend_{operation}",
            "error",
            instance_id=rec.instance_id if rec else None,
            reason=kind,
            tenant_id=rec.tenant_id if rec else None,
            sandbox_digest=rec.digest if rec else None,
        )

    def _record_lifecycle(
        self,
        action: str,
        rec: InstanceRecord,
        *,
        sandbox: SandboxKey | None = None,
        reason: str | None = None,
    ) -> None:
        self.metrics[f"lifecycle.{action}"] += 1
        self.record_operation(
            "lifecycle",
            "success",
            sandbox,
            instance_id=rec.instance_id,
            reason=reason or action,
            details={"action": action},
            tenant_id=rec.tenant_id,
            sandbox_digest=rec.digest,
        )

    async def execute(
        self, key: KernelKey, code: str, timeout_s: float | None = None
    ) -> ExecOutcome:
        """Run ``code`` in the kernel of ``key``, allocating what is missing."""

        if not isinstance(code, str):
            raise invalid_request("code must be a string", field="code")
        return await self._guarded(
            key, code, self._resolve_timeout(timeout_s), operation="execute"
        )

    async def run_command(
        self,
        sandbox: SandboxKey,
        conversation_id: str,
        cmd: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_s: float | None = None,
    ) -> ExecOutcome:
        """Run one shell command through the conversation's dedicated shell kernel.

        The shell kernel is separate from the code kernel, so a long command does not
        hold the code interpreter's lock. Timeout, output budget, resets and instance
        loss are handled by the same path as ``execute``.
        """

        validate_command(cmd)
        workdir = resolve_cwd(cwd, conversation_id)
        extra_env = validate_env(env)
        timeout = self._resolve_timeout(timeout_s)
        key = KernelKey(sandbox, conversation_id, "python3", CHANNEL_SHELL)
        code, marker = build_wrapper(cmd, workdir, extra_env, timeout, conversation_id)
        return await self._guarded(
            key,
            code,
            timeout,
            lambda raw: fold_result(raw, marker),
            operation="commands",
        )

    def _resolve_timeout(self, timeout_s: float | None) -> float:
        timeout = self._cfg.exec_timeout_default_s if timeout_s is None else timeout_s
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise invalid_request("timeout_s must be positive", field="timeout_s")
        # A request can only lower its timeout below the service-wide hard cap.
        return min(float(timeout), self._cfg.exec_timeout_max_s)

    async def _guarded(
        self,
        key: KernelKey,
        code: str,
        timeout: float,
        fold: Callable[[RunResult], tuple[RunResult, int | None]] | None = None,
        *,
        operation: str,
    ) -> ExecOutcome:
        try:
            return await self._execute(key, code, timeout, fold, operation=operation)
        except SandboxError:
            raise
        except BackendUnavailable as exc:
            self._record_backend_error(operation, exc)
            raise SandboxError(
                "backend_unavailable", str(exc) or "backend unavailable", retryable=True
            ) from exc
        except BackendError as exc:
            self._record_backend_error(operation, exc)
            raise SandboxError("internal_error", f"backend error: {exc}") from exc

    async def close_conversation(self, sandbox: SandboxKey, conversation_id: str) -> int:
        """Drop the kernels of one conversation; the instance stays until its TTL.

        Cleaning ``/workspace/sessions/<conv_id>/`` needs file operations and belongs
        to the backend adapter layer, not here.
        """

        rec = self._registry.get(sandbox.digest)
        if rec is None:
            return 0
        popped = [
            (token, rec.kernels.pop(token))
            for token in [t for t, k in rec.kernels.items() if k.conversation_id == conversation_id]
        ]
        for token, kr in popped:
            if rec.state != STATE_RUNNING or rec.instance_id is None:
                rec.stale_contexts.append(kr.context_id)
                continue
            lock_key = f"{sandbox.digest}:{token}"  # same shape as KernelKey.lock_key
            async with self._kernel_locks.hold(lock_key):
                try:
                    await self._retire_kernel(rec, kr)
                except ContextGone as exc:
                    self._record_backend_error("close_conversation", exc, rec)
                except InstanceGone as exc:
                    self._record_backend_error("close_conversation", exc, rec)
                    self._drop_lost(rec)
                    break
                except BackendError as exc:
                    self._record_backend_error("close_conversation", exc, rec)
                    rec.stale_contexts.append(kr.context_id)
        return len(popped)

    async def destroy_user(self, sandbox: SandboxKey) -> bool:
        """Revoke: destroy the user's instance now. No ``workspace_reset`` marker."""

        async with self._locks.hold(sandbox.digest):
            rec = self._registry.get(sandbox.digest)
            self._registry.clear_tombstone(sandbox.digest)
            if rec is None:
                return False
            if rec.instance_id is not None:
                try:
                    await self._destroy_locked(rec, tombstone=False, reason="destroy_user")
                except BackendUnavailable as exc:
                    self._record_backend_error("destroy_user", exc, rec)
                    raise SandboxError(
                        "backend_unavailable",
                        str(exc) or "backend unavailable",
                        retryable=True,
                    ) from exc
                except BackendError as exc:
                    self._record_backend_error("destroy_user", exc, rec)
                    raise SandboxError("internal_error", f"backend error: {exc}") from exc
            else:
                self._registry.remove(rec)
            return True

    async def tick(self) -> TickReport:
        """Periodic housekeeping: idle pause (A), retention (B), lifetime (C), watermark."""

        rep = TickReport()
        for rec in self._registry.all():
            if rec.state == STATE_CREATING:
                continue
            try:
                await self._tick_one(rec, rep)
            except InstanceGone as exc:
                self._record_backend_error("tick", exc, rec)
                self._drop_lost(rec)
            except BackendError as exc:
                rep.errors += 1
                self._record_backend_error("tick", exc, rec)
                logger.warning("tick failed for %s: %s", rec.digest, exc)
        if self._registry.count() >= self._cfg.high_water * self._cfg.capacity:
            rep.evicted += await self._relieve_pressure()
        now = self._clock.now_ms()
        self._registry.prune_tombstones(now - _ms(self._cfg.tombstone_ttl_s))
        window = self._cfg.create_window_s
        for digest in list(self._create_log):
            log = self._create_log[digest]
            while log and self._clock.time() - log[0] >= window:
                log.popleft()
            if not log:
                del self._create_log[digest]
        return rep

    async def reconcile(self) -> ReconcileReport:
        """Match the backend's instances with the registry: kill orphans, drop ghosts."""

        rep = ReconcileReport()
        started_ms = self._clock.now_ms()
        grace_ms = _ms(self._cfg.orphan_grace_s)
        infos = await self._backend.list_owned()
        listed = {i.instance_id for i in infos}
        known = {r.instance_id for r in self._registry.all() if r.instance_id}
        for info in infos:
            if META_KEY not in info.metadata or info.instance_id in known:
                continue
            if started_ms - info.created_at_ms < grace_ms:
                continue
            try:
                await self._backend.destroy(info.instance_id)
                rep.orphans_destroyed += 1
                self.metrics["lifecycle.orphan_destroyed"] += 1
                self.record_operation(
                    "lifecycle",
                    "success",
                    instance_id=info.instance_id,
                    reason="orphan_destroyed",
                    details={"action": "orphan_destroyed"},
                )
            except InstanceGone as exc:
                self._record_backend_error("reconcile_destroy", exc)
            except BackendError as exc:
                rep.errors += 1
                self._record_backend_error("reconcile_destroy", exc)
                logger.warning("orphan %s not destroyed: %s", info.instance_id, exc)
        for rec in self._registry.all():
            if rec.state == STATE_CREATING or rec.instance_id is None:
                continue
            if rec.instance_id in listed or started_ms - rec.created_ms < grace_ms:
                continue
            async with self._locks.try_hold(rec.digest) as got:
                if got and self._registry.get(rec.digest) is rec:
                    self._drop_lost(rec)
                    rep.lost += 1
        return rep

    # --------------------------------------------------------------- execution

    async def _execute(
        self,
        key: KernelKey,
        code: str,
        timeout: float,
        fold: Callable[[RunResult], tuple[RunResult, int | None]] | None = None,
        *,
        operation: str,
    ) -> ExecOutcome:
        digest = key.sandbox.digest
        rec, kr, path = await self._acquire(key)
        self.metrics[f"path.{path}"] += 1
        try:
            exit_code: int | None = None
            try:
                result, kr, kernel_reset = await self._run_in_kernel(rec, kr, key, code, timeout)
                if kr.reset_pending:
                    kernel_reset = True
                    kr.reset_pending = False
                if fold is not None:
                    result, exit_code = fold(result)
                    # A shell kernel holds no user variables, so a rebuild is not news.
                    kernel_reset = False
            except InstanceGone as exc:
                self._record_backend_error(operation, exc, rec)
                self._drop_lost(rec)
                raise SandboxError(
                    "instance_lost", "the sandbox instance disappeared", retryable=True
                ) from exc
            reset = self._registry.has_tombstone(digest)
            if reset:
                self._registry.clear_tombstone(digest)
            return ExecOutcome(
                result=result,
                path=path,
                instance_id=rec.instance_id or "",
                context_id=kr.context_id,
                verdict=judge(result, self._budget),
                workspace_reset=reset,
                kernel_reset=kernel_reset,
                exit_code=exit_code,
            )
        finally:
            rec.busy -= 1
            rec.last_active_ms = self._clock.now_ms()
            used = rec.kernels.get(key.token)
            if used is not None:
                used.last_used_ms = rec.last_active_ms

    async def _acquire(self, key: KernelKey) -> tuple[InstanceRecord, KernelRecord, str]:
        """Return a running instance with the kernel; ``busy`` is already incremented."""

        sandbox = key.sandbox
        async with self._locks.hold(sandbox.digest):
            for attempt in range(2):
                rec: InstanceRecord | None = None
                try:
                    rec, path = await self._ensure_running(sandbox)
                    await self._flush_stale(rec)
                    kr = rec.kernels.get(key.token) or await self._create_kernel(rec, key)
                    rec.busy += 1
                    return rec, kr, path
                except InstanceGone as exc:
                    if rec is not None:
                        self._drop_lost(rec)
                    if attempt == 1:
                        raise SandboxError(
                            "instance_lost", "the sandbox instance disappeared", retryable=True
                        ) from exc
        raise AssertionError("unreachable")  # pragma: no cover

    async def _ensure_running(self, sandbox: SandboxKey) -> tuple[InstanceRecord, str]:
        rec = self._registry.get(sandbox.digest)
        if rec is not None and rec.state == STATE_RUNNING:
            return rec, PATH_HIT
        if rec is not None and rec.state == STATE_PAUSED and rec.instance_id is not None:
            if await self._resume_locked(rec):
                return rec, PATH_RESUME
        elif rec is not None:
            # A leftover half-built record (e.g. creation interrupted): start over.
            self._registry.remove(rec)
        return await self._create_instance(sandbox), PATH_CREATE

    async def _resume_locked(self, rec: InstanceRecord) -> bool:
        """Resume a paused instance (caller holds its lock). False: the backend lost it."""

        assert rec.instance_id is not None
        instance_id = rec.instance_id
        try:
            await self._with_capacity_retry(lambda: self._backend.resume(instance_id))
        except InstanceGone as exc:
            self._record_backend_error("resume", exc, rec)
            self._drop_lost(rec)
            return False
        rec.state = STATE_RUNNING
        rec.paused_at_ms = None
        rec.last_active_ms = self._clock.now_ms()
        # Paused time is not idle time: without this the first tick after a
        # resume would delete every kernel.
        for kernel in rec.kernels.values():
            kernel.last_used_ms = rec.last_active_ms
        self._record_lifecycle("resumed", rec)
        return True

    async def _create_kernel(self, rec: InstanceRecord, key: KernelKey) -> KernelRecord:
        # The limit applies per channel: code kernels hold user state and memory, shell
        # kernels are stateless launchers. Counting them together would let one
        # conversation (code + shell) use two slots; counting per channel keeps the
        # limit meaning "conversations", while shell kernels stay bounded as well.
        same_channel = sum(1 for k in rec.kernels.values() if k.channel == key.channel)
        if same_channel >= self._cfg.max_kernels_per_user:
            self.metrics["quota_exceeded"] += 1
            raise SandboxError(
                "quota_exceeded",
                f"at most {self._cfg.max_kernels_per_user} kernels per end user",
                params={"limit": self._cfg.max_kernels_per_user},
            )
        assert rec.instance_id is not None
        context_id = await self._backend.create_context(rec.instance_id, key.language)
        kr = KernelRecord(
            context_id, key.language, key.conversation_id, self._clock.now_ms(), key.channel
        )
        kr.needs_init = key.channel == CHANNEL_CODE
        if key.token in rec.reaped_tokens:
            rec.reaped_tokens.discard(key.token)
            kr.reset_pending = True
        rec.kernels[key.token] = kr
        return kr

    async def _init_kernel(self, rec: InstanceRecord, kr: KernelRecord, key: KernelKey) -> None:
        """Move a fresh python code kernel into ``/workspace/sessions/<conversation>``.

        Relative paths used by the code interpreter then stay inside the conversation
        directory instead of the per-user shared ``/workspace``. Best effort: a failure
        is logged and never blocks the user's code; gone-errors propagate so the caller
        recreates the kernel or the instance.
        """

        if not kr.needs_init:
            return
        if key.channel != CHANNEL_CODE or key.language != "python3":
            kr.needs_init = False
            return
        assert rec.instance_id is not None
        try:
            res = await asyncio.wait_for(
                self._backend.run_code(
                    rec.instance_id,
                    kr.context_id,
                    build_chdir_code(key.conversation_id),
                    INIT_TIMEOUT_S,
                ),
                INIT_TIMEOUT_S + RUN_GRACE_S,
            )
            if res.error:
                logger.warning("kernel %s chdir failed: %s", kr.context_id, res.error)
        except (ContextGone, InstanceGone):
            raise
        except (BackendError, TimeoutError) as exc:
            logger.warning("kernel %s chdir not applied: %s", kr.context_id, exc)
        kr.needs_init = False

    async def _flush_stale(self, rec: InstanceRecord) -> None:
        """Best-effort deletion of kernels left behind by ``close_conversation``."""

        while rec.stale_contexts and rec.instance_id is not None:
            context_id = rec.stale_contexts.pop()
            try:
                await self._backend.delete_context(rec.instance_id, context_id)
            except ContextGone as exc:
                self._record_backend_error("stale_context_cleanup", exc, rec)
            except InstanceGone as exc:
                self._record_backend_error("stale_context_cleanup", exc, rec)
                raise
            except BackendError as exc:
                self._record_backend_error("stale_context_cleanup", exc, rec)
                logger.warning("stale context %s not deleted: %s", context_id, exc)

    async def _retire_kernel(self, rec: InstanceRecord, kr: KernelRecord) -> None:
        """Delete a kernel. A terminal kernel first kills the process groups its commands
        left behind (background servers); anything that escaped its group is out of reach.

        Raises like ``delete_context``; the reaper itself is best effort.
        """

        assert rec.instance_id is not None
        if kr.channel == CHANNEL_SHELL:
            try:
                await asyncio.wait_for(
                    self._backend.run_code(
                        rec.instance_id,
                        kr.context_id,
                        build_reaper(kr.conversation_id),
                        REAP_TIMEOUT_S,
                    ),
                    REAP_TIMEOUT_S + RUN_GRACE_S,
                )
            except InstanceGone:
                raise
            except (TimeoutError, BackendError) as exc:
                logger.warning("leftover processes of %s not reaped: %r", kr.context_id, exc)
        await self._backend.delete_context(rec.instance_id, kr.context_id)

    async def _reap_idle_kernels(self, rec: InstanceRecord, rep: TickReport) -> None:
        """Delete kernels unused for ``kernel_idle_s``; the caller holds the instance lock
        and has checked the instance is running and not busy.

        Does not touch ``last_active_ms``: housekeeping must not keep the instance awake.
        """

        idle_ms = _ms(self._cfg.kernel_idle_s)
        now = self._clock.now_ms()
        idle = [
            (token, kernel)
            for token, kernel in rec.kernels.items()
            if now - kernel.last_used_ms >= idle_ms
        ]
        for token, kr in idle:
            async with self._kernel_locks.try_hold(f"{rec.digest}:{token}") as got:
                if not got or rec.kernels.get(token) is not kr:
                    continue
                rec.kernels.pop(token, None)
                if kr.channel != CHANNEL_SHELL:
                    rec.reaped_tokens.add(token)
                try:
                    await self._retire_kernel(rec, kr)
                except ContextGone as exc:
                    self._record_backend_error("kernel_reap", exc, rec)
                except InstanceGone as exc:
                    self._record_backend_error("kernel_reap", exc, rec)
                    raise
                except BackendError as exc:
                    self._record_backend_error("kernel_reap", exc, rec)
                    rec.stale_contexts.append(kr.context_id)
                rep.kernels_reaped += 1
                self.metrics["kernels_reaped"] += 1

    async def _run_in_kernel(
        self, rec: InstanceRecord, kr: KernelRecord, key: KernelKey, code: str, timeout: float
    ) -> tuple[RunResult, KernelRecord, bool]:
        assert rec.instance_id is not None
        kernel_reset = False
        async with self._kernel_locks.hold(key.lock_key):
            kr = rec.kernels.get(key.token, kr)
            for attempt in range(2):
                try:
                    await self._init_kernel(rec, kr, key)
                    result = await asyncio.wait_for(
                        self._backend.run_code(rec.instance_id, kr.context_id, code, timeout),
                        timeout + RUN_GRACE_S,
                    )
                    return result, kr, kernel_reset
                except ContextGone as exc:
                    self._record_backend_error("run_code", exc, rec)
                    # The context did not exist, so the code never ran: safe to retry.
                    if attempt == 1:
                        raise SandboxError(
                            "kernel_lost", "the kernel disappeared", retryable=True
                        ) from exc
                    context_id = await self._backend.create_context(rec.instance_id, key.language)
                    kr = KernelRecord(
                        context_id,
                        key.language,
                        key.conversation_id,
                        self._clock.now_ms(),
                        key.channel,
                    )
                    kr.needs_init = key.channel == CHANNEL_CODE
                    rec.kernels[key.token] = kr
                    kernel_reset = True
                except TimeoutError:
                    await self._recover_after_timeout(rec, kr, key)
                    result = RunResult(error="timeout", duration_ms=_ms(timeout))
                    return result, rec.kernels.get(key.token, kr), True
        raise AssertionError("unreachable")  # pragma: no cover

    async def _recover_after_timeout(
        self, rec: InstanceRecord, kr: KernelRecord, key: KernelKey
    ) -> None:
        """Level-2 kill: rebuild the kernel (variables lost, files kept)."""

        assert rec.instance_id is not None
        try:
            await self._backend.restart_context(rec.instance_id, kr.context_id)
            # A restarted kernel is back in its default directory.
            kr.needs_init = key.channel == CHANNEL_CODE
        except InstanceGone as exc:
            self._record_backend_error("timeout_recovery", exc, rec)
            raise
        except BackendError as exc:
            self._record_backend_error("timeout_recovery", exc, rec)
            rec.kernels.pop(key.token, None)
            try:
                await self._backend.delete_context(rec.instance_id, kr.context_id)
            except InstanceGone as cleanup_exc:
                self._record_backend_error("timeout_cleanup", cleanup_exc, rec)
                raise
            except BackendError as cleanup_exc:
                self._record_backend_error("timeout_cleanup", cleanup_exc, rec)
                rec.stale_contexts.append(kr.context_id)

    # -------------------------------------------------------- creation / admission

    def _check_create_rate(self, digest: str) -> None:
        now = self._clock.time()
        window = self._cfg.create_window_s
        log = self._create_log.setdefault(digest, deque())
        while log and now - log[0] >= window:
            log.popleft()
        if len(log) >= self._cfg.create_max_per_window:
            self.metrics["rate_limited"] += 1
            raise SandboxError(
                "rate_limited",
                "too many sandbox creations for this end user",
                retryable=True,
                retry_after_s=max(0.0, window - (now - log[0])),
            )
        log.append(now)

    async def _create_instance(self, sandbox: SandboxKey) -> InstanceRecord:
        digest = sandbox.digest
        self._check_create_rate(digest)
        rec = await self._reserve(digest, sandbox.tenant_id)
        spec = InstanceSpec(
            key_digest=digest,
            tenant_id=sandbox.tenant_id,
            template=self._cfg.default_template,
            metadata={META_KEY: digest, META_TENANT: sandbox.tenant_id},
            allow_internet_access=False if self._egress_policy is not None else None,
            network=(
                self._egress_policy.network_config()
                if self._egress_policy is not None
                else None
            ),
        )

        async def op():
            async with self._create_sem:
                return await self._backend.create(spec)

        try:
            handle = await self._with_capacity_retry(op)
        except BackendError as exc:
            self._record_backend_error("create", exc, rec)
            self._registry.remove(rec)
            raise
        except BaseException:
            self._registry.remove(rec)
            raise
        rec.instance_id = handle.instance_id
        rec.created_ms = handle.created_at_ms
        rec.state = STATE_RUNNING
        rec.last_active_ms = self._clock.now_ms()
        if self._egress_policy is not None:
            self.metrics["egress_policy.applied"] += 1
            self.record_operation(
                "egress_policy",
                "applied",
                sandbox,
                instance_id=rec.instance_id,
                details={
                    "mode": "strict",
                    "fingerprint": self._egress_policy.fingerprint,
                    "rule_count": self._egress_policy.rule_count,
                },
            )
        self._record_lifecycle("created", rec, sandbox=sandbox)
        return rec

    async def _reserve(self, digest: str, tenant_id: str) -> InstanceRecord:
        """Admission: take a slot (as a ``creating`` record) or queue, then fail busy.

        The capacity check and ``registry.add`` happen without a suspension point in
        between, so two requests can never take the last slot together.
        """

        cfg = self._cfg
        deadline = self._clock.time() + cfg.queue_wait_s
        queued = False
        try:
            while True:
                if (
                    cfg.tenant_capacity is not None
                    and self._registry.count_tenant(tenant_id) >= cfg.tenant_capacity
                ):
                    await self._evict(1, only_tenant=tenant_id)
                    if self._registry.count_tenant(tenant_id) >= cfg.tenant_capacity:
                        self.metrics["quota_exceeded"] += 1
                        raise SandboxError(
                            "quota_exceeded",
                            "tenant instance quota reached",
                            params={"tenant_id": tenant_id},
                        )
                if self._registry.count() >= cfg.high_water * cfg.capacity:
                    await self._relieve_pressure()
                if self._registry.count() < cfg.capacity:
                    now = self._clock.now_ms()
                    rec = InstanceRecord(digest, tenant_id, STATE_CREATING, now, now)
                    self._registry.add(rec)
                    return rec
                if self._clock.time() >= deadline:
                    raise self._busy_error()
                if not queued:
                    queued = True
                    self._waiting += 1
                await self._clock.sleep(cfg.queue_poll_s)
        finally:
            if queued:
                self._waiting -= 1

    async def _with_capacity_retry[T](self, op: Callable[[], Awaitable[T]]) -> T:
        """Retry ``op`` while the backend itself is out of room (e.g. HTTP 409)."""

        deadline = self._clock.time() + self._cfg.queue_wait_s
        queued = False
        try:
            while True:
                try:
                    return await op()
                except CapacityExhausted:
                    if self._clock.time() >= deadline:
                        raise self._busy_error() from None
                    if not queued:
                        queued = True
                        self._waiting += 1
                    await self._evict(1)
                    await self._clock.sleep(self._cfg.queue_poll_s)
        finally:
            if queued:
                self._waiting -= 1

    def _busy_error(self) -> SandboxError:
        self.metrics["busy"] += 1
        return SandboxError(
            "sandbox_busy",
            "no sandbox capacity right now, retry shortly",
            retryable=True,
            retry_after_s=BUSY_RETRY_AFTER_S,
        )

    # ------------------------------------------------------------------ eviction

    async def _relieve_pressure(self) -> int:
        """Evict from the high watermark down to the low one (rate-limited per call)."""

        cfg = self._cfg
        target = int(cfg.low_water * cfg.capacity)
        need = max(1, min(self._registry.count() - target, cfg.evict_batch_max))
        return await self._evict(need)

    async def _evict(self, need: int, *, only_tenant: str | None = None) -> int:
        """Destroy up to ``need`` long-paused instances; returns how many went."""

        if need <= 0:
            return 0
        cfg = self._cfg
        victims = select_victims(
            self._registry.all(),
            now_ms=self._clock.now_ms(),
            p_min_ms=_ms(cfg.p_min_s),
            need=self._registry.count(),
            soft_quota=cfg.tenant_soft_quota,
            only_tenant=only_tenant,
        )
        done = 0
        for rec in victims:
            if done >= need:
                break
            async with self._locks.try_hold(rec.digest) as got:
                if not got or self._registry.get(rec.digest) is not rec:
                    continue
                # Re-check under the lock: state may have changed while we waited.
                if not is_candidate(rec, self._clock.now_ms(), _ms(cfg.p_min_s)):
                    continue
                try:
                    await self._destroy_locked(rec, tombstone=True, reason="evicted")
                except InstanceGone as exc:
                    self._record_backend_error("evict", exc, rec)
                    self._drop_lost(rec)
                    continue
                except BackendError as exc:
                    self._record_backend_error("evict", exc, rec)
                    logger.warning("eviction of %s failed: %s", rec.digest, exc)
                    continue
            done += 1
            self.metrics["evicted"] += 1
        return done

    async def _tick_one(self, rec: InstanceRecord, rep: TickReport) -> None:
        cfg = self._cfg
        async with self._locks.try_hold(rec.digest) as got:
            if not got or self._registry.get(rec.digest) is not rec:
                return
            if rec.state == STATE_CREATING or rec.busy or rec.instance_id is None:
                return
            now = self._clock.now_ms()
            if now - rec.created_ms >= _ms(cfg.absolute_lifetime_s):
                await self._destroy_locked(rec, tombstone=True, reason="absolute_lifetime")
                rep.lifetime_expired += 1
            elif rec.state == STATE_PAUSED:
                if rec.paused_at_ms is not None and now - rec.paused_at_ms >= _ms(
                    cfg.max_retention_s
                ):
                    await self._destroy_locked(rec, tombstone=True, reason="retention_expired")
                    rep.expired += 1
            elif rec.state == STATE_RUNNING and now - rec.last_active_ms >= _ms(
                cfg.idle_pause_s
            ):
                await self._backend.pause(rec.instance_id)
                rec.state = STATE_PAUSED
                rec.paused_at_ms = now
                rep.paused += 1
                self.metrics["paused"] += 1
                self._record_lifecycle("paused", rec, reason="idle")
            elif rec.state == STATE_RUNNING and cfg.kernel_idle_s > 0:
                await self._reap_idle_kernels(rec, rep)

    async def _destroy_locked(
        self, rec: InstanceRecord, *, tombstone: bool, reason: str
    ) -> None:
        """Destroy on the backend and forget; the caller holds the instance lock."""

        assert rec.instance_id is not None
        try:
            await self._backend.destroy(rec.instance_id)
        except InstanceGone as exc:
            self._record_backend_error("destroy", exc, rec)
        self._registry.remove(rec)
        if tombstone:
            self._registry.set_tombstone(rec.digest, self._clock.now_ms())
        self._record_lifecycle("destroyed", rec, reason=reason)

    def _drop_lost(self, rec: InstanceRecord) -> None:
        """The backend lost the instance: forget it and remember to report a reset."""

        if self._registry.remove(rec):
            self._registry.set_tombstone(rec.digest, self._clock.now_ms())
            self.metrics["lost"] += 1
            self._record_lifecycle("lost", rec)
