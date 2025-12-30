"""
Generate insights from RAG chunks.

This module provides functionality to analyze chunk content and generate insights using LLM.
"""

import asyncio
from collections import Counter
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


class ChunkInsight(BaseModel):
    """Pydantic model for chunk insight."""
    insight: str = Field(..., description="对chunk内容的深度洞察分析")


class DomainClassification(BaseModel):
    """Pydantic model for domain classification."""
    domain: str = Field(
        ...,
        description="内容所属的领域分类",
        examples=["技术", "商业", "教育", "生活", "娱乐", "健康", "其他"]
    )


async def classify_chunk_domain(chunk: str) -> str:
    """
    Classify a chunk into a specific domain.
    
    Args:
        chunk: Chunk content string
    
    Returns:
        Domain name
    """
    try:
        llm_client = _get_llm_client()
        
        prompt = f"""请将以下文本内容归类到最合适的领域中。

可选领域及其关键词：
- 技术：编程、软件、硬件、算法、数据、网络、系统、开发、工程等
- 商业：市场、销售、管理、财务、投资、创业、营销、战略等
- 教育：学习、课程、培训、教学、知识、技能、考试、研究等
- 生活：日常、家庭、饮食、购物、旅行、休闲、娱乐等
- 娱乐：游戏、电影、音乐、体育、艺术、文化等
- 健康：医疗、养生、运动、心理、保健、疾病等
- 其他：无法归入以上类别的内容

文本内容: {chunk[:500]}...

请直接返回最合适的领域名称。"""
        
        messages = [
            {"role": "system", "content": "你是一个专业的文本分类助手。请仔细分析文本内容，选择最合适的领域分类。"},
            {"role": "user", "content": prompt}
        ]
        
        classification = await llm_client.response_structured(
            messages=messages,
            response_model=DomainClassification
        )
        
        return classification.domain if classification else "其他"
        
    except Exception as e:
        business_logger.error(f"分类chunk领域失败: {str(e)}")
        return "其他"


async def analyze_domain_distribution(chunks: List[str], max_chunks: int = 20) -> Dict[str, float]:
    """
    Analyze the domain distribution of chunks.
    
    Args:
        chunks: List of chunk content strings
        max_chunks: Maximum number of chunks to analyze
    
    Returns:
        Dictionary of domain -> percentage
    """
    if not chunks:
        return {}
    
    try:
        # 限制分析的chunk数量
        chunks_to_analyze = chunks[:max_chunks]
        
        # 为每个chunk分类
        domain_counts = Counter()
        for chunk in chunks_to_analyze:
            domain = await classify_chunk_domain(chunk)
            domain_counts[domain] += 1
        
        # 计算百分比
        total = sum(domain_counts.values())
        domain_distribution = {
            domain: count / total
            for domain, count in domain_counts.items()
        }
        
        # 按百分比降序排序
        return dict(sorted(domain_distribution.items(), key=lambda x: x[1], reverse=True))
        
    except Exception as e:
        business_logger.error(f"分析领域分布失败: {str(e)}")
        return {}


async def generate_chunk_insight(chunks: List[str], max_chunks: int = 15) -> str:
    """
    Generate insights from the given chunks.
    
    Args:
        chunks: List of chunk content strings
        max_chunks: Maximum number of chunks to analyze
    
    Returns:
        A comprehensive insight report
    """
    if not chunks:
        business_logger.warning("没有提供chunk内容用于生成洞察")
        return "暂无足够数据生成洞察报告"
    
    try:
        # 1. 分析领域分布
        domain_dist = await analyze_domain_distribution(chunks, max_chunks=max_chunks)
        
        # 2. 统计基本信息
        total_chunks = len(chunks)
        avg_length = sum(len(chunk) for chunk in chunks) / total_chunks if total_chunks > 0 else 0
        
        # 3. 构建洞察prompt
        prompt_parts = []
        
        if domain_dist:
            top_domains = ", ".join([f"{k}({v:.0%})" for k, v in list(domain_dist.items())[:3]])
            prompt_parts.append(f"- 内容领域分布: {top_domains}")
        
        prompt_parts.append(f"- 内容规模: 共{total_chunks}个知识片段，平均长度{avg_length:.0f}字")
        
        # 添加部分chunk内容作为参考
        sample_chunks = chunks[:5]
        sample_content = "\n".join([f"示例{i+1}: {chunk[:200]}..." for i, chunk in enumerate(sample_chunks)])
        prompt_parts.append(f"\n内容示例:\n{sample_content}")
        
        system_prompt = """你是一位专业的知识内容分析师。你的任务是根据提供的信息，生成一段简洁、有洞察力的分析报告。

重要规则：
1. 报告需要将所有要点流畅地串联成一个段落
2. 语言风格要专业、客观，同时易于理解
3. 不要添加任何额外的解释或标题，直接输出报告内容
4. 基于提供的数据和示例内容进行分析，不要编造信息
5. 重点关注内容的主题、特点和价值
6. 报告长度控制在150-200字

例如，如果输入是：
- 内容领域分布: 技术(60%), 商业(25%), 教育(15%)
- 内容规模: 共50个知识片段，平均长度320字
内容示例: [示例内容...]

你的输出应该类似：
"该知识库主要聚焦于技术领域(60%)，涵盖商业(25%)和教育(15%)相关内容。共包含50个知识片段，平均每个片段约320字，内容详实。从示例来看，内容涉及[具体主题]，体现了[特点]，对[目标用户]具有较高的参考价值。"
"""
        
        user_prompt = "\n".join(prompt_parts)
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        # 调用LLM生成洞察
        llm_client = _get_llm_client()
        response = await llm_client.chat(messages=messages)
        
        insight = response.content.strip()
        business_logger.info(f"成功生成chunk洞察，分析了 {min(len(chunks), max_chunks)} 个片段")
        
        return insight
        
    except Exception as e:
        business_logger.error(f"生成chunk洞察失败: {str(e)}")
        return "洞察生成失败"


if __name__ == "__main__":
    # 测试代码
    test_chunks = [
        "Python是一种高级编程语言，以其简洁的语法和强大的功能而闻名。它广泛应用于Web开发、数据分析、人工智能等领域。",
        "机器学习算法可以从数据中自动学习模式，无需显式编程。常见的算法包括决策树、随机森林、神经网络等。",
        "深度学习是机器学习的一个分支，使用多层神经网络来学习数据的层次化表示。它在图像识别、语音识别等任务中表现出色。",
        "自然语言处理技术使计算机能够理解和生成人类语言。应用包括机器翻译、情感分析、文本摘要等。",
        "数据科学结合了统计学、计算机科学和领域知识，用于从数据中提取有价值的洞察。"
    ]
    
    print("开始生成chunk洞察...")
    insight = asyncio.run(generate_chunk_insight(test_chunks))
    print(f"\n生成的洞察：\n{insight}")
