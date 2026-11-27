"""The three memory-write entry points, with the API's behaviour.

``POST /memory/write`` (the API-key face) and the two application-service
callbacks (``ingest-workflow`` / ``ingest-agent``) used to run inside the
monolith. This service owns the HTTP side now; the pipelines themselves stay
with the API's workers, so every entry point reproduces the API's own
pre-dispatch logic verbatim and then hands the resulting unit to the dispatch
admission, which publishes the API's Celery task by name.

What is reproduced, in order:

* the end-user rows are resolved with merge routing, and the workspace boundary
  is checked on the *resolved* user (the API's rule);
* the identity confirmation of the API-key face
  (``identity_features`` → confirmed / merge into the existing subject);
* the config and storage type of the workspace;
* ``memory_messages`` is written with the API's seq allocation and row shape;
* the same tasks are dispatched with the same payloads — graph write, fast
  write, and the per-message RAG index write.

Anything the API treats as a caller error (empty messages, a bad role, an
invalid end-user id) is answered the same way, with the same message text, so
a client that switched over sees no difference.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa

from src.common.datetime_utils import ensure_dialog_at, parse_iso_to_utc_naive
from src.common.errors import BizCode, ProfileError
from src.config import ProfileSettings
from src.infrastructure.conversation_activity import ConversationActivity
from src.infrastructure.write_admission import (
    FAST_WRITE_TASK,
    RAG_WRITE_TASK,
    WRITE_MESSAGE_TASK,
    WriteAdmission,
)
from src.models.references.end_user_model import EndUser
from src.repositories.memory_messages_write import (
    WINDOW_SIZE,
    MemoryMessageWriteRepository,
)
from src.runtime import ProfileRuntime

logger = logging.getLogger(__name__)

DEFAULT_STORAGE_TYPE = "neo4j"

#: The three `memory_messages.source` values this service writes.
SOURCE_AGENT = "agent"
SOURCE_SERVICE_API = "service_api"
SOURCE_WORKFLOW = "workflow"

#: Fixed namespace for the content-derived RAG document id. It is part of the
#: wire contract with the API's RAG worker: the same message must derive the
#: same index id from both sides, so it may never be changed here alone.
RAG_MEMORY_NAMESPACE = uuid.UUID("8f3f1a52-6c1e-5b7a-9d4e-2a7c6b1f0e93")

#: Batch-local timestamp step, matching the API's sliding-window and RAG code.
_DIALOG_STEP = timedelta(milliseconds=1)

#: Messages that are structurally unusable for a write (the API's wording).
_MSG_INVALID_END_USER = "终端用户 ID 格式无效"
_MSG_END_USER_NOT_FOUND = "终端用户不存在"
_MSG_WORKSPACE_MISMATCH = "该终端用户不属于当前工作空间"
_MSG_INVALID_WRITE = "写入参数无效"
_MSG_WRITE_FAILED = "写入失败"
_MSG_RAG_DONE = "RAG 写入完成"
_MSG_QUEUED = "已提交 {count} 个写入任务"


@dataclass(frozen=True, slots=True)
class WriteOutcome:
    """What a write entry point answers, in the API's envelope vocabulary."""

    code: int = 0
    msg: str = "OK"
    error: str = ""
    data: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.code == 0


def _is_rag_storage(storage_type: Any) -> bool:
    """Accept both the string and the enum form of a storage type."""

    value = getattr(storage_type, "value", storage_type)
    return str(value or DEFAULT_STORAGE_TYPE).strip().lower() == "rag"


def _normalize_raw_dialog_at(raw: Any) -> str | None:
    """Coerce a message's time field to a string; other types count as absent."""

    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            raw = raw.replace(tzinfo=UTC)
        return raw.isoformat()
    return raw if isinstance(raw, str) else None


def _parse_raw_dialog_at(raw: Any) -> datetime | None:
    """Parse a caller-supplied time; absent or malformed answers ``None``."""

    normalized = _normalize_raw_dialog_at(raw)
    if not normalized or not normalized.strip():
        return None
    try:
        return parse_iso_to_utc_naive(normalized.strip())
    except Exception:  # noqa: BLE001 - an unusable time falls back, never raises
        return None


