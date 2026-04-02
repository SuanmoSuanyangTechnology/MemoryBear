"""
火山引擎 ChatOpenAI 扩展

ChatOpenAI 在解析流式 SSE 时只取 delta.content，会丢弃 delta.reasoning_content。
此类仅重写 _convert_chunk_to_generation_chunk，将 reasoning_content 补入 additional_kwargs。
"""
from __future__ import annotations

from typing import Any, Optional

from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI


class VolcanoChatOpenAI(ChatOpenAI):
    """火山引擎 Chat 模型，支持深度思考内容（reasoning_content）的流式透传。"""

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
