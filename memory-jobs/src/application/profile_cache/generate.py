"""Generate and persist one user's insight and summary cache columns.

The workspace and model configuration are read once in a short scope. Insight
and summary then run as separate aggregate writes (two transactions), which is
the service's existing "partial success" semantics. Every failure is reported per
part; only infrastructure failures reach Celery.

The per-user in-flight guard also lives here: only one refresh per user runs at
a time, so a redelivered unit and an online request cannot both generate.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from src.application.ports.chat_model import ChatModel
from src.application.ports.clock import Clock
from src.application.ports.llm import ChatModelFactory
from src.application.ports.lock import InflightLock
from src.application.ports.prompts import PromptCatalog
from src.application.ports.scene_names import SceneNameLookup
from src.application.ports.uow import UnitOfWork
from src.application.profile_cache.generators import (
    SUMMARY_ENTITY_QUERY_LIMIT,
    SUMMARY_STATEMENT_QUERY_LIMIT,
    MemoryInsightGenerator,
    UserSummaryGenerator,
)
from src.application.profile_cache.insight_input import (
    MEMORY_INSIGHT_MIN_ENTRIES,
    MEMORY_INSIGHT_STATEMENT_LIMIT,
    build_memory_insight_metadata_input,
    build_memory_insight_statement_input,
    count_memory_insight_entries,
)
from src.application.profile_cache.scene_names import load_scene_names
from src.application.profile_cache.workspace_guard import (
    ensure_workspace_can_generate,
)
from src.domain.end_user.model import EndUser
from src.domain.end_user.services import UserResolutionService
from src.domain.model_config.model import ResolvedChatModel
from src.domain.model_config.services import ModelResolutionService
from src.domain.profile.model import (
    InsightContent,
    MemoryInsight,
    UserSummary,
    WriteOutcome,
)
from src.domain.profile.read_models import InsightSource
from src.domain.profile.repository import (
    InsightStatementReader,
    SummarySourceReader,
)
from src.domain.shared.errors import ModelConfigurationError
from src.domain.shared.ids import EndUserId, WorkspaceId, parse_uuid
from src.domain.shared.language import Language
from src.domain.workspace.model import Workspace

logger = logging.getLogger(__name__)

def _canonical_end_user_id(raw: str) -> str:
    """The lock-key shape the scanner and the fan-out already use."""

    parsed = parse_uuid(raw)
    return raw if parsed is None else str(parsed)


INSUFFICIENT_MEMORY_INSIGHT_TEXT = "该用户记忆不足，暂无法生成有效洞察。"
INSUFFICIENT_MEMORY_INSIGHT_TEXT_EN = (
    "There is insufficient user memory to generate a meaningful insight."
)


@dataclass(frozen=True, slots=True)
class GenerateProfileCacheCommand:
    end_user_id: str
    workspace_id: WorkspaceId
    #: Opaque identity of this attempt for the in-flight lock. A redelivered
    #: execution keeps its token and therefore keeps the lock it already holds.
    lock_owner: str
    language: str = "zh"
    refresh_insight: bool = True
    refresh_summary: bool = True


@dataclass(frozen=True, slots=True)
class PartResult:
    success: bool | None
    error: str | None = None
    status: str | None = None


@dataclass(frozen=True, slots=True)
class ProfileCacheResult:
    end_user_id: str
    workspace_id: str
    insight_success: bool | None
    summary_success: bool | None
    errors: list[dict[str, Any]]
    #: Set when the refresh never ran; ``None`` means it ran (possibly failing).
    skipped_reason: str | None = None

    @classmethod
    def inflight_skipped(
        cls, *, end_user_id: str, workspace_id: str
    ) -> ProfileCacheResult:
        """Another refresh already holds this user's lock."""

        return cls(
            end_user_id=end_user_id,
            workspace_id=workspace_id,
            insight_success=None,
            summary_success=None,
            errors=[],
            skipped_reason="inflight",
        )

    def to_payload(self) -> dict[str, Any]:
        if self.skipped_reason is not None:
            return {
                "status": "skipped",
                "reason": self.skipped_reason,
                "end_user_id": self.end_user_id,
                "workspace_id": self.workspace_id,
            }
        return {
            "end_user_id": self.end_user_id,
            "insight_success": self.insight_success,
            "summary_success": self.summary_success,
            "errors": self.errors,
        }


class ChatModelMemo:
    """Resolve the workspace LLM once per use case, remembering a failure."""

    def __init__(
        self, factory: ChatModelFactory, resolved: ResolvedChatModel
    ) -> None:
        self._factory = factory
        self._resolved = resolved
        self._llm: ChatModel | None = None
        self._error: str | None = None

    def chat_model(self) -> ChatModel:
        if self._llm is not None:
            return self._llm
        if self._error is not None:
            raise ModelConfigurationError(self._error)
        try:
            self._llm = self._factory.chat_model(
                self._resolved.config_id, self._resolved.tenant_id
            )
        except Exception as exc:
            self._error = str(exc)
            raise
        return self._llm