def _resolve_message_identity(message: Mapping[str, Any], raw_dialog_at: Any) -> tuple[str, str]:
    """Return ``(dialog_at for storage, stable time fragment for the index id)``.

    ``ensure_dialog_at`` substitutes the server clock when the caller sent no
    usable time. Deriving the document id from that would give a replay a new
    id and duplicate the memory, so the message id stands in when the caller's
    time is unusable — the API's rule.
    """

    raw = _normalize_raw_dialog_at(raw_dialog_at)
    dialog_at = ensure_dialog_at(raw)
    if raw and raw.strip():
        try:
            if parse_iso_to_utc_naive(raw.strip()) is not None:
                return dialog_at, dialog_at
        except Exception:  # noqa: BLE001 - fall through to the message id
            pass

    message_id = message.get("original_message_id") or message.get("id")
    if message_id:
        return dialog_at, f"message_id:{message_id}"

    logger.warning(
        "message without a usable time or id; the RAG document id is not replay-stable: "
        "raw=%r",
        raw_dialog_at,
    )
    return dialog_at, dialog_at


def _rag_memory_id(end_user_id: str, id_time: str, role: str, content: str) -> str:
    """Content-derived document id (identical to the API's derivation)."""

    return str(
        uuid.uuid5(RAG_MEMORY_NAMESPACE, f"{end_user_id}:{id_time}:{role}:{content}")
    )


