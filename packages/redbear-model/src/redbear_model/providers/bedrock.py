"""AWS Bedrock model identifiers, parameters, and lazy loaders."""

from __future__ import annotations

import json
from typing import Any

from redbear_model.contracts import (
    EmbeddingContent,
    EmbeddingPurpose,
    ImageEmbeddingContent,
    ModelFeature,
    ModelProvider,
    ResolvedModelConfig,
)
from redbear_model.errors import ProviderDependencyMissingError

BEDROCK_MODEL_MAPPING = {
    "claude-3.5-sonnet": "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "claude-3-5-sonnet": "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "claude-sonnet-3.5": "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "claude-sonnet-3-5": "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "claude-3-sonnet": "anthropic.claude-3-sonnet-20240229-v1:0",
    "claude-3-haiku": "anthropic.claude-3-haiku-20240307-v1:0",
    "claude-3-opus": "anthropic.claude-3-opus-20240229-v1:0",
    "claude-sonnet": "anthropic.claude-3-sonnet-20240229-v1:0",
    "claude-haiku": "anthropic.claude-3-haiku-20240307-v1:0",
    "claude-opus": "anthropic.claude-3-opus-20240229-v1:0",
    "claude-2": "anthropic.claude-v2",
    "claude-2.1": "anthropic.claude-v2:1",
    "claude-instant": "anthropic.claude-instant-v1",
    "titan-text-express": "amazon.titan-text-express-v1",
    "titan-text-lite": "amazon.titan-text-lite-v1",
    "titan-embed-text": "amazon.titan-embed-text-v1",
    "titan-embed-image": "amazon.titan-embed-image-v1",
    "llama3-70b": "meta.llama3-70b-instruct-v1:0",
    "llama3-8b": "meta.llama3-8b-instruct-v1:0",
    "llama2-70b": "meta.llama2-70b-chat-v1",
    "llama2-13b": "meta.llama2-13b-chat-v1",
    "mistral-7b": "mistral.mistral-7b-instruct-v0:2",
    "mixtral-8x7b": "mistral.mixtral-8x7b-instruct-v0:1",
    "mistral-large": "mistral.mistral-large-2402-v1:0",
    "claude-sonnet-4-5": "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "claude-4-5-sonnet": "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "claude-sonnet-4.5": "anthropic.claude-3-5-sonnet-20240620-v1:0",
}


def normalize_bedrock_model_id(
    model_name: str,
    region: str | None = None,
) -> str:
    prefixes = ("us.", "eu.", "apac.", "sa.", "amer.", "global.", "us-gov.")
    if "." in model_name and not model_name.startswith(prefixes):
        provider = model_name.split(".", 1)[0]
        if provider in {
            "anthropic",
            "amazon",
            "meta",
            "mistral",
            "deepseek",
            "openai",
            "ai21",
            "cohere",
            "stability",
        }:
            return model_name
    region_prefix = None
    normalized_name = model_name
    if model_name.startswith(prefixes):
        region_prefix, normalized_name = model_name.split(".", 1)
    mapped = BEDROCK_MODEL_MAPPING.get(normalized_name.lower(), normalized_name)
    selected_region = region or region_prefix
    return f"{selected_region}.{mapped}" if selected_region else mapped


def _credentials(config: ResolvedModelConfig) -> tuple[str, str | None]:
    value = config.api_key.get_secret_value()
    if ":" in value:
        return tuple(value.split(":", 1))  # type: ignore[return-value]
    return value, None


