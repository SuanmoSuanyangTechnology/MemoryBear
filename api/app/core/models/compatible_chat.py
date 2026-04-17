"""
火山引擎 ChatOpenAI 扩展

ChatOpenAI 在解析流式 SSE 时只取 delta.content，会丢弃 delta.reasoning_content。
此类仅重写 _convert_chunk_to_generation_chunk，将 reasoning_content 补入 additional_kwargs。
"""
from __future__ import annotations

from typing import Any, Optional, Union

from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI


class CompatibleChatOpenAI(ChatOpenAI):
    """火山和千问的omni兼容模型，支持深度思考内容（reasoning_content）的流式和非流式透传。

    同时修复 json_output + tools 同时使用时 langchain_openai 强制走 .parse()/.stream()
    导致 strict 校验报错的问题：有工具时从 payload 中移除 response_format，
    让父类走普通 .create()/.astream() 路径，JSON 输出由 system prompt 指令保证。
    """

    def _get_request_payload(
        self,
        input_: list[BaseMessage],
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        # 有工具时 langchain_openai 检测到 response_format 会切换到 .parse()/.stream()
        # 接口，OpenAI SDK 要求此时所有工具必须 strict=True，动态生成的工具不满足。
        # 移除 response_format，让父类走普通路径，JSON 输出由 system prompt 指令保证。
        if payload.get("tools") and "response_format" in payload:
            payload.pop("response_format")
        return payload

    def _create_chat_result(self, response: Union[dict, Any], generation_info: Optional[dict] = None) -> ChatResult:
        result = super()._create_chat_result(response, generation_info)
        # 将非流式响应中的 reasoning_content 补入 additional_kwargs
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

        # 从原始 chunk 中提取 reasoning_content
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices", [])
        if choices:
            delta = choices[0].get("delta") or {}
            reasoning: Any = delta.get("reasoning_content")
            if reasoning:
                gen_chunk.message.additional_kwargs["reasoning_content"] = reasoning

        return gen_chunk
