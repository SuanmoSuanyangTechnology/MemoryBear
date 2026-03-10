"""
Generate memory insight report for RAG chunks using memory_insight.jinja2 prompt template.

The memory_insight.jinja2 template produces a four-section report:
  【总体概述】 → memory_insight
  【行为模式】 → behavior_pattern
  【关键发现】 → key_findings
  【成长轨迹】 → growth_trajectory

generate_chunk_insight() returns the full raw text (stored in end_user.memory_insight).
generate_chunk_insight_sections() returns a dict with all four fields for richer storage.
"""

import asyncio
import os
import re
from collections import Counter
from typing import Dict, List, Optional

from app.core.logging_config import get_business_logger
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.db import get_db_context

business_logger = get_business_logger()

DEFAULT_LLM_ID = os.getenv("SELECTED_LLM_ID", "openai/qwen-plus")


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


# ── Domain analysis helpers (kept for building prompt inputs) ─────────────────

async def _classify_domain(chunk: str, llm_client) -> str:
    """Classify a single chunk into a domain category."""
    from pydantic import BaseModel, Field

    class _Domain(BaseModel):
        domain: str = Field(..., description="领域分类")

    try:
        prompt = (
            "请将以下文本归类到最合适的领域（技术/商业/教育/生活/娱乐/健康/其他）。\n\n"
            f"文本: {chunk[:500]}\n\n直接返回领域名称。"
        )
        result = await llm_client.response_structured(
            messages=[{"role": "user", "content": prompt}],
            response_model=_Domain,
        )
        return result.domain if result else "其他"
    except Exception:
        return "其他"


async def _build_insight_inputs(
    chunks: List[str],
    max_chunks: int,
    end_user_id: Optional[str],
) -> Dict[str, Optional[str]]:
    """
    Derive domain_distribution, active_periods, social_connections strings
    to feed into the memory_insight.jinja2 template.
    """
    llm_client = _get_llm_client(end_user_id)
    chunks_sample = chunks[:max_chunks]

    # Domain distribution
    domain_counts: Counter = Counter()
    for chunk in chunks_sample:
        domain = await _classify_domain(chunk, llm_client)
        domain_counts[domain] += 1

    total = sum(domain_counts.values()) or 1
    domain_distribution = ", ".join(
        f"{d}({c / total:.0%})" for d, c in domain_counts.most_common(3)
    )

    return {
        "domain_distribution": domain_distribution,
        "active_periods": None,      # RAG模式暂无时间维度数据
        "social_connections": None,  # RAG模式暂无社交关联数据
    }


# ── Section parser ────────────────────────────────────────────────────────────

_ZH_SECTIONS = {
    "memory_insight": r"【总体概述】(.*?)(?=【|$)",
    "behavior_pattern": r"【行为模式】(.*?)(?=【|$)",
    "key_findings": r"【关键发现】(.*?)(?=【|$)",
    "growth_trajectory": r"【成长轨迹】(.*?)(?=【|$)",
}

_EN_SECTIONS = {
    "memory_insight": r"【Overview】(.*?)(?=【|$)",
    "behavior_pattern": r"【Behavior Pattern】(.*?)(?=【|$)",
    "key_findings": r"【Key Findings】(.*?)(?=【|$)",
    "growth_trajectory": r"【Growth Trajectory】(.*?)(?=【|$)",
}


def _parse_sections(text: str, language: str = "zh") -> Dict[str, str]:
    """Extract the four sections from the LLM output."""
    patterns = _ZH_SECTIONS if language == "zh" else _EN_SECTIONS
    result = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.DOTALL)
        result[key] = match.group(1).strip() if match else ""
    return result


# ── Public API ────────────────────────────────────────────────────────────────

async def generate_chunk_insight(
    chunks: List[str],
    max_chunks: int = 15,
    end_user_id: Optional[str] = None,
    language: str = "zh",
) -> str:
    """
    Generate a memory insight report from RAG chunks.

    Returns the full raw report text (suitable for end_user.memory_insight).
    Use generate_chunk_insight_sections() when you need all four dimensions.
    """
    sections = await generate_chunk_insight_sections(
        chunks=chunks,
        max_chunks=max_chunks,
        end_user_id=end_user_id,
        language=language,
    )
    return sections.get("memory_insight") or sections.get("_raw", "洞察生成失败")


async def generate_chunk_insight_sections(
    chunks: List[str],
    max_chunks: int = 15,
    end_user_id: Optional[str] = None,
    language: str = "zh",
) -> Dict[str, str]:
    """
    Generate a four-section memory insight report from RAG chunks.

    Returns a dict with keys:
        memory_insight, behavior_pattern, key_findings, growth_trajectory
    (plus '_raw' containing the full LLM output for debugging)
    """
    if not chunks:
        business_logger.warning("没有提供chunk内容用于生成洞察")
        empty = {k: "" for k in ("memory_insight", "behavior_pattern", "key_findings", "growth_trajectory")}
        empty["_raw"] = "暂无足够数据生成洞察报告"
        return empty

    try:
        from app.core.memory.utils.prompt.prompt_utils import render_memory_insight_prompt

        # Build template inputs from chunk analysis
        inputs = await _build_insight_inputs(chunks, max_chunks, end_user_id)

        rendered_prompt = await render_memory_insight_prompt(
            domain_distribution=inputs["domain_distribution"],
            active_periods=inputs["active_periods"],
            social_connections=inputs["social_connections"],
            language=language,
        )

        messages = [{"role": "user", "content": rendered_prompt}]
        llm_client = _get_llm_client(end_user_id)
        response = await llm_client.chat(messages=messages)
        raw_text = response.content.strip() if response and response.content else ""

        sections = _parse_sections(raw_text, language=language)
        sections["_raw"] = raw_text

        business_logger.info(
            f"成功生成chunk洞察四维度，分析了 {min(len(chunks), max_chunks)} 个片段"
        )
        return sections

    except Exception as e:
        business_logger.error(f"生成chunk洞察失败: {str(e)}")
        empty = {k: "" for k in ("memory_insight", "behavior_pattern", "key_findings", "growth_trajectory")}
        empty["_raw"] = "洞察生成失败"
        return empty
