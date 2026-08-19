"""AWS Bedrock model identifiers, parameters, and lazy loaders."""

from __future__ import annotations

from typing import Any

from redbear_model.contracts import ResolvedModelConfig
from redbear_model.errors import ProviderDependencyMissingError

BEDROCK_MODEL_MAPPING = {
    "claude-3.5-sonnet": "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "claude-3-sonnet": "anthropic.claude-3-sonnet-20240229-v1:0",
    "claude-3-haiku": "anthropic.claude-3-haiku-20240307-v1:0",
    "claude-3-opus": "anthropic.claude-3-opus-20240229-v1:0",
    "claude-2": "anthropic.claude-v2",
    "claude-2.1": "anthropic.claude-v2:1",
    "claude-instant": "anthropic.claude-instant-v1",
    "titan-text-express": "amazon.titan-text-express-v1",
    "titan-text-lite": "amazon.titan-text-lite-v1",
    "titan-embed-text": "amazon.titan-embed-text-v1",
    "titan-embed-image": "amazon.titan-embed-image-v1",
    "llama3-70b": "meta.llama3-70b-instruct-v1:0",
    "llama3-8b": "meta.llama3-8b-instruct-v1:0",
    "mistral-7b": "mistral.mistral-7b-instruct-v0:2",
    "mixtral-8x7b": "mistral.mixtral-8x7b-instruct-v0:1",
    "mistral-large": "mistral.mistral-large-2402-v1:0",
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
    access_key, secret_key = _credentials(config)
    region = config.base_url or str(config.provider_params.get("region", "us-east-1"))
    params: dict[str, Any] = {
        "model_id": normalize_bedrock_model_id(config.model_name),
        "region_name": region,
        "aws_access_key_id": access_key,
    }
    if secret_key:
        params["aws_secret_access_key"] = secret_key
    model_kwargs = dict(config.provider_params)
    model_kwargs.pop("region", None)
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
