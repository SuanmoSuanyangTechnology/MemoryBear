"""Generate one user's interest distribution and optionally cache it.

The workspace and config are read in one short scope; the generation then runs
under its own budget. A missing workspace is not cacheable, while a missing
config or a failed generation degrades to an empty cacheable result — the
behaviour the online path and the pre-warm both depend on. The cache write is
part of the use case: the online path asks the caller to store it, while the
pre-warm's unit writes it here for the next request to hit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.application.interest.generator import generate_interest_distribution
from src.application.ports.cache import InterestCache
from src.application.ports.llm import ChatModelFactory
from src.application.ports.prompts import PromptCatalog
from src.application.ports.uow import Repositories, UnitOfWork
from src.domain.interest.model import InterestDistribution
from src.domain.interest.repository import InterestSourceReader
from src.domain.model_config.model import ResolvedChatModel
from src.domain.model_config.services import ModelResolutionService
from src.domain.shared.errors import ModelConfigurationError
from src.domain.shared.ids import WorkspaceId
from src.domain.shared.language import Language
from src.domain.workspace.model import Workspace

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GenerateInterestDistributionCommand:
    end_user_id: str
    workspace_id: WorkspaceId
    language: str = "zh"
    limit: int = 5
    #: The pre-warm's units set this; the online path leaves the cache to its caller.
    write_cache: bool = False


@dataclass(frozen=True, slots=True)
class InterestDistributionResult:
    items: list[dict[str, Any]]
    cacheable: bool

    def to_payload(self) -> dict[str, Any]:
        return {"items": self.items, "cacheable": self.cacheable}


class GenerateInterestDistributionUseCase:
    def __init__(
        self,
        *,
        uow: UnitOfWork,
        source: InterestSourceReader,
        chat_models: ChatModelFactory,
        prompts: PromptCatalog,
        cache: InterestCache,
    ) -> None:
        self.uow = uow
        self.source = source
        self.chat_models = chat_models
        self.prompts = prompts
        self.cache = cache
        self.model_resolution = ModelResolutionService()

    def execute(
        self, command: GenerateInterestDistributionCommand
    ) -> InterestDistributionResult:
        language = Language.parse(command.language)

        with self.uow.read() as repositories:
            workspace = repositories.workspaces.get(command.workspace_id)
            if workspace is None:
                return InterestDistributionResult(items=[], cacheable=False)
            try:
                resolved = self._resolve_chat_model(workspace, repositories)
            except Exception as exc:
                logger.error(
                    "interest distribution degraded to empty: end_user_id=%s error=%s",
                    command.end_user_id,
                    type(exc).__name__,
                    exc_info=True,
                )
                return InterestDistributionResult(items=[], cacheable=True)

        try:
            distribution = generate_interest_distribution(
                self.source,
                lambda timeout_seconds: self.chat_models.chat_model(
                    resolved.config_id,
                    resolved.tenant_id,
                    timeout_seconds=timeout_seconds,
                ),
                self.prompts,
                command.end_user_id,
                command.workspace_id,
                language,
                command.limit,
            )
        except Exception as exc:
            logger.error(
                "interest distribution degraded to empty: end_user_id=%s error=%s",
                command.end_user_id,
                type(exc).__name__,
                exc_info=True,
            )
            distribution = InterestDistribution(
                end_user_id=command.end_user_id,
                workspace_id=command.workspace_id,
                language=language,
                items=(),
                cacheable=True,
            )

        items = [
            {"name": item.name, "frequency": item.frequency}
            for item in distribution.items
        ]
        if command.write_cache and distribution.cacheable:
            self.cache.write(command.end_user_id, language.value, items)
        return InterestDistributionResult(
            items=items,
            cacheable=distribution.cacheable,
        )

    def _resolve_chat_model(
        self, workspace: Workspace, repositories: Repositories
    ) -> ResolvedChatModel:
        workspace.ensure_model_resolution_allowed()
        config_id = workspace.memory_config
        if config_id is None:  # pragma: no cover - guarded above
            raise ModelConfigurationError(f"空间{workspace.id}无启用的记忆配置")
        config_row = repositories.model_configs.resolve_model(
            config_id=config_id,
            slot="llm",
            is_default_config=workspace.is_default_config,
            workspace_model_id=workspace.models.llm.value if workspace.models.llm else None,
        )
        return self.model_resolution.resolve(workspace, config_row)
