"""Ollama provider adapter."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from redbear_model.contracts import ResolvedModelConfig
from redbear_model.errors import ProviderDependencyMissingError


def build_ollama_params(config: ResolvedModelConfig) -> dict[str, Any]:
    params: dict[str, Any] = {
        "model": config.model_name,
        "base_url": config.base_url,
    }
    params.update(config.provider_params)
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
