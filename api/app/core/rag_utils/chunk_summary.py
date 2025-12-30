"""
Generate summary for RAG chunks.

This module provides functionality to summarize chunk content using LLM.
"""

import asyncio
from typing import Any, Dict, List

from app.core.logging_config import get_business_logger
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.db import get_db_context
from pydantic import BaseModel, Field

business_logger = get_business_logger()


def _get_llm_client():
    """Get LLM client using db context."""
    with get_db_context() as db:
        factory = MemoryClientFactory(db)
        return factory.get_llm_client(None)  # Uses default LLM


class ChunkSummary(BaseModel):
    """Pydantic model for chunk summary."""
    summary: str = Field(..., description="简洁的chunk内容摘要")


async def generate_chunk_summary(chunks: List[str], max_chunks: int = 10) -> str:
    """
    Generate a summary for the given chunks.
    
    Args:
        chunks: List of chunk content strings
        max_chunks: Maximum number of chunks to process (default: 10)
    
    Returns:
        A concise summary of the chunks
    """
    if not chunks:
        business_logger.warning("没有提供chunk内容用于生成摘要")
        return "暂无内容"
    
    try:
        # 限制处理的chunk数量，避免token过多
        chunks_to_process = chunks[:max_chunks]
        
        # 合并chunk内容
        combined_content = "\n\n".join([f"片段{i+1}: {chunk}" for i, chunk in enumerate(chunks_to_process)])
        
        # 构建prompt
        system_prompt = (
            "你是一位专业的文本摘要助手。请基于提供的文本片段，生成简洁的摘要。要求：\n"
            "- 摘要长度控制在100-150字；\n"
            "- 提取核心信息和关键要点；\n"
            "- 使用客观、清晰的语言；\n"
            "- 避免冗余和重复；\n"
            "- 如果内容涉及多个主题，按重要性排序呈现。"
        )
        
        user_prompt = f"请为以下文本片段生成摘要：\n\n{combined_content}"
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        
        # 调用LLM生成摘要
        llm_client = _get_llm_client()
        response = await llm_client.chat(messages=messages)
        
        summary = response.content.strip()
        business_logger.info(f"成功生成chunk摘要，处理了 {len(chunks_to_process)} 个片段")
        
        return summary
        
    except Exception as e:
        business_logger.error(f"生成chunk摘要失败: {str(e)}")
        return "摘要生成失败"


async def generate_chunk_summary_batch(chunks_list: List[List[str]]) -> List[str]:
    """
    Generate summaries for multiple chunk lists in batch.
    
    Args:
        chunks_list: List of chunk lists
    
    Returns:
        List of summaries
    """
    tasks = [generate_chunk_summary(chunks) for chunks in chunks_list]
    return await asyncio.gather(*tasks)


if __name__ == "__main__":
    # 测试代码
    test_chunks = [
        "这是第一段测试内容，讲述了关于机器学习的基础知识。",
        "第二段内容介绍了深度学习的应用场景和发展历史。",
        "第三段讨论了自然语言处理技术的最新进展。"
    ]
    
    print("开始生成chunk摘要...")
    summary = asyncio.run(generate_chunk_summary(test_chunks))
    print(f"\n生成的摘要：\n{summary}")
