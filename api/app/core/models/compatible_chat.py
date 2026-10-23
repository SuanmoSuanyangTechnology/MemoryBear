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
    的问题（该路径要求所有工具 strict=True，动态工具不满足会报错）：仅当请求同时带工具
    和字典形式的 response_format 时，改经 extra_body 透传，父类走普通 .create()/.astream()
    路径；不带工具时保持原状（response_format 留在顶层）。pydantic 类始终留顶层。
    """

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
        # 动态生成的工具不满足会直接报错。此时改经 extra_body 透传（SDK 合并进请求体顶层），
        # 上游 API 仍能收到 JSON 约束。
        # 没有工具时不做处理，response_format 保持在顶层，行为与此前一致。
        # pydantic 类（with_structured_output(json_schema)）必须留在顶层：它依赖 .parse()
        # 的 parsed 解析与截断报错，且无法序列化进 extra_body。system prompt 里的 JSON
        # 指令继续作兜底。
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