class GenerateProfileCacheUseCase:
    def __init__(
        self,
        *,
        uow: UnitOfWork,
        insight_statements: InsightStatementReader,
        summary_sources: SummarySourceReader,
        chat_models: ChatModelFactory,
        prompts: PromptCatalog,
        clock: Clock,
        lock: InflightLock,
        #: Required: an omitted lookup would silently drop every uuid scene.
        scene_names: SceneNameLookup,
    ) -> None:
        self.uow = uow
        self.insight_statements = insight_statements
        self.summary_sources = summary_sources
        self.chat_models = chat_models
        self.prompts = prompts
        self.clock = clock
        self.lock = lock
        self.scene_names = scene_names
        self.model_resolution = ModelResolutionService()
        self.user_resolution = UserResolutionService()

    def execute(self, command: GenerateProfileCacheCommand) -> ProfileCacheResult:
        """Refresh one user's cache columns unless another attempt holds the lock.

        The lock is released on every exit path; ``acquire`` is reentrant for a
        redelivered attempt that kept the same token. The key is the canonical
        UUID string the scanner and the fan-out use, so an uppercase or
        differently formatted id from a producer cannot open a second lock.
        """

        lock_subject = _canonical_end_user_id(command.end_user_id)
        if not self.lock.acquire(lock_subject, command.lock_owner):
            logger.info(
                "profile cache generation skipped, another refresh is in flight "
                "user=%s workspace=%s",
                command.end_user_id,
                command.workspace_id,
            )
            return ProfileCacheResult.inflight_skipped(
                end_user_id=command.end_user_id,
                workspace_id=str(command.workspace_id),
            )
        try:
            return self._execute(command)
        finally:
            self.lock.release(lock_subject, command.lock_owner)

    def _execute(self, command: GenerateProfileCacheCommand) -> ProfileCacheResult:
        language = Language.parse(command.language)
        workspace, resolved = self._load_workspace_and_model(command.workspace_id)
        memo = ChatModelMemo(self.chat_models, resolved)

        insight = (
            self._refresh_insight(command, language, memo, workspace)
            if command.refresh_insight
            else PartResult(None, status="skipped")
        )
        summary = (
            self._refresh_summary(command, language, memo)
            if command.refresh_summary
            else PartResult(None, status="skipped")
        )

        errors: list[dict[str, Any]] = []
        if command.refresh_insight and not insight.success:
            errors.append({"type": "insight", "error": insight.error})
        if command.refresh_summary and not summary.success:
            errors.append({"type": "summary", "error": summary.error})
        return ProfileCacheResult(
            end_user_id=command.end_user_id,
            workspace_id=str(command.workspace_id),
            insight_success=insight.success if command.refresh_insight else None,
            summary_success=summary.success if command.refresh_summary else None,
            errors=errors,
        )

    # ------------------------------------------------------------- internals

    def _load_workspace_and_model(
        self, workspace_id: WorkspaceId
    ) -> tuple[Workspace, ResolvedChatModel]:
        with self.uow.read() as repositories:
            workspace = ensure_workspace_can_generate(
                repositories.workspaces.get(workspace_id), workspace_id
            )
            workspace.ensure_model_resolution_allowed()
            config_id = workspace.memory_config
            if config_id is None:  # pragma: no cover - guarded above
                raise ModelConfigurationError(f"空间{workspace_id}无启用的记忆配置")
            config_row = repositories.model_configs.resolve_model(
                config_id=config_id,
                slot="llm",
                is_default_config=workspace.is_default_config,
                workspace_model_id=(
                    workspace.models.llm.value if workspace.models.llm else None
                ),
            )
        return workspace, self.model_resolution.resolve(workspace, config_row)

    def _refresh_insight(
        self,
        command: GenerateProfileCacheCommand,
        language: Language,
        memo: ChatModelMemo,
        workspace: Workspace,
    ) -> PartResult:
        started_at = time.monotonic()
        end_user_uuid = parse_uuid(command.end_user_id)
        if end_user_uuid is None:
            return PartResult(False, "无效的用户或工作空间ID格式", "invalid_parameter")
        end_user_id = EndUserId(end_user_uuid)

        try:
            resolved = self._resolve_insight_source(end_user_id, workspace.id)
            if resolved is None:
                return PartResult(
                    False, "用户不存在、已停用或不属于当前工作空间", "source_not_found"
                )
            end_user, source = resolved

            scene_names = load_scene_names(
                source.metadata, self.scene_names, workspace.tenant_id
            )
            metadata_input = build_memory_insight_metadata_input(
                source.metadata, scene_names
            )
            fallback_statements: list[str] = []
            if count_memory_insight_entries(metadata_input) < MEMORY_INSIGHT_MIN_ENTRIES:
                raw_statements = self.insight_statements.recent_user_statements(
                    str(end_user.id),
                    ["FACT", "OPINION"],
                    MEMORY_INSIGHT_STATEMENT_LIMIT,
                )
                fallback_statements = build_memory_insight_statement_input(
                    raw_statements, metadata_input
                )

            total_entries = (
                count_memory_insight_entries(metadata_input)
                + len(fallback_statements)
            )
            if total_entries < MEMORY_INSIGHT_MIN_ENTRIES:
                content = InsightContent(
                    overview=(
                        INSUFFICIENT_MEMORY_INSIGHT_TEXT_EN
                        if language is Language.EN
                        else INSUFFICIENT_MEMORY_INSIGHT_TEXT
                    )
                )
            else:
                content = MemoryInsightGenerator(
                    memo.chat_model(), self.prompts
                ).generate(metadata_input, fallback_statements, language)

            insight = MemoryInsight(
                end_user_id=end_user.id,
                workspace_id=workspace.id,
                content=content,
                source_version=source.version,
                generated_at=self.clock.now(),
            )
            with self.uow.transaction() as repositories:
                outcome = repositories.insights.save_if_source_unchanged(insight)
        except Exception as exc:
            logger.exception(
                "generate memory insight failed: end_user_id=%s error=%s",
                command.end_user_id,
                exc,
            )
            return PartResult(False, f"Neo4j或LLM服务不可用: {exc}", "generation_failed")

        if outcome is WriteOutcome.SUPERSEDED:
            return PartResult(False, "源数据已更新，本轮洞察未写入", "superseded")
        if outcome is WriteOutcome.NOT_FOUND:
            return PartResult(
                False, "用户不存在、已停用或不属于当前工作空间", "source_not_found"
            )
        logger.debug(
            "insight generated user=%s elapsed=%.3fs",
            command.end_user_id,
            time.monotonic() - started_at,
        )
        return PartResult(True, status="refreshed")

    def _refresh_summary(
        self,
        command: GenerateProfileCacheCommand,
        language: Language,
        memo: ChatModelMemo,
    ) -> PartResult:
        end_user_uuid = parse_uuid(command.end_user_id)
        if end_user_uuid is None:
            return PartResult(False, "无效的用户ID格式", "invalid_parameter")

        try:
            with self.uow.read() as repositories:
                candidates = repositories.end_users.resolution_candidates(
                    EndUserId(end_user_uuid), command.workspace_id
                )
            resolution = self.user_resolution.resolve(candidates)
            if resolution is None:
                return PartResult(False, "用户不存在", "source_not_found")
            end_user = resolution.user

            entity_tags = self.summary_sources.raw_entity_tags(
                str(end_user.id), SUMMARY_ENTITY_QUERY_LIMIT
            )
            statements = self.summary_sources.recent_statements(
                str(end_user.id), SUMMARY_STATEMENT_QUERY_LIMIT
            )
            sections = UserSummaryGenerator(
                memo.chat_model(), self.prompts
            ).generate(
                str(end_user.id),
                end_user.display_name,
                entity_tags,
                statements,
                language,
            )
            if sections.is_empty():
                return PartResult(
                    False,
                    "生成的用户摘要为空,可能Neo4j中没有该用户的数据",
                    "generation_failed",
                )

            summary = UserSummary(
                end_user_id=end_user.id,
                workspace_id=command.workspace_id,
                sections=sections,
                generated_at=self.clock.now(),
            )
            with self.uow.transaction() as repositories:
                outcome = repositories.summaries.save(summary)
        except Exception as exc:
            logger.error(
                "generate user summary failed: end_user_id=%s error=%s",
                command.end_user_id,
                exc,
                exc_info=True,
            )
            return PartResult(False, f"Neo4j或LLM服务不可用: {exc}", "generation_failed")

        if outcome is not WriteOutcome.UPDATED:
            return PartResult(False, "数据库更新失败", "write_failed")
        return PartResult(True, status="refreshed")

    def _resolve_insight_source(
        self, end_user_id: EndUserId, workspace_id: WorkspaceId
    ) -> tuple[EndUser, InsightSource] | None:
        """Resolve merge routing, then read the source of the resolved user.

        Both halves of the cache use the same routing rule; without this the
        insight would look up an origin id that was merged away while the
        summary had already resolved to the target.
        """

        with self.uow.read() as repositories:
            resolution = self.user_resolution.resolve(
                repositories.end_users.resolution_candidates(
                    end_user_id, workspace_id
                )
            )
            if resolution is None:
                return None
            source = repositories.insights.find_source(
                resolution.user.id, workspace_id
            )
            if source is None:
                return None
            return resolution.user, source