def build_bedrock_params(config: ResolvedModelConfig) -> dict[str, Any]:
    try:
        from botocore.config import Config as BotoConfig
    except ModuleNotFoundError as exc:
        raise ProviderDependencyMissingError("bedrock", "bedrock") from exc

    access_key, secret_key = _credentials(config)
    region = config.base_url or "us-east-1"
    config_only = {
        "deep_thinking",
        "thinking_budget_tokens",
        "enable_search",
        "enable_thinking",
        "response_format",
        "json_output",
        "default_headers",
        "streaming",
    }
    provider_specific = {
        "top_k",
        "repetition_penalty",
        "seed",
        "enable_search",
        "stop",
        "temperature",
        "max_tokens",
    }
    filtered = {
        key: value
        for key, value in config.provider_params.items()
        if key not in config_only
        and key not in provider_specific
    }
    params: dict[str, Any] = {
        "model_id": normalize_bedrock_model_id(config.model_name),
        "region_name": region,
        "aws_access_key_id": access_key,
        "config": BotoConfig(
            max_pool_connections=config.runtime.bedrock_max_pool_connections,
            retries={
                "max_attempts": config.runtime.bedrock_max_retries,
                "mode": "adaptive",
            },
        ),
        **filtered,
    }
    if secret_key:
        params["aws_secret_access_key"] = secret_key
    model_kwargs: dict[str, Any] = {}
    for key in ("top_k", "seed"):
        value = config.provider_params.get(key)
        if value is not None:
            model_kwargs[key] = value
    stop = config.provider_params.get("stop")
    if stop is not None:
        model_kwargs["stop_sequences"] = stop
    for key in ("temperature", "max_tokens"):
        value = config.provider_params.get(key)
        if value is not None:
            params[key] = value
    if config.deep_thinking:
        params["additional_model_request_fields"] = {
            "thinking": {
                "type": "enabled",
                "budget_tokens": config.thinking_budget_tokens or 1024,
            }
        }
    should_send_json = config.json_output or isinstance(
        config.provider_params.get("response_format"),
        dict,
    )
    thinking_conflict = (
        ModelFeature.THINKING in config.profile.features and config.deep_thinking
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


def load_bedrock_chat_class():
    try:
        from langchain_aws import ChatBedrock
    except ModuleNotFoundError as exc:
        raise ProviderDependencyMissingError("bedrock", "bedrock") from exc
    return ChatBedrock


def load_bedrock_embedding_class():
    try:
        from langchain_aws import BedrockEmbeddings
    except ModuleNotFoundError as exc:
        raise ProviderDependencyMissingError("bedrock", "bedrock") from exc
    return BedrockEmbeddings


def is_bedrock_nova_embedding(config: ResolvedModelConfig) -> bool:
    return (
        config.provider is ModelProvider.BEDROCK
        and normalize_bedrock_model_id(config.model_name)
        == "amazon.nova-2-multimodal-embeddings-v1:0"
    )


def embed_bedrock_nova(
    client: Any, model_name: str, content: EmbeddingContent, purpose: EmbeddingPurpose
) -> list[float]:
    params: dict[str, Any] = {
        "embeddingPurpose": (
            "GENERIC_INDEX" if purpose is EmbeddingPurpose.INDEX else "GENERIC_RETRIEVAL"
        ),
    }
    if isinstance(content, ImageEmbeddingContent):
        media_format = content.media_type.split("/", 1)[1]
        if media_format not in {"jpeg", "png", "webp"}:
            raise ValueError("Nova embedding images must be JPEG, PNG, or WebP")
        params["image"] = {
            "format": media_format,
            "source": {"bytes": content.data_uri.split(",", 1)[1]},
        }
    else:
        params["text"] = {"value": content.text, "truncationMode": "END"}

    response = client.invoke_model(
        modelId=normalize_bedrock_model_id(model_name),
        contentType="application/json",
        accept="application/json",
        body=json.dumps({"taskType": "SINGLE_EMBEDDING", "singleEmbeddingParams": params}),
    )
    with response["body"] as body:
        embeddings = json.load(body)["embeddings"]
    if len(embeddings) != 1 or not embeddings[0]["embedding"]:
        raise RuntimeError("Nova embedding did not return a single non-empty vector")
    return embeddings[0]["embedding"]


def load_bedrock_rerank_class():
    try:
        from langchain_aws import BedrockRerank
    except ModuleNotFoundError as exc:
        raise ProviderDependencyMissingError("bedrock", "bedrock") from exc
    return BedrockRerank


def bedrock_rerank_model_arn(model_name: str, region: str) -> str:
    """模型名 → Bedrock rerank 需要的 model ARN。

    ``BedrockRerank`` 只接受 ``model_arn``（不是 ``model_id``），广场里存的却是
    ``amazon.rerank-v1:0`` 这类模型标识，必须在这里拼成 foundation-model ARN。
    用户直接填 ARN 时原样透传，不做二次拼接。

    注意不传 region 给 ``normalize_bedrock_model_id``：那个函数的 region 参数是
    跨区推理前缀（``us.anthropic...``）的语义，rerank 模型不吃这个前缀，带上会把
    ``us-east-1.amazon.rerank-v1:0`` 这类非法标识塞进 ARN。
    """
    if model_name.startswith("arn:"):
        return model_name
    return (
        f"arn:aws:bedrock:{region}::foundation-model/"
        f"{normalize_bedrock_model_id(model_name)}"
    )


def build_bedrock_rerank_params(config: ResolvedModelConfig) -> dict[str, Any]:
    """``BedrockRerank`` 构造参数（凭据/区域口径与 chat、embedding 一致）。

    凭据必须包成 ``SecretStr``：``BedrockRerank`` 的 ``model_validator(mode="before")``
    在字段校验**之前**就调用 ``create_aws_client``，那里直接对入参调
    ``get_secret_value()``；传裸 ``str`` 会在构造期抛
    ``'str' object has no attribute 'get_secret_value'``。
    """
    try:
        from botocore.config import Config as BotoConfig
    except ModuleNotFoundError as exc:
        raise ProviderDependencyMissingError("bedrock", "bedrock") from exc

    from pydantic import SecretStr

    access_key, secret_key = _credentials(config)
    region = config.base_url or "us-east-1"
    params: dict[str, Any] = {
        "model_arn": bedrock_rerank_model_arn(config.model_name, region),
        "region_name": region,
        "aws_access_key_id": SecretStr(access_key),
        "config": BotoConfig(
            max_pool_connections=config.runtime.bedrock_max_pool_connections,
            retries={
                "max_attempts": config.runtime.bedrock_max_retries,
                "mode": "adaptive",
            },
        ),
    }
    if secret_key:
        params["aws_secret_access_key"] = SecretStr(secret_key)
    top_n = config.provider_params.get("top_n")
    if isinstance(top_n, int) and top_n > 0:
        params["top_n"] = top_n
    return params
