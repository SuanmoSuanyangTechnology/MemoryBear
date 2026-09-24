"""运行参数仲裁（契约只描述不决策：从 ResolvedModelConfig validator 移出，spec §13.5）。"""

from __future__ import annotations

import logging

from ..contracts import ModelFeature

logger = logging.getLogger(__name__)


def normalize_runtime_flags(
    features: tuple[ModelFeature, ...],
    deep_thinking: bool,
    thinking_budget_tokens: int | None,
    json_output: bool,
    model_name: str,
) -> tuple[bool, int | None, bool]:
    """Preserve the legacy RedBearModelConfig capability normalization."""
    has_thinking = ModelFeature.THINKING in features
    has_thinking_only = ModelFeature.THINKING_ONLY in features
    supports_json_output = ModelFeature.JSON_OUTPUT in features

    if deep_thinking and not has_thinking and not has_thinking_only:
        logger.warning(
            "Model %s does not support thinking; disabling deep_thinking",
            model_name,
        )
        deep_thinking = False
        thinking_budget_tokens = None

    if not deep_thinking and thinking_budget_tokens is not None:
        logger.warning(
            "Thinking is disabled for model %s; clearing thinking_budget_tokens",
            model_name,
        )
        thinking_budget_tokens = None

    if has_thinking_only:
        deep_thinking = True
        thinking_budget_tokens = None
        if json_output:
            logger.warning(
                "thinking_only model %s does not support JSON output",
                model_name,
            )
            json_output = False

    if json_output and not supports_json_output:
        logger.warning(
            "Model %s capability does not include json_output; disabling it",
            model_name,
        )
        json_output = False

    return deep_thinking, thinking_budget_tokens, json_output
