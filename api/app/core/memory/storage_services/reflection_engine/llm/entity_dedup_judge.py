"""子问题 3 · 方案A LLM层：单对去重判定
渲染 entity_dedup_reflection.jinja2 + call_structured。
"""
import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel
from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

_prompt_dir = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "utils", "prompt", "prompts"
))
_prompt_env = Environment(loader=FileSystemLoader(_prompt_dir))


class DedupLLMDecision(BaseModel):
    """LLM 去重判定结果"""
    same_entity: bool           # 是否同一实体
    confidence: float           # 置信度 0~1
    winner_id: str = "a"        # 保留哪个: "a" | "b"（canonical_idx 映射）
    merged_name: str = ""       # 合并后主名（取自 LLM new_name）
    new_aliases: List[str] = []  # LLM 给出的新别名
    reason: str = ""            # 判定理由


class DedupJudgeOutput(BaseModel):
    """LLM 结构化输出（对应 entity_dedup_reflection.jinja2 的 Output format）"""
    same_entity: bool
    canonical_idx: int          # 0=A, 1=B
    new_name: Optional[str] = None      # 合并后主名；same_entity=false 时为 None
    new_aliases: List[str] = []         # 合并后新增/保留别名；same_entity=false 时为 []
    confidence: float
    reason: str


async def judge_pair_for_dedup(
    llm_client: Any,
    entity_a: Any,
    entity_b: Any,
    scores: Dict,
    language: str = "zh",
) -> Optional[DedupLLMDecision]:
    """对单对候选调用 LLM 判定

    直接渲染 entity_dedup_reflection.jinja2，把两路召回已计算好的分数传入。
    entity_a/entity_b 需有 name, entity_type, description, description_summary, aliases 属性。
    """
    try:
        from app.core.memory.storage_services.extraction_engine.steps.base import call_structured

        template = _prompt_env.get_template("entity_dedup_reflection.jinja2")
        rendered_prompt = template.render(
            entity_a={
                "name": entity_a.name,
                "entity_type": entity_a.entity_type,
                "description_summary": getattr(entity_a, "description_summary", ""),
                "description": getattr(entity_a, "description", ""),
                "aliases": getattr(entity_a, "aliases", []),
            },
            entity_b={
                "name": entity_b.name,
                "entity_type": entity_b.entity_type,
                "description_summary": getattr(entity_b, "description_summary", ""),
                "description": getattr(entity_b, "description", ""),
                "aliases": getattr(entity_b, "aliases", []),
            },
            language=language,
            json_schema=json.dumps(DedupJudgeOutput.model_json_schema(), indent=2),
        )

        messages = [{"role": "user", "content": rendered_prompt}]
        response = await call_structured(llm_client, messages, DedupJudgeOutput)

        if isinstance(response, DedupJudgeOutput):
            # new_name 为空时回退到 canonical_idx 对应实体名
            fallback_name = entity_a.name if response.canonical_idx == 0 else entity_b.name
            return DedupLLMDecision(
                same_entity=response.same_entity,
                confidence=response.confidence,
                winner_id="a" if response.canonical_idx == 0 else "b",
                merged_name=(response.new_name or fallback_name),
                new_aliases=response.new_aliases or [],
                reason=response.reason,
            )
        return None
    except Exception as e:
        logger.error(f"LLM 去重判定失败: {e}")
        return None


async def judge_batch(
    llm_client: Any,
    pairs: List,
    concurrency: int = 5,
    language: str = "zh",
) -> List[Tuple[Any, Optional[DedupLLMDecision]]]:
    """批量 LLM 判定，带并发控制

    Args:
        pairs: DedupCandidatePair 列表
        concurrency: 并发数
    """
    sem = asyncio.Semaphore(concurrency)

    async def _judge_one(pair):
        async with sem:
            # 直接从 pair 构造简单对象供模板渲染
            entity_a = type("E", (), {
                "name": pair.a_name, "entity_type": pair.entity_type,
                "description_summary": pair.a_desc_summary,
                "description": pair.a_desc, "aliases": pair.a_aliases or [],
            })()
            entity_b = type("E", (), {
                "name": pair.b_name, "entity_type": pair.entity_type,
                "description_summary": pair.b_desc_summary,
                "description": pair.b_desc, "aliases": pair.b_aliases or [],
            })()
            scores = {"sim_name": pair.sim_name, "sim_embed": pair.sim_embed}
            decision = await judge_pair_for_dedup(llm_client, entity_a, entity_b, scores, language)
            return (pair, decision)

    tasks = [_judge_one(p) for p in pairs]
    return await asyncio.gather(*tasks)