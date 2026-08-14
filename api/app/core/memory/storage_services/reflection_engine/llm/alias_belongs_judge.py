"""反思阶段 · 别名归并 LLM 校验
使用 alias_belongs_judge.jinja2 模板，对一个规范实体下的全部候选别名一次性判定，
为每个候选输出 0.0-1.0 置信度，按阈值二分为 merge / drop。
"""
import logging
import os
from typing import Any, Dict, List

from pydantic import BaseModel, Field
from jinja2 import Environment, FileSystemLoader

from app.core.memory.storage_services.reflection_engine.errors import (
    ReflectionBusinessError,
    ReflectionFailureReason,
    ReflectionModelType,
)

logger = logging.getLogger(__name__)

# 加载模板（与 entity_dedup_batch_judge 同路径）
_prompt_dir = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "utils", "prompt", "prompts"
))
_prompt_env = Environment(loader=FileSystemLoader(_prompt_dir))


class AliasJudgeItem(BaseModel):
    """单个候选别名判定结果"""
    alias_index: int                 # 候选序号，从 1 开始
    confidence: float = 0.0          # 0.0-1.0
    reason: str = ""


class AliasBatchJudgeOutput(BaseModel):
    """LLM 分组判定输出"""
    results: List[AliasJudgeItem] = Field(default_factory=list)


async def judge_alias_belongs(
    llm_client: Any,
    canonical: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    threshold: float = 0.9,
    language: str = "zh",
) -> List[Dict[str, Any]]:
    """对一个规范实体的全部候选别名做分组判定。

    Args:
        llm_client: LLM 客户端（同 entity_dedup 用法）
        canonical: 规范实体 dict，含 name/entity_type/description/description_summary/aliases
        candidates: 候选别名列表，每项含 alias_id/alias_name/alias_entity_type/
                    alias_description/alias_description_summary/aliases
        threshold: merge 阈值，confidence >= threshold 判 merge
        language: zh / en

    Returns:
        已判定的候选列表，每项：{alias_id, decision("merge"|"drop"), confidence, reason}。
        未拿到有效判定（LLM 异常/缺失/越界/非法分数）的候选不在返回中 —— 调用方据此 skip（保留边）。
    """
    if not candidates:
        return []

    template = _prompt_env.get_template("alias_belongs_judge.jinja2")
    rendered_prompt = template.render(
        canonical_entity=canonical,
        candidates=candidates,
        language=language,
    )
    messages = [{"role": "user", "content": rendered_prompt}]
    try:
        response = await llm_client.call_structured(messages, AliasBatchJudgeOutput)
    except Exception as exc:
        logger.error("[AliasJudge] 别名校验模型调用失败", exc_info=True)
        raise ReflectionBusinessError(
            ReflectionFailureReason.MODEL_CALL_FAILED,
            "alias_merge_model_call",
            model_type=ReflectionModelType.LLM,
        ) from exc

    if not isinstance(response, AliasBatchJudgeOutput):
        logger.error("[AliasJudge] 别名校验返回结构无效")
        raise ReflectionBusinessError(
            ReflectionFailureReason.RESULT_PARSE_FAILED,
            "alias_merge_result_parse",
            model_type=ReflectionModelType.LLM,
        )

    # alias_index(1-based) → AliasJudgeItem
    by_index: Dict[int, AliasJudgeItem] = {}
    for item in response.results:
        if 1 <= item.alias_index <= len(candidates):
            by_index[item.alias_index] = item

    decided: List[Dict[str, Any]] = []
    for i, cand in enumerate(candidates, start=1):
        item = by_index.get(i)
        if item is None:
            continue  # 未返回 → skip
        try:
            conf = float(item.confidence)
        except (TypeError, ValueError):
            continue  # 非法分数 → skip
        if not (0.0 <= conf <= 1.0):
            continue  # 越界 → skip
        decided.append({
            "alias_id": cand["alias_id"],
            "decision": "merge" if conf >= threshold else "drop",
            "confidence": conf,
            "reason": (item.reason or "")[:200],
        })
    return decided
