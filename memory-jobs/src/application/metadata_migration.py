"""Application use cases for the one-time legacy metadata migration."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict
from task_bridge.metadata_migration import (
    apply_migration_response_operations,
    legacy_metadata_input,
    metadata_write_column,
    new_metadata,
)

from src.application.dto import MetadataMigrationResult, MetadataMigrationScanReport
from src.application.ports.llm import ChatModelFactory
from src.application.ports.lock import (
    InflightLock,
    MetadataMigrationCircuitBreaker,
    MetadataMigrationLock,
)
from src.application.ports.prompts import PromptCatalog
from src.application.ports.publishing import UnitPublisher
from src.application.ports.uow import UnitOfWork
from src.domain.model_config.services import ModelResolutionService
from src.domain.profile.read_models import MetadataMigrationCursor
from src.domain.shared.errors import ModelConfigurationError, WorkspaceNotEligible
from src.domain.shared.ids import EndUserId, WorkspaceId, parse_uuid
from src.domain.workspace.model import WorkspaceStatus

logger = logging.getLogger(__name__)

_MAX_SCAN_PAGES = 10


class MigrationOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: str
    field: str
    value: str


class MigrationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_id: str
    operations: list[MigrationOperation]
    dropped_ops_count: Literal[0]


class MigrationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[MigrationItem]


def apply_migration_response(existing: object, raw: object, user_id: str):
    response = raw if isinstance(raw, MigrationResponse) else MigrationResponse.model_validate(raw)
    if len(response.items) != 1:
        raise ValueError("migration response must contain exactly one user")
    return apply_migration_response_operations(
        existing,
        [operation.model_dump() for operation in response.items[0].operations],
        response.items[0].entity_id,
        user_id,
    )


@dataclass(frozen=True, slots=True)
class MetadataMigrationCommand:
    end_user_id: str
    workspace_id: str
    attempt_token: str
    language: str = "zh"


class MetadataMigrationUseCase:
    def __init__(
        self,
        *,
        uow: UnitOfWork,
        chat_models: ChatModelFactory,
        prompts: PromptCatalog,
        lock: MetadataMigrationLock,
        circuit_breaker: MetadataMigrationCircuitBreaker,
        enabled: bool = False,
    ) -> None:
        self._enabled = enabled
        self._uow = uow
        self._chat_models = chat_models
        self._prompts = prompts
        self._lock = lock
        self._circuit_breaker = circuit_breaker
        self._model_resolution = ModelResolutionService()

    def execute(self, command: MetadataMigrationCommand) -> MetadataMigrationResult:
        started = time.monotonic()
        fields = {
            "event": "metadata_migration_user_result",
            "workspace_id": command.workspace_id,
        }
        try:
            result = self._execute(command)
        except Exception as exc:
            # Celery emits the traceback. Keep the searchable outcome concise.
            logger.warning(
                "历史 metadata 单用户迁移失败 end_user_id=%s status=failed elapsed_seconds=%.3f",
                command.end_user_id,
                time.monotonic() - started,
                extra={**fields, "exception_type": type(exc).__name__},
            )
            raise
        logger.info(
            "历史 metadata 单用户迁移结果 end_user_id=%s status=%s reason=%s "
            "operations_applied=%d operations_rejected=%d elapsed_seconds=%.3f",
            result.end_user_id,
            result.status,
            result.reason or "-",
            result.operations_applied,
            result.operations_rejected,
            time.monotonic() - started,
            extra=fields,
        )
        return result

    def _execute(self, command: MetadataMigrationCommand) -> MetadataMigrationResult:
        if not self._enabled:
            return MetadataMigrationResult(
                status="skipped",
                end_user_id=command.end_user_id,
                reason="disabled",
            )
        user_uuid = parse_uuid(command.end_user_id)
        workspace_uuid = parse_uuid(command.workspace_id)
        if user_uuid is None or workspace_uuid is None:
            raise ValueError("end_user_id and workspace_id must be UUIDs")
        user_id, workspace_id = EndUserId(user_uuid), WorkspaceId(workspace_uuid)
        if not self._lock.acquire(str(user_id), command.attempt_token):
            return MetadataMigrationResult(
                status="deferred",
                end_user_id=str(user_id),
                workspace_id=str(workspace_id),
                reason="lock_unavailable",
            )
        try:
            return self._execute_locked(user_id, workspace_id, command.language)
        except Exception as exc:
            if _is_workspace_model_unavailable(exc):
                self._circuit_breaker.open(str(workspace_id))
            raise
        finally:
            self._lock.release(str(user_id), command.attempt_token)

    def _execute_locked(
        self, user_id: EndUserId, workspace_id: WorkspaceId, language: str
    ) -> MetadataMigrationResult:
        with self._uow.read() as repositories:
            candidate = repositories.metadata_migration.get_migration_candidate(
                user_id, workspace_id
            )
        if candidate is None:
            return MetadataMigrationResult(
                status="skipped",
                end_user_id=str(user_id),
                workspace_id=str(workspace_id),
                reason="not_active_or_not_found",
            )
        if candidate.meta_data_new is not None:
            return MetadataMigrationResult(
                status="already_migrated", end_user_id=str(user_id), workspace_id=str(workspace_id)
            )
        if metadata_write_column(candidate.created_at, None) == "meta_data_new":
            return MetadataMigrationResult(
                status="skipped",
                end_user_id=str(user_id),
                workspace_id=str(workspace_id),
                reason="new_user",
            )
        if not candidate.has_info_row:
            return MetadataMigrationResult(
                status="deferred",
                end_user_id=str(user_id),
                workspace_id=str(workspace_id),
                reason="missing_info_row",
            )
        if candidate.meta_data is not None and not isinstance(candidate.meta_data, Mapping):
            raise ValueError("legacy metadata must be an object")
        existing = candidate.meta_data if candidate.meta_data is not None else {}
        legacy = legacy_metadata_input(existing)
        if not legacy:
            return self._commit(
                user_id,
                workspace_id,
                candidate.meta_data,
                new_metadata(existing),
                0,
                0,
            )
        with self._uow.read() as repositories:
            workspace = repositories.workspaces.get(workspace_id)
            if workspace is None:
                raise WorkspaceNotEligible(workspace_id, "workspace_not_found")
            if workspace.status is not WorkspaceStatus.ACTIVE:
                raise WorkspaceNotEligible(workspace_id, "workspace_inactive")
            config_id = workspace.memory_config
            if config_id is None:
                raise ModelConfigurationError("workspace has no memory config")
            config_row = repositories.model_configs.resolve_model(
                config_id=config_id,
                slot="llm",
                is_default_config=workspace.is_default_config,
                workspace_model_id=workspace.models.llm.value if workspace.models.llm else None,
            )
        resolved = self._model_resolution.resolve(workspace, config_row)
        model = self._chat_models.chat_model(resolved.config_id, resolved.tenant_id)
        prompt = self._prompts.metadata_migration(
            entity_id=str(user_id),
            language=language,
            input_json={"entity_id": str(user_id), **legacy},
        )
        raw = model.call_structured([{"role": "user", "content": prompt}], MigrationResponse)
        merged, applied, rejected = apply_migration_response(existing, raw, str(user_id))
        return self._commit(user_id, workspace_id, candidate.meta_data, merged, applied, rejected)

    def _commit(
        self,
        user_id: EndUserId,
        workspace_id: WorkspaceId,
        original: object,
        merged: dict[str, object],
        applied: int,
        rejected: int,
    ) -> MetadataMigrationResult:
        self._lock.ensure_valid()
        with self._uow.transaction() as repositories:
            if repositories.metadata_migration.cas_write_metadata_new(
                end_user_id=user_id, original_meta_data=original, merged_metadata=merged
            ):
                return MetadataMigrationResult(
                    status="migrated",
                    end_user_id=str(user_id),
                    workspace_id=str(workspace_id),
                    operations_applied=applied,
                    operations_rejected=rejected,
                )
        with self._uow.read() as repositories:
            latest = repositories.metadata_migration.get_migration_candidate(user_id, workspace_id)
        if latest is not None and latest.meta_data_new is not None:
            return MetadataMigrationResult(
                status="already_migrated", end_user_id=str(user_id), workspace_id=str(workspace_id)
            )
        return MetadataMigrationResult(
            status="deferred",
            end_user_id=str(user_id),
            workspace_id=str(workspace_id),
            reason="metadata_changed",
        )


_MODEL_UNAVAILABLE_MARKERS = (
    "AUTH_INVALID_API_KEY",
    "API Key 无效或已禁用",
    "NO_AVAILABLE_CHANNEL",
    "no available channel",
    "model config is inactive",
    "model config was not found",
    "无可用渠道",
    "模型配置当前无可用渠道凭据",
)


def _is_workspace_model_unavailable(exc: BaseException) -> bool:
    """Classify only workspace-wide model configuration/auth failures."""

    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ModelConfigurationError):
            return True
        status = getattr(current, "http_status", None)
        if status in (401, 403):
            return True
        code = getattr(current, "code", None)
        if code in (4003, 4012, "4003", "4012"):
            return True
        text = str(current)
        if any(marker in text for marker in _MODEL_UNAVAILABLE_MARKERS):
            return True
        for wrapped in (current.__cause__, current.__context__):
            if isinstance(wrapped, BaseException):
                pending.append(wrapped)
    return False


class ScanMetadataMigrationUseCase:
    def __init__(
        self,
        *,
        uow: UnitOfWork,
        lock: InflightLock,
        circuit_breaker: MetadataMigrationCircuitBreaker,
        publisher: UnitPublisher,
        page_size: int,
        cutoff: datetime,
        enabled: bool = False,
    ) -> None:
        self._uow, self._lock, self._circuit_breaker, self._publisher = (
            uow, lock, circuit_breaker, publisher
        )
        self._page_size, self._cutoff = page_size, cutoff
        self._enabled = enabled

    def execute(self) -> MetadataMigrationScanReport:
        if not self._enabled:
            logger.info(
                "历史 metadata 迁移扫描未启用",
                extra={"event": "metadata_migration_scan_skipped"},
            )
            return MetadataMigrationScanReport()
        started = time.monotonic()
        dispatched = skipped_inflight = skipped_circuit_breaker = failed = page_failures = 0
        excluded_workspaces: set[WorkspaceId] = set()
        seen_users: set[EndUserId] = set()
        cursor: MetadataMigrationCursor | None = None
        remaining: int | None = None
        pages_read = candidates_read = 0
        stop_reason = "page_limit"
        for page in range(_MAX_SCAN_PAGES):
            try:
                with self._uow.read() as repositories:
                    repository = repositories.metadata_migration
                    if page == 0:
                        remaining = repository.remaining_count(cutoff=self._cutoff)
                        if remaining == 0:
                            stop_reason = "complete"
                            break
                    candidates, cursor = repository.migration_candidates(
                        cutoff=self._cutoff,
                        after=cursor,
                        limit=self._page_size,
                        excluded_workspaces=frozenset(excluded_workspaces),
                    )
            except Exception:
                page_failures += 1
                stop_reason = "page_read_failed"
                logger.exception(
                    "历史 metadata 迁移扫描读页失败 page=%s",
                    page + 1,
                    extra={"event": "metadata_migration_scan_page_failed"},
                )
                break
            pages_read += 1
            candidates_read += len(candidates)
            if page == 0:
                logger.info(
                    "metadata 迁移待处理用户数=%s，首批候选数=%s",
                    remaining,
                    len(candidates),
                    extra={"event": "metadata_migration_scan_started"},
                )
            for candidate in candidates:
                if dispatched >= self._page_size:
                    break
                if candidate.end_user_id in seen_users:
                    continue
                seen_users.add(candidate.end_user_id)
                try:
                    if (
                        candidate.workspace_id in excluded_workspaces
                        or self._circuit_breaker.is_open(str(candidate.workspace_id))
                    ):
                        excluded_workspaces.add(candidate.workspace_id)
                        skipped_circuit_breaker += 1
                        continue
                    if self._lock.is_locked(str(candidate.end_user_id)):
                        skipped_inflight += 1
                        continue
                    self._publisher.publish_metadata_migration(
                        end_user_id=str(candidate.end_user_id),
                        workspace_id=str(candidate.workspace_id),
                        language="zh",
                        priority="background",
                        dispatch_index=dispatched,
                    )
                    dispatched += 1
                except Exception:
                    failed += 1
                    logger.exception(
                        "历史 metadata 迁移派发失败 end_user_id=%s",
                        candidate.end_user_id,
                        extra={
                            "event": "metadata_migration_dispatch_failed",
                            "workspace_id": str(candidate.workspace_id),
                        },
                    )
            if dispatched >= self._page_size:
                stop_reason = "dispatch_limit"
                break
            if cursor is None:
                stop_reason = "candidates_exhausted"
                break
        report = MetadataMigrationScanReport(
            dispatched=dispatched,
            skipped_inflight=skipped_inflight,
            skipped_circuit_breaker=skipped_circuit_breaker,
            failed=failed,
            page_failures=page_failures,
            elapsed_seconds=time.monotonic() - started,
        )
        message = (
            "迁移完成：全部有效历史用户 metadata 已迁移，剩余用户数=0"
            if stop_reason == "complete"
            else "历史 metadata 迁移扫描汇总"
        )
        logger.log(
            logging.WARNING if report.failed or report.page_failures else logging.INFO,
            "%s remaining_at_start=%s pages_read=%d candidates_read=%d dispatch_limit=%d "
            "dispatched=%d skipped_circuit_breaker=%d skipped_inflight=%d failed=%d "
            "page_failures=%d stop_reason=%s elapsed_seconds=%.3f",
            message,
            remaining if remaining is not None else "unknown",
            pages_read,
            candidates_read,
            self._page_size,
            report.dispatched,
            report.skipped_circuit_breaker,
            report.skipped_inflight,
            report.failed,
            report.page_failures,
            stop_reason,
            report.elapsed_seconds,
            extra={"event": "metadata_migration_scan_summary"},
        )
        return report
