"""Profile-cache generation orchestration for the Celery tasks.

The entry point mirrors ``ProfileAnalyticsService.generate_cache``: validate the
workspace once, resolve the workspace LLM once, then generate the insight and the
summary for one user or for every active user of the workspace. Failures are
reported per part instead of aborting the batch.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from src.common.normalization import normalize_language, parse_uuid
from src.infrastructure.model_service import ModelServiceManager, SyncChatModel
from src.infrastructure.neo4j import Neo4jManager
from src.infrastructure.postgres import PostgresManager
from src.modules.memory_insight.generation import (
    refresh_memory_insight,
    validate_workspace_row,
)
from src.modules.shared.model_config import ModelConfigurationError, resolve_chat_model
from src.modules.shared.repositories.end_user import EndUserRepository
from src.modules.shared.repositories.generation_graph import GenerationGraphRepository
from src.modules.shared.repositories.memory_generation import MemoryGenerationRepository
from src.modules.shared.repositories.model_config import ModelConfigRepository
from src.modules.shared.repositories.workspace import WorkspaceRepository
from src.modules.user_summary.generation import generate_user_summary

logger = logging.getLogger(__name__)


def _failed_insight_result(error: str) -> dict[str, Any]:
    """Failure shape shared by the insight entry point and the batch loop."""

    return {
        "success": False,
        "status": "generation_failed",
        "memory_insight": None,
        "behavior_pattern": None,
        "key_findings": None,
        "actionable_findings": [],
        "growth_trajectory": None,
        "error": error,
    }


_EMPTY_SUMMARY_RESULT: dict[str, Any] = {
    "success": False,
    "user_summary": None,
    "personality": None,
    "core_values": None,
    "one_sentence": None,
    "error": None,
}


def _skipped_insight_result() -> dict[str, Any]:
    """Placeholder for the insight half when the caller did not ask for it."""

    return {
        "success": None,
        "status": "skipped",
        "memory_insight": None,
        "behavior_pattern": None,
        "key_findings": None,
        "actionable_findings": [],
        "growth_trajectory": None,
        "error": None,
    }


def _skipped_summary_result() -> dict[str, Any]:
    """Placeholder for the summary half when the caller did not ask for it."""

    return {**_EMPTY_SUMMARY_RESULT, "success": None, "status": "skipped"}


class ChatModelMemo:
    """Resolve the workspace LLM once per task, remembering a failure.

    The model configuration is a workspace-level fact, so a batch over one
    workspace resolves it once instead of once per end user. A failed resolution
    is remembered too, so N users do not retry it N times.
    """

    def __init__(
        self,
        repository: ModelConfigRepository,
        model_service: ModelServiceManager,
        workspace: dict[str, Any],
    ) -> None:
        self._repository = repository
        self._model_service = model_service
        self._workspace = workspace
        self._llm: SyncChatModel | None = None
        self._error: str | None = None

    def chat_model(self) -> SyncChatModel:
        if self._llm is not None:
            return self._llm
        if self._error is not None:
            raise ModelConfigurationError(self._error)
        try:
            self._llm = resolve_chat_model(
                self._repository, self._model_service, self._workspace
            )
        except Exception as exc:
            self._error = str(exc)
            raise
        return self._llm


class ProfileCacheGenerationService:
    """Generate and persist the insight/summary cache columns."""

    def __init__(
        self,
        postgres: PostgresManager,
        neo4j: Neo4jManager,
        model_service: ModelServiceManager,
    ) -> None:
        self.workspace = WorkspaceRepository(postgres)
        self.end_users = EndUserRepository(postgres)
        self.generation = MemoryGenerationRepository(postgres)
        self.graph = GenerationGraphRepository(neo4j)
        self.model_config = ModelConfigRepository(postgres)
        self.model_service = model_service

    def generate_cache(
        self,
        workspace_id: UUID,
        end_user_id: str | None,
        language: str | None,
        refresh_insight: bool = True,
        refresh_summary: bool = True,
    ) -> dict[str, Any]:
        """Generate for one user (``end_user_id`` given) or the whole workspace.

        ``refresh_insight`` / ``refresh_summary`` let the periodic scanner ask for
        only the part its freshness rules allow; a manual trigger sends both.
        """

        language = normalize_language(language)
        workspace_row = self.workspace.workspace_row(workspace_id)
        valid, reason, storage_type = validate_workspace_row(workspace_row)
        if not valid:
            raise ValueError(
                f"workspace {workspace_id} cannot generate cache: {reason} "
                f"(storage_type={storage_type or 'unknown'})"
            )

        resolver = ChatModelMemo(self.model_config, self.model_service, workspace_row)
        callable_workspace_row = dict(workspace_row or {})

        if end_user_id:
            insight_result = (
                self._generate_and_cache_insight(
                    end_user_id, workspace_id, language, resolver, callable_workspace_row
                )
                if refresh_insight
                else _skipped_insight_result()
            )
            summary_result = (
                self._generate_and_cache_summary(
                    end_user_id, workspace_id, language, resolver
                )
                if refresh_summary
                else _skipped_summary_result()
            )
            errors: list[dict[str, Any]] = []
            if refresh_insight and not insight_result["success"]:
                errors.append({"type": "insight", "error": insight_result.get("error")})
            if refresh_summary and not summary_result["success"]:
                errors.append({"type": "summary", "error": summary_result.get("error")})
            # Exactly the API's per-user result shape; the part-level details
            # stay in the logs, not in the payload the caller sees.
            return {
                "end_user_id": end_user_id,
                "insight_success": insight_result["success"] if refresh_insight else None,
                "summary_success": summary_result["success"] if refresh_summary else None,
                "errors": errors,
            }

        user_ids = self.end_users.active_ids_in_workspace(workspace_id)
        successful = 0
        failed = 0
        batch_errors: list[dict[str, Any]] = []
        for user_id in user_ids:
            current_id = str(user_id)
            try:
                insight_result = (
                    self._generate_and_cache_insight(
                        current_id, workspace_id, language, resolver, callable_workspace_row
                    )
                    if refresh_insight
                    else _skipped_insight_result()
                )
                summary_result = (
                    self._generate_and_cache_summary(
                        current_id, workspace_id, language, resolver
                    )
                    if refresh_summary
                    else _skipped_summary_result()
                )
                requested = [
                    bool(result["success"])
                    for result, wanted in (
                        (insight_result, refresh_insight),
                        (summary_result, refresh_summary),
                    )
                    if wanted
                ]
                if requested and all(requested):
                    successful += 1
                else:
                    failed += 1
                    batch_errors.append(
                        {
                            "end_user_id": current_id,
                            "insight_error": insight_result.get("error"),
                            "summary_error": summary_result.get("error"),
                        }
                    )
            except Exception as exc:
                failed += 1
                batch_errors.append({"end_user_id": current_id, "error": str(exc)})
                logger.error(
                    "batch cache generation failed for user: %s",
                    current_id,
                    exc_info=True,
                )
        return {
            "total_users": len(user_ids),
            "successful": successful,
            "failed": failed,
            "errors": batch_errors,
        }

    # ------------------------------------------------------------- internals

    def _generate_and_cache_insight(
        self,
        end_user_id: str,
        workspace_id: UUID,
        language: str,
        resolver: ChatModelMemo,
        workspace_row: dict[str, Any],
    ) -> dict[str, Any]:
        started_at = time.monotonic()
        try:
            # The model is resolved lazily inside the pipeline: only after the
            # source checks say there is something to generate.
            result = refresh_memory_insight(
                self.generation,
                self.graph,
                resolver.chat_model,
                end_user_id,
                workspace_id,
                language,
                workspace_row=workspace_row,
            )
        except Exception as exc:
            logger.exception(
                "generate memory insight failed: end_user_id=%s error=%s",
                end_user_id,
                exc,
            )
            return _failed_insight_result(f"Neo4j或LLM服务不可用: {exc}")
        logger.debug(
            "insight generated user=%s elapsed=%.3fs", end_user_id, time.monotonic() - started_at
        )
        return result

    def _generate_and_cache_summary(
        self,
        end_user_id: str,
        workspace_id: UUID,
        language: str,
        resolver: ChatModelMemo,
    ) -> dict[str, Any]:
        empty = _EMPTY_SUMMARY_RESULT
        user_id = parse_uuid(end_user_id)
        if user_id is None:
            return {**empty, "error": "无效的用户ID格式"}

        try:
            # The access domain owns merge routing and the workspace boundary, so
            # a cross-workspace id can neither be generated nor written here.
            end_user = self.end_users.resolve_user_in_workspace(user_id, workspace_id)
            if end_user is None:
                return {**empty, "error": "用户不存在"}
            llm = resolver.chat_model()
            sections = generate_user_summary(
                self.generation,
                self.graph,
                llm,
                str(end_user["id"]),
                language=language,
                display_name=end_user["other_name"],
            )
            if not any(sections.values()):
                return {
                    **empty,
                    "error": "生成的用户摘要为空,可能Neo4j中没有该用户的数据",
                }
            updated = self.generation.update_user_summary(
                end_user["id"],
                sections["user_summary"],
                sections["personality"],
                sections["core_values"],
                sections["one_sentence"],
                datetime.now(UTC).replace(tzinfo=None),
            )
            if not updated:
                return {**empty, **sections, "error": "数据库更新失败"}
            return {**sections, "success": True, "error": None}
        except Exception as exc:
            logger.error(
                "generate user summary failed: end_user_id=%s error=%s",
                end_user_id,
                exc,
                exc_info=True,
            )
            return {**empty, "error": f"Neo4j或LLM服务不可用: {exc}"}
