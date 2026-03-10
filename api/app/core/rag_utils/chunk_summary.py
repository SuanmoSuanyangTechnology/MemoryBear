"""
Generate summary for RAG chunks using memory_summary.jinja2 prompt template.
"""

import asyncio
import os
from typing import List, Optional

from app.core.logging_config import get_business_logger
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.db import get_db_context
from pydantic import BaseModel, Field

business_logger = get_business_logger()

DEFAULT_LLM_ID = os.getenv("SELECTED_LLM_ID", "openai/qwen-plus")


# ── Schema ──────────────────────────────────────────────────────────────────

class MemorySummaryStatement(BaseModel):
    """Single labelled statement extracted by memory_summary.jinja2."""
    statement: str = Field(..., description="提取的陈述内容")
    label: Optional[str] = Field(None, description="陈述标签")


class MemorySummaryResponse(BaseModel):
    """
    Structured output expected from memory_summary.jinja2.
    The template asks for a JSON array of labelled statements;
    we wrap it in an object so response_structured can parse it.
    """
    statements: List[MemorySummaryStatement] = Field(
        default_factory=list,
        description="从chunk中提取的陈述列表"
    )
    summary: Optional[str] = Field(None, description="整体摘要文本（可选）")


# ── LLM client helper ────────────────────────────────────────────────────────

def _get_llm_client(end_user_id: Optional[str] = None):
    """Get LLM client, preferring user-connected config with fallback to default."""
    with get_db_context() as db:
        try:
            if end_user_id:
                from app.services.memory_agent_service import get_end_user_connected_config
                from app.services.memory_config_service import MemoryConfigService
                connected_config = get_end_user_connected_config(end_user_id, db)
                config_id = connected_config.get("memory_config_id")
                workspace_id = connected_config.get("workspace_id")
                if config_id or workspace_id:
                    config_service = MemoryConfigService(db)
                    memory_config = config_service.load_memory_config(
                        config_id=config_id,
                        workspace_id=workspace_id
                    )
                    factory = MemoryClientFactory(db)
                    return factory.get_llm_client(memory_config.llm_model_id)
        except Exception as e:
            business_logger.warning(f"Failed to get user connected config, using default LLM: {e}")
        factory = MemoryClientFactory(db)
        return factory.get_llm_client(DEFAULT_LLM_ID)


# ── Core function ─────────────────────────────────────────────────────────────

async def generate_chunk_summary(
    chunks: List[str],
    max_chunks: int = 10,
    end_user_id: Optional[str] = None,
    language: str = "zh",
) -> str:
    """
    Generate a user summary from RAG chunks using the memory_summary.jinja2 template.

    The template extracts labelled statements from the chunks; we then join them
    into a coherent summary string that can be stored in end_user.user_summary.

    Args:
        chunks: List of chunk content strings
        max_chunks: Maximum number of chunks to process
        end_user_id: Optional end-user ID for model selection
        language: Output language ("zh" or "en")

    Returns:
        Summary string (joined statements or fallback text)
    """
    if not chunks:
        business_logger.warning("没有提供chunk内容用于生成摘要")
        return "暂无内容"

    try:
        from app.core.memory.utils.prompt.prompt_utils import render_memory_summary_prompt

        chunks_to_process = chunks[:max_chunks]
        chunk_texts = "\n\n".join(
            [f"片段{i + 1}: {chunk}" for i, chunk in enumerate(chunks_to_process)]
        )

        json_schema = MemorySummaryResponse.model_json_schema()

        rendered_prompt = await render_memory_summary_prompt(
            chunk_texts=chunk_texts,
            json_schema=json_schema,
            max_words=200,
            language=language,
        )

        messages = [{"role": "user", "content": rendered_prompt}]

        llm_client = _get_llm_client(end_user_id)

        # Try structured output; fall back to plain chat only for LLMClientException
        # (indicates the model/provider doesn't support structured output).
        # All other exceptions are re-raised so config/schema errors stay visible.
        try:
            response: MemorySummaryResponse = await llm_client.response_structured(
                messages=messages,
                response_model=MemorySummaryResponse,
            )
            if response.summary:
                summary = response.summary.strip()
            elif response.statements:
                summary = "；".join(s.statement for s in response.statements)
            else:
                summary = "暂无内容"
        except Exception as e:
            from app.core.memory.llm_tools.llm_client import LLMClientException
            if isinstance(e, LLMClientException):
                business_logger.warning(
                    f"结构化输出不可用，降级为普通对话: end_user_id={end_user_id}, reason={e}"
                )
                raw = await llm_client.chat(messages=messages)
                summary = raw.content.strip() if raw and raw.content else "暂无内容"
            else:
                business_logger.error(f"生成摘要时发生非预期异常: {e}")
                raise

        business_logger.info(
            f"成功生成chunk摘要，处理了 {len(chunks_to_process)} 个片段"
        )
        return summary

    except Exception as e:
        business_logger.error(f"生成chunk摘要失败: {str(e)}")
        return "摘要生成失败"
