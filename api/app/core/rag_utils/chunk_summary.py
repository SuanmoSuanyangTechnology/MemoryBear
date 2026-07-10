"""
Generate summary for RAG chunks using memory_summary.jinja2 prompt template.
"""

from typing import List, Optional

from pydantic import BaseModel, Field

from app.core.logging_config import get_business_logger
from app.core.memory.pipelines.base_pipeline import ModelClientMixin
from app.db import get_db_context
from app.services.memory_config_service import MemoryConfigService

business_logger = get_business_logger()


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
    """Get LLM client from user-connected config. Raises if no config found."""
    with get_db_context() as db:
        if not end_user_id:
            raise ValueError("end_user_id is required to resolve LLM client")
        config_service = MemoryConfigService(db)
        config_id = config_service.get_config_id_by_end_user(end_user_id) # 这个获取记忆配置是没有问题
        if not config_id:
            raise ValueError(
                f"No memory configuration found for end_user_id: {end_user_id}"
            )
        memory_config = config_service.load_memory_config(config_id=config_id)
        return ModelClientMixin.get_llm_client(
            db, memory_config.llm_model_id, memory_config.tenant_id
        )


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

        # Try structured output; fall back to plain ainvoke for parse failures.
        try:
            response: MemorySummaryResponse = await llm_client.call_structured(messages, MemorySummaryResponse)
            if response.summary:
                summary = response.summary.strip()
            elif response.statements:
                summary = "；".join(s.statement for s in response.statements)
            else:
                summary = "暂无内容"
        except Exception as e:
            business_logger.warning(
                f"结构化解析失败，降级为普通对话: end_user_id={end_user_id}, reason={e}"
            )
            raw = await llm_client.ainvoke(messages)
            summary = raw.content.strip() if raw and hasattr(raw, 'content') else "暂无内容"

        business_logger.info(
            f"成功生成chunk摘要，处理了 {len(chunks_to_process)} 个片段"
        )
        return summary

    except Exception as e:
        business_logger.error(f"生成chunk摘要失败: {str(e)}")
        return "摘要生成失败"
