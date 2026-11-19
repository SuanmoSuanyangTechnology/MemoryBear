"""Generate and save emotion suggestions through existing application ports."""

import json
import logging

from src.application.emotion.dto import EmotionSuggestionsResult, GenerateEmotionSuggestionsCommand
from src.application.emotion.ports import EmotionSource
from src.application.emotion.schemas import EmotionSuggestions
from src.application.ports.llm import ChatModelFactory
from src.application.ports.prompts import PromptCatalog
from src.application.ports.task_cache import TaskCache
from src.application.ports.uow import UnitOfWork
from src.application.shared.generation import model_for_context, structured_data
from src.domain.emotion.analysis import EmotionAnalysis

logger = logging.getLogger(__name__)


class GenerateEmotionSuggestionsUseCase:
    def __init__(
        self,
        uow: UnitOfWork,
        source: EmotionSource,
        models: ChatModelFactory,
        prompts: PromptCatalog,
        cache: TaskCache,
    ) -> None:
        self.uow, self.sources, self.models, self.prompts, self.cache = (
            uow,
            source,
            models,
            prompts,
            cache,
        )

    def execute(self, command: GenerateEmotionSuggestionsCommand) -> EmotionSuggestionsResult:
        with self.uow.read() as repos:
            context = repos.task_context.context(command.scope)
        if context["storage_type"] != "neo4j":
            raise ValueError("该任务仅支持 Neo4j 工作空间")
        key = (
            f"memory_jobs:operation:emotion_suggestions:{context['workspace_id']}:"
            f"{context['end_user_id']}"
        )
        with self.cache.lock(key):
            result = self._generate(context, command.language)
            self.cache.invalidate(context["end_user_id"], context["workspace_id"])
            return result

    def _generate(self, context, language: str) -> EmotionSuggestionsResult:
        evidence = self.sources.emotion_context(context["end_user_id"])
        analysis = EmotionAnalysis()
        health = analysis.health(evidence["emotions"], evidence["time_range"])
        if not evidence["emotions"]:
            health["emotion_distribution"] = evidence["all_counts"]
        patterns = analysis.analyze_patterns(evidence["emotions"])
        prompt = self.prompts.emotion_suggestions(
            health_data=health,
            patterns=patterns,
            user_profile=evidence["user_profile"],
            language=language,
            emotion_distribution_json=json.dumps(
                health["emotion_distribution"], ensure_ascii=False
            ),
        )
        model = model_for_context(self.models, context)
        try:
            data = structured_data(
                model.call_structured([{"role": "user", "content": prompt}], EmotionSuggestions),
                EmotionSuggestions,
            )
        except Exception:
            logger.warning(
                "emotion suggestion generation failed; using legacy fallback", exc_info=True
            )
            data = EmotionSuggestions.model_validate(
                analysis.default_suggestions(health, language)
            ).model_dump(mode="json")
        with self.uow.transaction() as repos:
            repos.emotion_suggestions.save(context, data)
        return EmotionSuggestionsResult(data)
