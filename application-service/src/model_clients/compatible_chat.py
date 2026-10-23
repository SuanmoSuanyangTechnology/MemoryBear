"""
Volcano Engine ChatOpenAI extension

When parsing streaming SSE, ChatOpenAI only reads delta.content and drops delta.reasoning_content.
This class overrides only _convert_chunk_to_generation_chunk to splice reasoning_content into additional_kwargs.
"""
from __future__ import annotations

from typing import Any, Optional, Union

from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI


class CompatibleChatOpenAI(ChatOpenAI):
    """Omni-compatible model for Volcano and Qwen: passes deep-thinking content (reasoning_content) through in streaming and non-streaming modes.

    Also fixes the strict-validation error when json_output + tools are used together:
    langchain_openai forces the .parse()/.stream() path then; with tools present,
    response_format is removed from the payload so the parent uses the plain
    .create()/.astream() path, and JSON output is guaranteed by system prompt instructions.
    """

    def _get_request_payload(
        self,
        input_: list[BaseMessage],
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        # With tools present, langchain_openai switches to the .parse()/.stream() path on
        # seeing response_format, and the OpenAI SDK then requires every tool to be
        # strict=True, which dynamically generated tools are not. So only when tools AND a
        # dict response_format (json_object etc.) are both present, move response_format
        # into extra_body (the SDK merges it into the top level of the request body), so the
        # upstream API still receives the JSON constraint. Without tools nothing changes.
        # Pydantic classes (with_structured_output) must stay at the top level: they rely on
        # .parse() and cannot be serialized into extra_body. JSON instructions in the system
        # prompt remain as a fallback.
        response_format = payload.get("response_format")
        if payload.get("tools") and isinstance(response_format, dict):
            payload.pop("response_format")
            payload["extra_body"] = {
                **(payload.get("extra_body") or {}),
                "response_format": response_format,
            }
        return payload

    def _create_chat_result(self, response: Union[dict, Any], generation_info: Optional[dict] = None) -> ChatResult:
        result = super()._create_chat_result(response, generation_info)
        # Splice reasoning_content from the non-streaming response into additional_kwargs
        choices = response.choices if hasattr(response, "choices") else response.get("choices", [])
        if choices:
            message = choices[0].message if hasattr(choices[0], "message") else choices[0].get("message", {})
            reasoning = (
                getattr(message, "reasoning_content", None)
                or (message.get("reasoning_content") if isinstance(message, dict) else None)
            )
            if reasoning and result.generations:
                result.generations[0].message.additional_kwargs["reasoning_content"] = reasoning
        return result

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: Optional[dict],
    ) -> Optional[ChatGenerationChunk]:
        gen_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if gen_chunk is None:
            return None

        # Extract reasoning_content from the raw chunk
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices", [])
        if choices:
            delta = choices[0].get("delta") or {}
            reasoning: Any = delta.get("reasoning_content")
            if reasoning:
                gen_chunk.message.additional_kwargs["reasoning_content"] = reasoning

        return gen_chunk
