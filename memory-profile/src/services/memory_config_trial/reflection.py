"""Reflection configuration preview; static input, remote LLM, no memory writes."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from uuid import UUID

from jinja2 import Environment, FileSystemLoader

from src.i18n import t
from src.infrastructure.model_service import ModelServiceManager, ProfileChatModel
from src.infrastructure.postgres import PostgresManager
from src.repositories.memory_config import MemoryConfigReadRepository
from src.repositories.model_config import ModelConfigRepository
from src.schemas.reflection_trial import (
    ConflictResultSchema,
    ReflectionTrialConfig,
    ReflectionTrialData,
    ReflectionTrialFailure,
    ReflexionResultSchema,
    TranslationResponse,
)
from src.services.memory_config.errors import ConfigNotFoundError
from src.services.model_config import ModelConfigurationError

logger = logging.getLogger(__name__)
_ASSETS = Path(__file__).with_name("reflection_assets")
_PROMPTS = Environment(loader=FileSystemLoader(_ASSETS), autoescape=False)
_REMOVE_KEYS = frozenset(
    {"created_at", "relationship", "predicate", "statement_id", "id", "relationship_statement_id"}
)


class ReflectionTrialService:
    def __init__(self, postgres: PostgresManager, model_service: ModelServiceManager) -> None:
        self.configs = MemoryConfigReadRepository(postgres)
        self.models = ModelConfigRepository(postgres)
        self.model_service = model_service

    async def run(
        self,
        config_id: UUID | int,
        workspace_id: UUID,
        tenant_id: UUID,
        *,
        locale: str,
    ) -> ReflectionTrialData | ReflectionTrialFailure:
        row = await self.configs.get_reflection_trial(config_id, workspace_id, tenant_id)
        if row is None:
            raise ConfigNotFoundError(str(config_id))
        config = ReflectionTrialConfig.model_validate(row)
        # Match the preview's workspace.llm selection even for inactive configs.
        # There is no usable default-model fallback in the legacy invoke path.
        try:
            model_id = UUID(str(row["llm"]))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ModelConfigurationError("Workspace LLM is not configured") from exc
        models = await self.models.get_models([model_id], tenant_id)
        if model_id not in models or not models[model_id]["is_active"]:
            raise ModelConfigurationError("Workspace LLM is unavailable")
        model = await self.model_service.chat_model(model_id, tenant_id)
        return await run_preview(config, model, locale=locale)


async def run_preview(
    config: ReflectionTrialConfig, model: ProfileChatModel, *, locale: str
) -> ReflectionTrialData | ReflectionTrialFailure:
    started = time.monotonic()
    # Assets are packaged with the service; never resolve relative to the cwd.
    example = json.loads((_ASSETS / "example.json").read_text(encoding="utf-8"))["memory_verify"]
    source, records = example["source_data"], example["databasets"]
    try:
        prompt = _PROMPTS.get_template("evaluate.jinja2").render(
            evaluate_data=records,
            statement_databasets=source,
            json_schema=ConflictResultSchema.model_json_schema(),
            baseline=config.baseline,
            memory_verify=config.memory_verify,
            quality_assessment=config.quality_assessment,
            language_type=locale,
        )
        detected = await model.call_structured(
            [{"role": "user", "content": prompt}], ConflictResultSchema
        )
        conflict = ConflictResultSchema.model_validate(detected)
        cleaned = {
            "data": [
                {key: value for key, value in item.model_dump().items() if key not in _REMOVE_KEYS}
                for item in conflict.data
            ],
            "conflict": conflict.conflict,
        }
        prompt = _PROMPTS.get_template("reflexion.jinja2").render(
            data=[cleaned],
            statement_databasets=source,
            json_schema=ReflexionResultSchema.model_json_schema(),
            baseline=config.baseline,
            memory_verify=config.memory_verify,
            # Preserve the historical resolution prompt; English is translated below.
            language_type="zh",
        )
        resolved = await model.call_structured(
            [{"role": "user", "content": prompt}], ReflexionResultSchema
        )
        solved = ReflexionResultSchema.model_validate(resolved)
    except Exception:
        # Legacy trial failure is a data result in a successful HTTP envelope.
        logger.warning("Reflection preview model execution failed", exc_info=True)
        return ReflectionTrialFailure(
            message=t("reflection.trial.failed", locale),
            execution_time=time.monotonic() - started,
        )

    result = ReflectionTrialData(
        baseline=config.baseline,
        source_data=(_ASSETS / "source.txt").read_text(encoding="utf-8"),
        memory_verifies=[conflict.memory_verify] if config.memory_verify else [],
        quality_assessments=[conflict.quality_assessment] if config.quality_assessment else [],
        reflexion_data=[item.reflexion for item in solved.results],
    )
    if locale == "en":
        await translate_preview(result, model)
    return result


async def translate_preview(result: ReflectionTrialData, model: ProfileChatModel) -> None:
    """Translate display strings while retaining baseline, flags and scores."""

    async def translate(text: str) -> str:
        response = await model.call_structured(
            [
                {
                    "role": "user",
                    "content": f'{text}\n\n中文翻译为英文，输出格式为{{"data":"翻译后的内容"}}',
                }
            ],
            TranslationResponse,
        )
        return TranslationResponse.model_validate(response).data

    result.source_data = await translate(result.source_data)
    for item in result.memory_verifies:
        if item is not None:
            item.privacy_types = [await translate(value) for value in item.privacy_types]
            item.summary = await translate(item.summary)
    for item in result.quality_assessments:
        if item is not None:
            item.summary = await translate(item.summary)
    for item in result.reflexion_data:
        item.reason = await translate(item.reason)
        item.solution = await translate(item.solution)
