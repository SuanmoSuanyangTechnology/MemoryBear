"""OpenAI-compatible provider parameters and chat compatibility."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI

from redbear_model.contracts import (
    ModelFeature,
    ModelProvider,
    ResolvedModelConfig,
)
from redbear_model.runtime.client_pool import HttpClients


class CompatibleChatOpenAI(ChatOpenAI):
    """Preserve reasoning content and avoid strict tool/JSON conflicts (tools + dict response_format go via extra_body)."""

    def _get_request_payload(
        self,
        input_: list[BaseMessage],
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        # 工具 + json_output 同时存在时，response_format（json_object 等字典）留在顶层会让
        # langchain_openai 切到 .parse()/.stream()，而该路径要求所有工具 strict=True，
        # 动态工具不满足会直接报错。此时改经 extra_body 透传（SDK 合并进请求体顶层），
        # 上游 API 仍能收到 JSON 约束。
        # 没有工具时不做处理，response_format 保持在顶层，行为与此前一致。
        # pydantic 类（with_structured_output(json_schema)）必须留在顶层：它依赖 .parse()
        # 的 parsed 解析与截断报错，且无法序列化进 extra_body。
        response_format = payload.get("response_format")
        if payload.get("tools") and isinstance(response_format, dict):
            payload.pop("response_format")
            payload["extra_body"] = {
                **(payload.get("extra_body") or {}),
                "response_format": response_format,
            }
        return payload

    def _create_chat_result(
        self,
        response: dict | Any,
        generation_info: dict | None = None,
    ) -> ChatResult:
        result = super()._create_chat_result(response, generation_info)
        choices = (
            response.choices
            if hasattr(response, "choices")
            else response.get("choices", [])
        )
        if choices:
            message = (
                choices[0].message
                if hasattr(choices[0], "message")
                else choices[0].get("message", {})
            )
            reasoning = getattr(message, "reasoning_content", None)
            if reasoning is None and isinstance(message, dict):
                reasoning = message.get("reasoning_content")
            if reasoning and result.generations:
                result.generations[0].message.additional_kwargs[
                    "reasoning_content"
                ] = reasoning
        return result

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> ChatGenerationChunk | None:
        generation = super()._convert_chunk_to_generation_chunk(
            chunk,
            default_chunk_class,
            base_generation_info,
        )
        if generation is None:
            return None
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices", [])
        if choices:
            reasoning = (choices[0].get("delta") or {}).get("reasoning_content")
            if reasoning:
                generation.message.additional_kwargs["reasoning_content"] = reasoning
        return generation


_CONFIG_ONLY_KEYS = {
    "deep_thinking",
    "thinking_budget_tokens",
    "enable_search",
    "enable_thinking",
    "response_format",
    "json_output",
    "default_headers",
    # 流式提示：唯一效果是下方 stream_usage=True（末帧带 usage）；不进 ChatOpenAI 构造，
    # 免得 streaming=True 字段反过来强制内部走流式（非流式 invoke 也要能带此提示）。
    "streaming",
}
_PROVIDER_SPECIFIC_KEYS = {
    "top_k",
    "repetition_penalty",
    "seed",
    "enable_search",
    "stop",
    "temperature",
    "max_tokens",
}


def _reasoning_effort(budget_tokens: int | None) -> str | None:
    if budget_tokens is None:
        return None
    if budget_tokens <= 2048:
        return "low"
    if budget_tokens <= 4096:
        return "medium"
    return "high"


def _response_format(config: ResolvedModelConfig) -> dict[str, Any]:
    configured = config.provider_params.get("response_format")
    if isinstance(configured, dict):
        return configured
    return {"type": "json_object"}


def build_openai_compatible_params(
    config: ResolvedModelConfig,
    clients: HttpClients,
) -> dict[str, Any]:
    provider_params = dict(config.provider_params)
    provider_specific = {
        key: provider_params[key]
        for key in _PROVIDER_SPECIFIC_KEYS
        if key in provider_params
    }
    filtered = {
        key: value
        for key, value in provider_params.items()
        if key not in _CONFIG_ONLY_KEYS and key not in _PROVIDER_SPECIFIC_KEYS
    }
    params: dict[str, Any] = {
        "model": config.model_name,
        "base_url": (
            "https://dashscope.aliyuncs.com/compatible-mode/v1"
            if config.provider is ModelProvider.DASHSCOPE and not config.base_url
            else config.base_url
        ),
        "api_key": config.api_key.get_secret_value(),
        "max_retries": config.runtime.max_retries,
        "http_client": clients.sync,
        "http_async_client": clients.async_client,
        **filtered,
    }
    if clients.timeout is not None:
        params["timeout"] = clients.timeout
    for key in ("temperature", "max_tokens", "seed", "stop"):
        if provider_specific.get(key) is not None:
            params[key] = provider_specific[key]
    if config.provider is ModelProvider.DASHSCOPE:
        # DashScope 扩展参数不是 OpenAI 标准字段，经 extra_body 透传
        for key in ("repetition_penalty", "top_k", "enable_search"):
            if provider_specific.get(key) is not None:
                params.setdefault("extra_body", {})[key] = provider_specific[key]
    default_headers = provider_params.get("default_headers")
    if isinstance(default_headers, dict):
        params["default_headers"] = default_headers
    if provider_params.get("streaming"):
        params["stream_usage"] = True

    features = set(config.profile.features)
    if ModelFeature.THINKING in features:
        if config.provider is ModelProvider.VOLCANO:
            params.setdefault("extra_body", {})["thinking"] = {
                "type": "enabled" if config.deep_thinking else "disabled"
            }
            effort = _reasoning_effort(config.thinking_budget_tokens)
            if config.deep_thinking and effort is not None:
                params["reasoning_effort"] = effort
        elif config.provider is ModelProvider.SPEEDBEAR:
            params["reasoning_effort"] = "minimal" if config.deep_thinking else "none"
            effort = _reasoning_effort(config.thinking_budget_tokens)
            if config.deep_thinking and effort is not None:
                params["reasoning_effort"] = effort
        else:
            extra_body = params.setdefault("extra_body", {})
            extra_body["enable_thinking"] = config.deep_thinking
            if config.deep_thinking and config.thinking_budget_tokens:
                extra_body["thinking_budget"] = config.thinking_budget_tokens

    should_send_json = config.json_output or isinstance(
        config.provider_params.get("response_format"),
        dict,
    )
    thinking_conflict = (
        ModelFeature.THINKING in features and config.deep_thinking
    )
    if should_send_json and not thinking_conflict:
        params.setdefault("model_kwargs", {})["response_format"] = _response_format(
            config
        )
    return params


def build_openai_embedding_params(
    config: ResolvedModelConfig,
    clients: HttpClients,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "model": config.model_name,
        "base_url": config.base_url,
        "api_key": config.api_key.get_secret_value(),
        "max_retries": config.runtime.max_retries,
        "http_client": clients.sync,
        "http_async_client": clients.async_client,
    }
    if clients.timeout is not None:
        params["timeout"] = clients.timeout
    if config.provider is ModelProvider.SPEEDBEAR:
        params["check_embedding_ctx_length"] = False
    return params


def load_openai_embedding_class():
    from langchain_openai import OpenAIEmbeddings

    return OpenAIEmbeddings
