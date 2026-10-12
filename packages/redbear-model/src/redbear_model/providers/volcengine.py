"""Volcengine generation client lazy loader."""

from __future__ import annotations

from typing import Any

from redbear_model.contracts import ResolvedModelConfig
from redbear_model.errors import ProviderDependencyMissingError


def load_ark_class():
    try:
        from volcenginesdkarkruntime import Ark
    except ModuleNotFoundError as exc:
        raise ProviderDependencyMissingError("volcano", "generation") from exc
    return Ark


def build_ark_client(config: ResolvedModelConfig):
    """Ark client for a resolved config.

    Omit ``base_url`` when the channel leaves it unset: the SDK default only applies when the
    argument is absent — an explicit ``None`` is wrapped in ``httpx.URL`` and raises TypeError.
    """
    kwargs: dict[str, Any] = {"api_key": config.api_key.get_secret_value()}
    if config.base_url:
        kwargs["base_url"] = config.base_url
    return load_ark_class()(**kwargs)


def build_sequential_image_options(values: dict):
    try:
        from volcenginesdkarkruntime.types.images.images import (
            SequentialImageGenerationOptions,
        )
    except ModuleNotFoundError as exc:
        raise ProviderDependencyMissingError("volcano", "generation") from exc
    return SequentialImageGenerationOptions(**values)


def build_content_generation_tool(values: dict):
    try:
        from volcenginesdkarkruntime.types.images.images import ContentGenerationTool
    except ModuleNotFoundError as exc:
        raise ProviderDependencyMissingError("volcano", "generation") from exc
    return ContentGenerationTool(**values)


def build_optimize_prompt_options(values: dict):
    try:
        from volcenginesdkarkruntime.types.images.images import OptimizePromptOptions
    except ModuleNotFoundError as exc:
        raise ProviderDependencyMissingError("volcano", "generation") from exc
    return OptimizePromptOptions(**values)
