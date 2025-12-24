"""
Extract tags from RAG chunks.

This module provides functionality to extract meaningful tags from chunk content using LLM.
"""

import asyncio
from collections import Counter
from typing import List, Tuple

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


class ExtractedTags(BaseModel):
    """Pydantic model for extracted tags."""
    tags: List[str] = Field(..., description="从文本中提取的关键标签列表")


class ExtractedPersona(BaseModel):
    """Pydantic model for extracted persona."""
    personas: List[str] = Field(..., description="从文本中提取的人物形象列表，如'产品设计师'、'旅行爱好者'等")


async def extract_chunk_tags(chunks: List[str], max_tags: int = 10, max_chunks: int = 10) -> List[Tuple[str, int]]:
    """
    Extract meaningful tags from the given chunks.
    
    Args:
        chunks: List of chunk content strings
        max_tags: Maximum number of tags to return (default: 10)
        max_chunks: Maximum number of chunks to process (default: 10)
    
    Returns:
        List of tuples (tag, frequency), sorted by frequency in descending order
    """
    if not chunks:
        business_logger.warning("没有提供chunk内容用于提取标签")
        return []
    
    try:
        # 限制处理的chunk数量
        chunks_to_process = chunks[:max_chunks]
        
        # 构建prompt
        system_prompt = (
            "你是一位专业的文本分析专家，擅长从文本中提取关键标签。请遵循以下规则：\n\n"
            "1. **提取核心概念**: 识别文本中最重要的名词、专业术语、主题词；\n"
            "2. **过滤无意义词**: 排除过于宽泛的词（如'内容'、'信息'、'数据'）；\n"
            "3. **保持具体性**: 优先选择具体的、有代表性的词语；\n"
            "4. **标签数量**: 提取5-15个最具代表性的标签；\n"
            "5. **去重合并**: 语义相近的标签只保留一个最核心的。\n\n"
            "标签应该是名词或名词短语，能够准确概括文本的核心内容。"
        )
        
        llm_client = _get_llm_client()
        
        # 为每个chunk单独提取标签，然后统计频率
        all_tags = []
        for chunk in chunks_to_process:
            single_chunk_prompt = f"请从以下文本中提取关键标签：\n\n{chunk}"
            single_messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": single_chunk_prompt},
            ]
            
            try:
                single_response = await llm_client.response_structured(
                    messages=single_messages,
                    response_model=ExtractedTags
                )
                all_tags.extend(single_response.tags)
            except Exception as e:
                business_logger.warning(f"处理单个chunk时出错: {str(e)}")
                continue
        
        # 统计标签频率
        tag_counter = Counter(all_tags)
        
        # 获取最常见的标签，限制数量
        most_common_tags = tag_counter.most_common(max_tags)
        
        business_logger.info(f"成功提取 {len(most_common_tags)} 个标签，处理了 {len(chunks_to_process)} 个片段")
        
        return most_common_tags
        
    except Exception as e:
        business_logger.error(f"提取chunk标签失败: {str(e)}")
        return []


async def extract_chunk_tags_with_frequency(chunks: List[str], max_tags: int = 10) -> List[Tuple[str, int]]:
    """
    Extract tags with actual frequency calculation across all chunks.
    
    This is an alias for extract_chunk_tags for backward compatibility.
    
    Args:
        chunks: List of chunk content strings
        max_tags: Maximum number of tags to return
    
    Returns:
        List of tuples (tag, frequency), sorted by frequency
    """
    return await extract_chunk_tags(chunks, max_tags=max_tags, max_chunks=len(chunks))


async def extract_chunk_persona(chunks: List[str], max_personas: int = 5, max_chunks: int = 20) -> List[str]:
    """
    Extract persona (人物形象) from the given chunks.
    
    Args:
        chunks: List of chunk content strings
        max_personas: Maximum number of personas to return (default: 5)
        max_chunks: Maximum number of chunks to process (default: 20)
    
    Returns:
        List of persona strings like "产品设计师", "旅行爱好者", "摄影发烧友"
    """
    if not chunks:
        business_logger.warning("没有提供chunk内容用于提取人物形象")
        return []
    
    try:
        # 限制处理的chunk数量
        chunks_to_process = chunks[:max_chunks]
        
        # 合并chunk内容
        combined_content = "\n\n".join([f"片段{i+1}: {chunk}" for i, chunk in enumerate(chunks_to_process)])
        
        # 构建prompt
        system_prompt = (
            "你是一位专业的人物画像分析专家，擅长从文本中提取人物形象标签。请遵循以下规则：\n\n"
            "1. **职业身份**: 识别职业、专业领域（如'产品设计师'、'软件工程师'、'创业者'）；\n"
            "2. **兴趣爱好**: 提取核心兴趣和爱好（如'旅行爱好者'、'摄影发烧友'、'咖啡控'）；\n"
            "3. **生活方式**: 概括生活态度和习惯（如'极简主义者'、'户外探险家'、'阅读爱好者'）；\n"
            "4. **个性特征**: 提炼显著的性格特点（如'思考者'、'行动派'、'完美主义者'）；\n"
            "5. **数量控制**: 提取3-8个最具代表性的人物形象标签；\n"
            "6. **简洁明确**: 每个标签应该是简短的名词或名词短语（2-6个字）。\n\n"
            "人物形象标签应该能够准确刻画这个人的核心特征和身份定位。"
        )
        
        user_prompt = f"请从以下文本中提取人物形象标签：\n\n{combined_content}"
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        
        # 调用LLM提取人物形象
        llm_client = _get_llm_client()
        structured_response = await llm_client.response_structured(
            messages=messages,
            response_model=ExtractedPersona
        )
        
        # 去重并限制数量
        personas = list(dict.fromkeys(structured_response.personas))[:max_personas]
        
        business_logger.info(f"成功提取 {len(personas)} 个人物形象，处理了 {len(chunks_to_process)} 个片段")
        
        return personas
        
    except Exception as e:
        business_logger.error(f"提取人物形象失败: {str(e)}")
        return []


if __name__ == "__main__":
    # 测试代码
    test_chunks = [
        "我是一名产品设计师，平时喜欢旅行和摄影。周末经常去户外徒步，探索新的风景。",
        "最近在学习咖啡拉花，已经能做出简单的图案了。每天早上都会给自己冲一杯手冲咖啡。",
        "喜欢阅读各类书籍，尤其是设计和心理学相关的。记录生活是我的习惯，用镜头捕捉美好瞬间。"
    ]
    
    print("开始提取chunk标签...")
    tags = asyncio.run(extract_chunk_tags(test_chunks))
    print("\n提取的标签：")
    for tag, freq in tags:
        print(f"- {tag} (频率: {freq})")
    
    print("\n" + "="*50)
    print("开始提取人物形象...")
    personas = asyncio.run(extract_chunk_persona(test_chunks))
    print("\n提取的人物形象：")
    for persona in personas:
        print(f"- {persona}")