class MemoryWriteService:
    """Serve the three write faces on top of the API's own task contract."""

    def __init__(self, runtime: ProfileRuntime) -> None:
        # Deferred imports keep the port wiring substitutable in tests, the
        # same way the transport handlers compose them.
        from src.core.adapters.read.config import build_config_port, build_end_user_port

        self._runtime = runtime
        self._settings: ProfileSettings = runtime.settings
        self._end_users = build_end_user_port(runtime)
        self._configs = build_config_port(runtime)

    # ── API-key face: POST /memory/write ─────────────────────────────────

    async def write_api(
        self,
        *,
        workspace_id: uuid.UUID,
        tenant_id: uuid.UUID,
        payload: Mapping[str, Any],
        language: str,
    ) -> WriteOutcome:
        """Persist one API-key batch and dispatch its write tasks."""

        try:
            end_user_uuid = uuid.UUID(str(payload.get("end_user_id")))
        except (TypeError, ValueError):
            return WriteOutcome(
                code=BizCode.INVALID_PARAMETER,
                msg=_MSG_INVALID_END_USER,
                error="invalid end_user_id",
            )

        target = await self._end_users.write_target(str(end_user_uuid))
        if target is None:
            return WriteOutcome(
                code=BizCode.USER_NOT_FOUND, msg=_MSG_END_USER_NOT_FOUND, error="end_user not found"
            )
        if str(target["workspace_id"]) != str(workspace_id):
            return WriteOutcome(
                code=BizCode.PERMISSION_DENIED,
                msg=_MSG_WORKSPACE_MISMATCH,
                error="workspace mismatch",
            )
        effective_end_user_id = str(target["id"])

        identity_data: dict[str, Any] | None = None
        identity_features = payload.get("identity_features")
        if isinstance(identity_features, str) and identity_features.strip():
            clean_features = identity_features.strip()
            # Fast path, exactly as the host wrote it: the stored feature string
            # already matches and the subject is confirmed, so nothing changes.
            # Skipping it would take the identity advisory lock and rewrite the
            # same values on every single write that carries a feature string.
            stored_features = target.get("identity_features") or ""
            stored_status = target.get("identity_status") or ""
            if stored_features != clean_features or stored_status != "confirmed":
                effective_end_user_id, identity_status, merged = await self._confirm_identity(
                    workspace_id=workspace_id,
                    end_user_id=uuid.UUID(effective_end_user_id),
                    identity_features=clean_features,
                )
            else:
                identity_status, merged = str(stored_status), False
            identity_data = {
                "end_user_id": effective_end_user_id,
                "identity_status": identity_status,
                "merged": merged,
            }

        # Resolution order mirrors ``write_server_async``: the workspace facts
        # are read *outside* the dispatch guard, so a failure there surfaces as
        # itself (the global handler's business error) instead of being folded
        # into a generic "写入失败" — the API's database block sits outside its
        # try/except for the same reason.
        config_id = await self._configs.active_config_id(workspace_id)
        storage_type = (
            await self._configs.workspace_storage_type(workspace_id)
        ) or DEFAULT_STORAGE_TYPE

        try:
            messages = _message_list(payload.get("messages") or [])

            if _is_rag_storage(storage_type):
                await self._dispatch_rag(
                    messages=_memorisable(messages),
                    end_user_id=effective_end_user_id,
                    config_id=str(config_id) if config_id else "",
                    source=SOURCE_SERVICE_API,
                )
                return WriteOutcome(
                    msg=_MSG_RAG_DONE, data=dict(identity_data) if identity_data else {}
                )

            task_ids = await self._dispatch_api_service(
                messages=messages,
                end_user_id=effective_end_user_id,
                config_id=str(config_id) if config_id else "",
                workspace_id=str(workspace_id),
                tenant_id=tenant_id,
                language=language,
                storage_type=storage_type,
            )
        except ValueError as exc:
            # Empty messages / bad role: a caller problem, never 10001, or the
            # caller retries as if the service were broken.
            logger.warning("memory write rejected by validation: %s", exc)
            return WriteOutcome(
                code=BizCode.INVALID_PARAMETER, msg=_MSG_INVALID_WRITE, error=str(exc)
            )
        except Exception as exc:  # noqa: BLE001 - boundary maps to the host envelope
            logger.exception("memory write failed")
            return WriteOutcome(
                code=BizCode.INTERNAL_ERROR, msg=_MSG_WRITE_FAILED, error=str(exc)
            )

        data: dict[str, Any] = {"task_ids": task_ids}
        if identity_data:
            data.update(identity_data)
        return WriteOutcome(msg=_MSG_QUEUED.format(count=len(task_ids)), data=data)

    # ── application-service callback: ingest-workflow ────────────────────

    async def ingest_workflow(
        self, *, workspace_id: uuid.UUID, tenant_id: uuid.UUID, payload: Mapping[str, Any]
    ) -> None:
        """Persist a workflow node's messages on the conversation channel."""

        resolved = await self._require_end_user(payload.get("end_user_id"), workspace_id)
        end_user_id = resolved or str(payload.get("end_user_id") or "")

        messages = _message_list(payload.get("messages") or [], host_fields=True)
        config_id = str(payload.get("config_id") or "")
        storage_type = str(payload.get("storage_type") or DEFAULT_STORAGE_TYPE)
        language = str(payload.get("language") or "zh")
        conversation_id = str(payload.get("conversation_id") or "")

        if _is_rag_storage(storage_type):
            await self._dispatch_rag(
                messages=_memorisable(messages),
                end_user_id=end_user_id,
                config_id=config_id,
                source=SOURCE_WORKFLOW,
                swallow_rate_limit=True,
            )
            return

        if not conversation_id:
            # The API's workflow node always runs inside a real conversation;
            # an empty id here means the caller violated the contract.
            raise ValueError("conversation_id is required for workflow ingestion")

        async with self._runtime.postgres.session() as session:
            repository = MemoryMessageWriteRepository(session)
            written = await repository.write_batch(
                conversation_id=conversation_id,
                messages=messages,
                end_user_id=end_user_id,
                source=SOURCE_WORKFLOW,
            )
            await session.commit()

        activity = await self._activity()
        await activity.refresh_active(conversation_id)
        await activity.mark_pending(conversation_id)

        await self._dispatch_fast_writes(
            raw_messages=messages,
            written=written,
            storage_type=storage_type,
            end_user_id=end_user_id,
            config_id=config_id,
            workspace_id=str(workspace_id),
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            language=language,
            source=SOURCE_WORKFLOW,
        )
        await self._sliding_window(
            conversation_id=conversation_id,
            config_id=config_id,
            end_user_id=end_user_id,
            workspace_id=str(workspace_id),
            tenant_id=tenant_id,
            language=language,
        )

    # ── application-service callback: ingest-agent ───────────────────────

    async def ingest_agent(
        self, *, workspace_id: uuid.UUID, tenant_id: uuid.UUID, payload: Mapping[str, Any]
    ) -> bool:
        """Persist one agent turn (user + assistant together) and dispatch it.

        The pair is written under one lock and one window scan so both rows get
        consecutive seq values — two separate dispatches race there and can put
        the assistant before its user.
        """

        messages = _message_list(payload.get("messages") or [], host_fields=True)
        if not messages:
            return False

        # The app's memory flag is the caller's decision: application-service
        # only sends this callback for a run whose app has memory enabled, so
        # this service neither re-reads ``app_releases`` nor second-guesses it.
        resolved = await self._require_end_user(payload.get("end_user_id"), workspace_id)
        end_user_id = resolved or str(payload.get("end_user_id") or "")

        config_id = str(payload.get("config_id") or "")
        storage_type = str(payload.get("storage_type") or DEFAULT_STORAGE_TYPE)
        language = str(payload.get("language") or "zh")
        conversation_id = str(payload.get("conversation_id") or "")

        if _is_rag_storage(storage_type):
            await self._dispatch_rag(
                messages=_memorisable(messages),
                end_user_id=end_user_id,
                config_id=config_id,
                source=SOURCE_AGENT,
                swallow_rate_limit=True,
            )
            return bool(_memorisable(messages))

        if not conversation_id:
            raise ValueError("conversation_id is required for agent ingestion")

        async with self._runtime.postgres.session() as session:
            repository = MemoryMessageWriteRepository(session)
            written = await repository.write_batch(
                conversation_id=conversation_id,
                messages=messages,
                end_user_id=end_user_id,
                source=SOURCE_AGENT,
            )
            await session.commit()
        if not written:
            return False

        activity = await self._activity()
        await activity.refresh_active(conversation_id)
        await activity.mark_pending(conversation_id)

        await self._dispatch_fast_writes(
            raw_messages=messages,
            written=written,
            storage_type=storage_type,
            end_user_id=end_user_id,
            config_id=config_id,
            workspace_id=str(workspace_id),
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            language=language,
            source=SOURCE_AGENT,
        )
        await self._sliding_window(
            conversation_id=conversation_id,
            config_id=config_id,
            end_user_id=end_user_id,
            workspace_id=str(workspace_id),
            tenant_id=tenant_id,
            language=language,
        )
        return True

    # ── dispatch: API-style immediate fan-out ────────────────────────────

    async def _dispatch_api_service(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        end_user_id: str,
        config_id: str,
        workspace_id: str,
        tenant_id: uuid.UUID,
        language: str,
        storage_type: str,
    ) -> list[str]:
        """Write the batch, then dispatch one graph task per user message.

        The API gives each user message the surrounding window of the same
        batch as context; assistant-only or user-only batches are legal, which
        is why the window is computed over the indices of user messages rather
        than by pairing neighbours.
        """

        async with self._runtime.postgres.session() as session:
            repository = MemoryMessageWriteRepository(session)
            written = await repository.write_batch(
                conversation_id=None,
                messages=messages,
                end_user_id=end_user_id,
                source=SOURCE_SERVICE_API,
            )
            await session.commit()
        if not written:
            return []

        admission = await self._admission()
        qps = await admission.write_qps(tenant_id)
        user_indices = [index for index, row in enumerate(written) if row["role"] == "user"]

        task_ids: list[str] = []
        for position, index in enumerate(user_indices):
            target = written[index]
            before_start = user_indices[max(0, position - WINDOW_SIZE)]
            context_before = written[before_start:index]

            after_position = min(len(user_indices) - 1, position + WINDOW_SIZE)
            if after_position == position:
                context_after = written[index + 1 :]
            else:
                context_after = written[index + 1 : user_indices[after_position] + 1]

            task_ids.append(
                await self._dispatch_graph_write(
                    admission=admission,
                    qps=qps,
                    end_user_id=end_user_id,
                    target_message=target,
                    context_before=list(context_before),
                    context_after=list(context_after),
                    config_id=config_id,
                    workspace_id=workspace_id,
                    conversation_id="",
                    language=language,
                    skip_cursor_advance=True,
                    source=SOURCE_SERVICE_API,
                )
            )
            # Fast write stays isolated: a failure here never removes the graph
            # task above it from this response.
            await self._dispatch_fast_write(
                admission=admission,
                role=str(target.get("role", "user")),
                should_memorize=True,
                storage_type=storage_type,
                end_user_id=end_user_id,
                target_message=target,
                config_id=config_id,
                workspace_id=workspace_id,
                conversation_id="",
                language=language,
                source=SOURCE_SERVICE_API,
            )
        return task_ids

    async def _dispatch_fast_writes(
        self,
        *,
        raw_messages: Sequence[Mapping[str, Any]],
        written: Sequence[Mapping[str, Any]],
        storage_type: str,
        end_user_id: str,
        config_id: str,
        workspace_id: str,
        conversation_id: str,
        tenant_id: uuid.UUID,
        language: str,
        source: str,
    ) -> None:
        """Fan the batch out to the fast-write task, one row at a time.

        The written rows drop empty content, so they are paired with the raw
        messages that survive the same filter; any mismatch is treated as
        upstream data corruption and the whole loop is abandoned without
        touching the graph dispatch that follows.
        """

        admission = await self._admission()
        persistable = [m for m in raw_messages if str(m.get("content", "") or "").strip()]
        try:
            for raw, row in zip(persistable, written, strict=True):
                await self._dispatch_fast_write(
                    admission=admission,
                    role=str(raw.get("role", "user")),
                    should_memorize=bool(raw.get("should_memorize", True)),
                    storage_type=storage_type,
                    end_user_id=end_user_id,
                    target_message=row,
                    config_id=config_id,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    language=language,
                    source=source,
                )
        except Exception as exc:  # noqa: BLE001 - isolated from the graph dispatch
            logger.warning(
                "fast write fan-out abandoned (graph dispatch unaffected): "
                "conversation=%s end_user=%s batch=%s written=%s error=%s",
                conversation_id,
                end_user_id,
                len(persistable),
                len(written),
                exc,
            )

    async def _dispatch_graph_write(
        self,
        *,
        admission: WriteAdmission,
        qps: float,
        end_user_id: str,
        target_message: Mapping[str, Any],
        context_before: list[Mapping[str, Any]],
        context_after: list[Mapping[str, Any]],
        config_id: str,
        workspace_id: str,
        conversation_id: str,
        language: str,
        skip_cursor_advance: bool,
        source: str,
    ) -> str:
        """Publish one graph write with the API's payload shape."""

        params = {
            "end_user_id": end_user_id,
            "target_message": dict(target_message),
            "context_before": [dict(item) for item in context_before],
            "context_after": [dict(item) for item in context_after],
            "config_id": config_id,
            "workspace_id": workspace_id,
            "conversation_id": conversation_id or "",
            "message_seq": int(target_message["message_seq"]),
            "language": language,
            "dispatch_at": datetime.now(UTC).isoformat(),
            "skip_cursor_advance": skip_cursor_advance,
            "source": str(target_message.get("source") or source),
        }
        return await admission.submit(
            WRITE_MESSAGE_TASK, user_id=end_user_id, params=params, qps=qps
        )

    async def _dispatch_fast_write(
        self,
        *,
        admission: WriteAdmission,
        role: str,
        should_memorize: bool,
        storage_type: str,
        end_user_id: str,
        target_message: Mapping[str, Any],
        config_id: str,
        workspace_id: str,
        conversation_id: str,
        language: str,
        source: str,
    ) -> None:
        """Publish one fast write when the entry point allows it.

        The gate is the API's: RAG spaces never fast-write, and only user
        messages that should be memorised do. The app's memory flag is not read
        here — the caller that produced the batch already applied it. Failures
        are isolated from the caller.
        """

        try:
            if _is_rag_storage(storage_type):
                return
            if role != "user" or not should_memorize:
                return

            params = {
                "end_user_id": end_user_id,
                "target_message": dict(target_message),
                "config_id": config_id,
                "workspace_id": workspace_id,
                "conversation_id": conversation_id or "",
                "message_seq": int(target_message.get("message_seq") or 0),
                "language": language,
                "dispatch_at": datetime.now(UTC).isoformat(),
                "source": source,
            }
            await admission.submit(FAST_WRITE_TASK, user_id=end_user_id, params=params)
        except Exception as exc:  # noqa: BLE001 - fast write never fails the write
            logger.warning(
                "fast write dispatch failed (isolated): end_user=%s source=%s "
                "conversation=%s seq=%s error=%s",
                end_user_id,
                source,
                conversation_id or "-",
                target_message.get("message_seq"),
                exc,
            )

    async def _dispatch_rag(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        end_user_id: str,
        config_id: str,
        source: str,
        swallow_rate_limit: bool = False,
    ) -> list[str]:
        """Publish one RAG index write per message.

        ``swallow_rate_limit`` mirrors the conversation entry points, where a
        refused admission only skips that turn's memory. The API-key face
        leaves it off so a first-message rejection surfaces to the caller
        instead of reporting a write that never happened.
        """

        admission = await self._admission()
        message_ids: list[str] = []
        try:
            payloads = self._build_rag_payloads(
                messages=messages, end_user_id=end_user_id, config_id=config_id, source=source
            )
            for payload in payloads:
                message_ids.append(
                    await admission.submit(
                        RAG_WRITE_TASK, user_id=payload["end_user_id"], params=payload
                    )
                )
        except ValueError as exc:
            # A deterministic construction failure (a malformed end-user id):
            # the API skips that batch's memory rather than failing the run.
            logger.warning(
                "RAG dispatch skipped (invalid parameters): end_user=%s source=%s error=%s",
                end_user_id,
                source,
                exc,
            )
        except Exception as exc:  # noqa: BLE001 - mirrors the API's isolation rules
            if not swallow_rate_limit and not message_ids:
                # Not one message was admitted: reporting "RAG 写入完成" would
                # claim a write that never happened. A partial rejection is a
                # different case below — what did enqueue has taken effect.
                raise
            logger.warning(
                "RAG dispatch refused; skipping the remaining messages: end_user=%s "
                "source=%s enqueued=%s error=%s",
                end_user_id,
                source,
                len(message_ids),
                exc,
            )
        return message_ids

    @staticmethod
    def _build_rag_payloads(
        *,
        messages: Sequence[Mapping[str, Any]],
        end_user_id: str,
        config_id: str,
        source: str,
    ) -> list[dict[str, Any]]:
        """Assemble the RAG document fields (the worker only adds the vector)."""

        try:
            uuid.UUID(str(end_user_id))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid end_user_id for rag dispatch: {end_user_id!r}") from exc

        payloads: list[dict[str, Any]] = []
        previous_dialog: datetime | None = None
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            raw_dialog_at = message.get("dialog_at") or message.get("created_at")

            text = str(content or "").strip()
            if not text:
                continue

            dialog_at, id_time = _resolve_message_identity(message, raw_dialog_at)
            dialog_naive = parse_iso_to_utc_naive(dialog_at)
            raw_naive = _parse_raw_dialog_at(raw_dialog_at)
            # Only a caller-supplied time advances the monotonic baseline: a
            # server-side fallback says nothing about the real conversation
            # order and would push every later message past "now".
            if raw_naive is not None and dialog_naive is not None:
                if previous_dialog is not None and dialog_naive <= previous_dialog:
                    dialog_naive = previous_dialog + _DIALOG_STEP
                    dialog_at = dialog_naive.replace(tzinfo=UTC).isoformat()
                previous_dialog = dialog_naive
            role_value = str(role or "user").strip().lower()
            payloads.append(
                {
                    "id": _rag_memory_id(end_user_id, id_time, role_value, text),
                    "end_user_id": end_user_id,
                    "config_id": config_id,
                    "role": role_value,
                    "content": text,
                    "source": source,
                    "dialog_at": dialog_at,
                }
            )
        return payloads

    # ── dispatch: sliding window (agent / workflow) ──────────────────────

    async def _sliding_window(
        self,
        *,
        conversation_id: str,
        config_id: str,
        end_user_id: str,
        workspace_id: str,
        tenant_id: uuid.UUID,
        language: str,
    ) -> None:
        """Dispatch at most one pending user message when its window is full.

        The cursor is advanced before the publish, so a concurrent flush or a
        later ingest can never dispatch the same message twice; the partial
        unique update is what makes that safe.
        """

        from bisect import bisect_right

        async with self._runtime.postgres.session() as session:
            repository = MemoryMessageWriteRepository(session)
            cursor = await repository.write_cursor(conversation_id) or 0
            pending = await repository.pending_messages(conversation_id, cursor)
            user_seqs = await repository.user_seqs(conversation_id)
        if not pending:
            return

        for message in pending:
            if message.get("role") != "user" or not message.get("should_memorize", True):
                continue

            target_seq = int(message["message_seq"])
            downstream = len(user_seqs) - bisect_right(user_seqs, target_seq)
            if downstream < WINDOW_SIZE:
                logger.info(
                    "sliding window not full yet: conversation=%s seq=%s downstream=%s < %s",
                    conversation_id,
                    target_seq,
                    downstream,
                    WINDOW_SIZE,
                )
                return

            async with self._runtime.postgres.session() as session:
                repository = MemoryMessageWriteRepository(session)
                group = await repository.seq_group(conversation_id, target_seq)
            if not group:
                return
            if len(group) > 1:
                logger.warning(
                    "duplicate seq rows found; dispatching the whole group row by row: "
                    "conversation=%s seq=%s rows=%s",
                    conversation_id,
                    target_seq,
                    len(group),
                )

            dispatchable = [
                row
                for row in group
                if row["role"] == "user" and row.get("should_memorize", True)
            ]
            if not dispatchable:
                # Nothing to dispatch in this group (assistant-only): the caller
                # leaves the cursor for the flush path, exactly like the API.
                return

            async with self._runtime.postgres.session() as session:
                repository = MemoryMessageWriteRepository(session)
                acquired = await repository.advance_write_cursor(conversation_id, target_seq)
                await session.commit()
            if not acquired:
                logger.info(
                    "cursor already advanced; skipping duplicate dispatch: "
                    "conversation=%s seq=%s",
                    conversation_id,
                    target_seq,
                )
                return

            async with self._runtime.postgres.session() as session:
                repository = MemoryMessageWriteRepository(session)
                context_before = await repository.context_before(conversation_id, target_seq)
                context_after = await repository.context_after(conversation_id, target_seq)

            admission = await self._admission()
            qps = await admission.write_qps(tenant_id)
            first_user_row = True
            for row_index, row in enumerate(group):
                if row["role"] != "user" or not row.get("should_memorize", True):
                    continue

                # Rows of this group that were stored earlier join the batch's
                # own context, so a collided seq still keeps its 1, 2, 3 order.
                target = dict(row)
                if first_user_row:
                    first_user_row = False
                else:
                    # A row-level suffix turns every extra row of the group into
                    # its own dialogue node instead of being overwritten by the
                    # canonical ``Dialog_<conversation>_<seq>`` id downstream.
                    target["_dialogue_suffix"] = (
                        "m" + str(row["memory_message_id"]).replace("-", "")[:16]
                    )

                await self._dispatch_graph_write(
                    admission=admission,
                    qps=qps,
                    end_user_id=end_user_id,
                    target_message=target,
                    context_before=[*context_before, *group[:row_index]],
                    context_after=context_after,
                    config_id=config_id,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    language=language,
                    skip_cursor_advance=False,
                    source=str(row.get("source") or SOURCE_AGENT),
                )
            # One seq per call: the next window is decided by the next ingest.
            return

    # ── identity confirmation (API-key face) ─────────────────────────────

    async def _confirm_identity(
        self, *, workspace_id: uuid.UUID, end_user_id: uuid.UUID, identity_features: str
    ) -> tuple[str, str, bool] | None:
        """Confirm the caller's subject and merge it into an existing one.

        Ordering follows the API: the identity columns are stamped on the
        current row *before* the merge, so a source that is about to be
        soft-deleted keeps the feature string that answers "which subject was
        this moved under". The advisory lock cannot span the merge either way —
        the merged transaction commits repeatedly and releases it — exactly as
        the API documents on its own identity lock.
        """

        clean = identity_features.strip()

        async with self._runtime.postgres.session() as session:
            await session.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"eu_identity:{workspace_id}|{clean}"},
            )
            candidate_id = await _find_active_by_identity(session, workspace_id, clean)
            if candidate_id is None or candidate_id == end_user_id:
                await _stamp_identity(session, end_user_id, clean, "confirmed")
                await session.commit()
                return str(end_user_id), "confirmed", False

            await _stamp_identity(session, end_user_id, clean, "confirmed")
            await session.commit()

        await self._merge_end_users(source=end_user_id, target=candidate_id)
        return str(candidate_id), "confirmed", True

    async def _merge_end_users(self, *, source: uuid.UUID, target: uuid.UUID) -> None:
        """Run the ported merge (PG intent, graph, RAG, metadata, reflection)."""

        from src.infrastructure.forget_candidates import ForgetCandidateRefresher
        from src.infrastructure.graph.end_user_merge import build_graph_merge
        from src.infrastructure.graph.memory_count import build_graph_memory_count
        from src.infrastructure.metadata_migration_llm import LlmMetadataMigrator
        from src.infrastructure.rag.end_user_reassign import (
            build_rag_memory_count,
            build_rag_merge,
        )
        from src.infrastructure.scene_names import build_scene_name_resolver
        from src.services.end_user_merge import EndUserMergeService

        service = EndUserMergeService(
            postgres=self._runtime.postgres,
            settings=self._runtime.settings,
            # The API's reflection task owns the queue, and its declaration has
            # no priority argument, so the API-facing producer is the only one
            # that can publish to it.
            task_bridge=self._runtime.api_tasks,
            graph_merge=build_graph_merge(self._runtime.postgres, self._runtime.neo4j),
            rag_merge=build_rag_merge(self._runtime.elasticsearch),
            graph_memory_count=build_graph_memory_count(self._runtime.neo4j),
            rag_memory_count=build_rag_memory_count(self._runtime.elasticsearch),
            migrate_metadata=LlmMetadataMigrator(
                postgres=self._runtime.postgres,
                model_service=self._runtime.model_service,
                settings=self._runtime.settings,
            ),
            scene_names=build_scene_name_resolver(self._runtime.postgres),
            refresh_forget_candidates=ForgetCandidateRefresher(
                postgres=self._runtime.postgres,
                neo4j=self._runtime.neo4j,
                redis_manager=self._runtime.redis,
                limits=self._runtime.quota.effective_limits,
            ),
        )
        await service.merge({source}, target)

    # ── shared helpers ───────────────────────────────────────────────────

    async def _require_end_user(
        self, raw_end_user_id: Any, workspace_id: uuid.UUID
    ) -> str | None:
        """Resolve an end user against the workspace, routing merges.

        The API resolved the row before dispatch and rewrote the id, so a
        merged-away id writes to its target instead of creating an orphan. An
        id that resolves nowhere is a caller error there (the validation helper
        raises), so it raises here too; an *absent* id is legal for the agent
        callback and stays ``None``.
        """

        end_user_id = str(raw_end_user_id or "")
        if not end_user_id:
            return None
        reference = await self._end_users.resolve_in_workspace(end_user_id, workspace_id)
        if reference is not None:
            return str(reference.end_user_id)
        target = await self._end_users.write_target(end_user_id)
        if target is None:
            raise ProfileError(
                BizCode.USER_NOT_FOUND, _MSG_END_USER_NOT_FOUND, detail="end_user not found"
            )
        return str(target["id"])

    async def _admission(self) -> WriteAdmission:
        return WriteAdmission(
            redis=await self._runtime.redis.client(),
            task_bridge=self._runtime.task_bridge,
            entitlements=self._runtime.quota,
        )

    async def _activity(self) -> ConversationActivity:
        return ConversationActivity(await self._runtime.redis.client())


