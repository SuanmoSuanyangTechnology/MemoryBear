"""Volcengine generation client lazy loader."""

from __future__ import annotations

from redbear_model.errors import ProviderDependencyMissingError


def load_ark_class():
    try:
        from volcenginesdkarkruntime import Ark
    except ModuleNotFoundError as exc:
        raise ProviderDependencyMissingError("volcano", "generation") from exc
    return Ark


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
