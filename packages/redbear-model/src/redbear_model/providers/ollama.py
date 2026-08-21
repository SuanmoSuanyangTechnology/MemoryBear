"""Ollama provider adapter."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from redbear_model.contracts import ResolvedModelConfig
from redbear_model.errors import ProviderDependencyMissingError


def build_ollama_params(config: ResolvedModelConfig) -> dict[str, Any]:
    config_only = {
        "deep_thinking",
        "thinking_budget_tokens",
        "enable_search",
        "enable_thinking",
        "response_format",
        "json_output",
        "default_headers",
    }
    provider_specific = {
        "top_k",
        "repetition_penalty",
        "seed",
        "stop",
        "temperature",
        "max_tokens",
    }
    params: dict[str, Any] = {
        "model": config.model_name,
        "base_url": config.base_url,
    }
    params.update(
        {
            key: value
            for key, value in config.provider_params.items()
            if key not in config_only and key not in provider_specific
        }
    )
    for key in provider_specific:
        value = config.provider_params.get(key)
        if value is not None:
            params[key] = value
    return params


def load_ollama_llm_class():
    try:
        return import_module("langchain_ollama").OllamaLLM
    except (ModuleNotFoundError, AttributeError) as exc:
        raise ProviderDependencyMissingError("ollama", "ollama") from exc


def load_ollama_embedding_class():
    try:
        return import_module("langchain_ollama").OllamaEmbeddings
    except (ModuleNotFoundError, AttributeError) as exc:
        raise ProviderDependencyMissingError("ollama", "ollama") from exc