# ── module helpers ───────────────────────────────────────────────────────


def _message_list(
    raw_messages: Sequence[Any], *, host_fields: bool = False
) -> list[dict[str, Any]]:
    """Validate and normalize the caller's messages (the API's rules).

    Empty messages, an unknown role, or blank content is a caller error with a
    specific message; both write faces answer it as an invalid parameter.

    ``host_fields`` is on for the application-service callbacks only. The
    API-key face's own request model carries just role/content/time/files, and
    the host ignored anything else a caller sent, so honouring
    ``should_memorize`` there would change what a client already in production
    means.
    """

    if not raw_messages:
        raise ValueError("Message list cannot be empty")

    normalized: list[dict[str, Any]] = []
    for index, message in enumerate(raw_messages):
        role = str(message.get("role") or "")
        content = message.get("content") or ""
        if role not in ("user", "assistant"):
            raise ValueError(
                f"Role must be 'user' or 'assistant', got: {role}. Message index: {index}"
            )
        if not str(content).strip():
            raise ValueError(
                f"Message content cannot be empty. Message index: {index}, role: {role}"
            )
        item: dict[str, Any] = {"role": role, "content": content}
        if message.get("dialog_at"):
            item["dialog_at"] = message["dialog_at"]
        files = message.get("files")
        if host_fields and not files and isinstance(message.get("meta_data"), dict):
            # The agent path carried attachments inside the host's ``meta_data``;
            # the API's dispatcher reads them from there for the same reason.
            files = message["meta_data"].get("files")
        if files:
            item["files"] = list(files)
        if host_fields:
            created_at = _parse_raw_dialog_at(message.get("created_at"))
            if created_at is not None:
                # The row writer forces monotonic, strictly increasing
                # timestamps inside one batch, which needs a datetime — not the
                # wire string.
                item["created_at"] = created_at
            if "should_memorize" in message:
                item["should_memorize"] = bool(message["should_memorize"])
            if message.get("id"):
                item["original_message_id"] = message["id"]
        normalized.append(item)
    return normalized


