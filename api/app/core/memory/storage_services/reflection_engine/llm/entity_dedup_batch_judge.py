"""子问题 3 · 方案B LLM层：分组去重判定
使用 entity_dedup_batch.jinja2 模板，一次性找出同类型实体中的所有重复对。
"""
import logging
import os
from typing import Any, Dict, List, Tuple

from pydantic import BaseModel
from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

# 加载模板
_prompt_dir = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "utils", "prompt", "prompts"
))
_prompt_env = Environment(loader=FileSystemLoader(_prompt_dir))


class DedupResultItem(BaseModel):
    """单个重复对结果"""
    pair: List[int]             # [1, 3] 索引从1开始
    confidence: float = 0.9
    reason: str = ""


class BatchDedupOutput(BaseModel):
    """LLM 分组判定输出"""
    results: List[DedupResultItem] = []


async def judge_batch_dedup(
    llm_client: Any,
    entities: List[Dict],
    entity_type: str,
    language: str = "zh",
) -> List[Tuple[int, int, float, str]]:
    """分组 LLM 判定：一次性找出所有重复对

    Args:
        llm_client: LLM 客户端
        entities: 同类型实体列表（含 name, description, aliases）
        entity_type: 实体类型（如 "Person"）
        language: "zh" | "en"

    Returns:
        [(idx_a, idx_b, confidence, reason), ...] 索引从0开始（内部已转换）
    """
    from app.core.memory.storage_services.extraction_engine.steps.base import call_structured

    try:
        template = _prompt_env.get_template("entity_dedup_batch.jinja2")
        rendered_prompt = template.render(
            entities=entities,
            entity_type=entity_type,
            language=language,
        )

        messages = [{"role": "user", "content": rendered_prompt}]
        response = await call_structured(llm_client, messages, BatchDedupOutput)

        if not isinstance(response, BatchDedupOutput):
            return []

        # 校验 + 转换索引 + 去重（每个实体最多出现在一个配对中）
        results = []
        seen_entities = set()
        for item in response.results:
            if len(item.pair) != 2:
                continue
            idx_a, idx_b = item.pair[0] - 1, item.pair[1] - 1  # 转为0-based
            if not (0 <= idx_a < len(entities) and 0 <= idx_b < len(entities)):
                continue
            if idx_a in seen_entities or idx_b in seen_entities:
                continue
            seen_entities.add(idx_a)
            seen_entities.add(idx_b)
            results.append((idx_a, idx_b, item.confidence, item.reason))

        return results
    except Exception as e:
        logger.error(f"反思引擎 实体去重方案B LLM 分组判定失败: {e}")
        return []