"""DashScope provider parameter mapping and lazy loaders."""

from __future__ import annotations

from collections.abc import Mapping
from importlib import import_module
from typing import Any

from redbear_model.contracts import (
    ModelCapability,
    ModelProvider,
    ModelType,
    ResolvedModelConfig,
)
from redbear_model.errors import ProviderDependencyMissingError


def is_qwen3_vl_embedding(config: ResolvedModelConfig) -> bool:
    return (
        config.provider is ModelProvider.DASHSCOPE
        and config.model_type is ModelType.EMBEDDING
        and config.model_name == "qwen3-vl-embedding"
        and ModelCapability.VISION in config.capabilities
    )


def is_qwen3_vl_reranker(config: ResolvedModelConfig) -> bool:
    return (
        config.provider is ModelProvider.DASHSCOPE
        and config.model_type is ModelType.RERANK
        and config.model_name == "qwen3-vl-rerank"
        and ModelCapability.VISION in config.capabilities
    )


def resolve_dashscope_native_base_address(base_url: str | None) -> str | None:
    if base_url is None:
        return None
    normalized = base_url.rstrip("/")
    for suffix in ("/compatible-mode/v1", "/compatible-api/v1"):
        if normalized.endswith(suffix):
            return f"{normalized[: -len(suffix)]}/api/v1"
    return normalized


def is_dashscope_multimodal_input_limit(response: Any) -> bool:
    if isinstance(response, Mapping):
        status = response.get("status_code")
        code = response.get("code")
        message = response.get("message")
    else:
        status = getattr(response, "status_code", None)
        code = getattr(response, "code", None)
        message = getattr(response, "message", None)
    try:
        if int(status) != 400:
            return False
    except (TypeError, ValueError):
        return False
    text = f"{code or ''} {message or ''}".lower()
    return (
        "token" in text
        and any(marker in text for marker in ("exceed", "limit", "length", "too many"))
    )


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