def _memorisable(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The rows a RAG write is allowed to index (both entry-point rules)."""

    return [
        dict(message)
        for message in messages
        if bool(message.get("should_memorize", True))
        and str(message.get("content", "") or "").strip()
    ]


async def _find_active_by_identity(
    session: Any, workspace_id: uuid.UUID, identity_features: str
) -> uuid.UUID | None:
    """The oldest active subject carrying this feature string, if any."""

    statement = (
        sa.select(EndUser.id)
        .where(
            EndUser.workspace_id == workspace_id,
            EndUser.identity_features == identity_features,
            EndUser.is_active.is_(True),
        )
        .order_by(EndUser.created_at.asc())
        .limit(1)
    )
    value = (await session.execute(statement)).scalar_one_or_none()
    return uuid.UUID(str(value)) if value is not None else None


async def _stamp_identity(
    session: Any, end_user_id: uuid.UUID, identity_features: str, status: str
) -> None:
    """Set the identity columns on one row."""

    await session.execute(
        sa.update(EndUser)
        .where(EndUser.id == end_user_id)
        .values(identity_features=identity_features, identity_status=status)
    )


__all__ = [
    "MemoryWriteService",
    "WriteOutcome",
    "DEFAULT_STORAGE_TYPE",
    "SOURCE_AGENT",
    "SOURCE_SERVICE_API",
    "SOURCE_WORKFLOW",
]
