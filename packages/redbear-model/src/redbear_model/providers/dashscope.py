"""DashScope provider parameter mapping and lazy loaders."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from redbear_model.contracts import ModelCapability, ResolvedModelConfig
from redbear_model.errors import ProviderDependencyMissingError

_MODEL_KWARG_KEYS = {
    "top_k",
    "repetition_penalty",
    "seed",
    "enable_search",
    "stop",
    "temperature",
    "max_tokens",
}
_CONFIG_ONLY_KEYS = {
    "deep_thinking",
    "thinking_budget_tokens",
    "json_output",
    "response_format",
}


def build_dashscope_params(config: ResolvedModelConfig) -> dict[str, Any]:
    params: dict[str, Any] = {
        "model": config.model_name,
        "dashscope_api_key": config.api_key.get_secret_value(),
        "max_retries": config.runtime.max_retries,
    }
    model_kwargs = {
        key: value
        for key, value in config.provider_params.items()
        if key in _MODEL_KWARG_KEYS and value is not None
    }
    for key, value in config.provider_params.items():
        if key not in _MODEL_KWARG_KEYS and key not in _CONFIG_ONLY_KEYS:
            params[key] = value
    if ModelCapability.THINKING in config.capabilities:
        model_kwargs["enable_thinking"] = config.deep_thinking
        if config.deep_thinking and config.thinking_budget_tokens:
            model_kwargs["thinking_budget"] = config.thinking_budget_tokens
        if config.deep_thinking and config.provider_params.get("streaming"):
            model_kwargs["incremental_output"] = True
    thinking_conflict = (
        ModelCapability.THINKING in config.capabilities and config.deep_thinking
    )
    should_send_json = config.json_output or isinstance(
        config.provider_params.get("response_format"),
        dict,
    )
    if should_send_json and not thinking_conflict:
        response_format = config.provider_params.get("response_format")
        model_kwargs["response_format"] = (
            response_format
            if isinstance(response_format, dict)
            else {"type": "json_object"}
        )
    if model_kwargs:
        params["model_kwargs"] = model_kwargs
    return params


def load_dashscope_chat_class():
    try:
        import_module("dashscope")
        from langchain_community.chat_models import ChatTongyi
    except ModuleNotFoundError as exc:
        raise ProviderDependencyMissingError("dashscope", "runtime,dashscope") from exc
    return ChatTongyi


def load_dashscope_embedding_class():
    try:
        import_module("dashscope")
        from langchain_community.embeddings import DashScopeEmbeddings
    except ModuleNotFoundError as exc:
        raise ProviderDependencyMissingError("dashscope", "runtime,dashscope") from exc
    return DashScopeEmbeddings


def load_dashscope_rerank_class():
    try:
        import_module("dashscope")
        from langchain_community.document_compressors.dashscope_rerank import (
            DashScopeRerank,
        )
    except ModuleNotFoundError as exc:
        raise ProviderDependencyMissingError("dashscope", "runtime,dashscope") from exc
    return DashScopeRerank
