"""子问题 3 · 方案A LLM层：单对去重判定
优先调用实体去重专用模型；不可用或响应异常时回退原 LLM。
"""
import asyncio
import json
import logging
import os
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel
from jinja2 import Environment, FileSystemLoader

from app.core.memory.storage_services.reflection_engine.bert.entity_dedup_client import (
    EntityDedupModelError,
    EntityDedupResult,
)
from app.core.memory.storage_services.reflection_engine.errors import (
    ReflectionBusinessError,
    ReflectionFailureReason,
    ReflectionModelType,
)

logger = logging.getLogger(__name__)

_prompt_dir = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "utils", "prompt", "prompts"
))
_prompt_env = Environment(loader=FileSystemLoader(_prompt_dir))


class DedupLLMDecision(BaseModel):
    """单对去重判定结果（专用模型或 LLM 兜底）"""
    same_entity: bool           # 是否同一实体
    confidence: Optional[float] = None  # LLM 置信度；专用模型路径留空
    winner_id: str = "a"        # 保留哪个: "a" | "b"
    merged_name: str = ""       # 合并后主名
    new_aliases: List[str] = []  # 建议保留或新增的别名
    reason: str = ""            # 判定理由或审计说明
    outcome: str = "reject"     # merge | reject | deferred
    backend: str = "llm"        # entity_dedup_model | llm
    fallback_reason: Optional[str] = None
    reason_code: Optional[str] = None
    request_ms: int = 0


class DedupJudgeOutput(BaseModel):
    """LLM 结构化输出（对应 entity_dedup_reflection.jinja2 的 Output format）"""
    same_entity: bool
    canonical_idx: int          # 0=A, 1=B
    new_name: Optional[str] = None      # 合并后主名；same_entity=false 时为 None
    new_aliases: List[str] = []         # 合并后新增/保留别名；same_entity=false 时为 []
    confidence: float
    reason: str


def _entity_payload(entity: Any) -> Dict[str, Any]:
    """实体对象 → 提示词/专用模型共用的字段快照。"""
    return {
        "name": entity.name,
        "entity_type": entity.entity_type,
        "description_summary": getattr(entity, "description_summary", ""),
        "description": getattr(entity, "description", ""),
        "aliases": getattr(entity, "aliases", []),
    }


def _decision_from_model(result: EntityDedupResult, request_ms: int) -> DedupLLMDecision:
    """专用模型结果 → 内部判定；不套用 LLM 置信度阈值。"""
    return DedupLLMDecision(
        same_entity=result.should_merge,
        merged_name=result.new_name or "",
        new_aliases=result.merged_aliases or [],
        reason="专用模型判定合并" if result.should_merge else "专用模型判定不合并",
        outcome="merge" if result.should_merge else "reject",
        backend="entity_dedup_model",
        request_ms=request_ms,
    )


async def _judge_with_llm(
    llm_client: Any,
    entity_a: Dict[str, Any],
    entity_b: Dict[str, Any],
    language: str,
) -> DedupLLMDecision:
    """原 LLM 单对判定；调用/解析失败抛 ReflectionBusinessError。"""
    template = _prompt_env.get_template("entity_dedup_reflection.jinja2")
    rendered_prompt = template.render(
        entity_a=entity_a,
        entity_b=entity_b,
        language=language,
        json_schema=json.dumps(DedupJudgeOutput.model_json_schema(), indent=2),
    )

    messages = [{"role": "user", "content": rendered_prompt}]
    started = time.perf_counter()
    try:
        response = await llm_client.call_structured(messages, DedupJudgeOutput)
    except Exception as exc:
        logger.error("LLM 去重模型调用失败", exc_info=True)
        raise ReflectionBusinessError(
            ReflectionFailureReason.MODEL_CALL_FAILED,
            "entity_dedup_model_call",
            model_type=ReflectionModelType.LLM,
        ) from exc
    request_ms = int((time.perf_counter() - started) * 1000)

    if not isinstance(response, DedupJudgeOutput):
        logger.error("LLM 去重返回结构无效")
        raise ReflectionBusinessError(
            ReflectionFailureReason.RESULT_PARSE_FAILED,
            "entity_dedup_result_parse",
            model_type=ReflectionModelType.LLM,
        )
    if response.canonical_idx not in (0, 1) or not (0 <= response.confidence <= 1):
        logger.error("LLM 去重返回字段校验失败")
        raise ReflectionBusinessError(
            ReflectionFailureReason.RESULT_PARSE_FAILED,
            "entity_dedup_result_parse",
            model_type=ReflectionModelType.LLM,
        )

    # new_name 为空时回退到 canonical_idx 对应实体名
    fallback_name = entity_a["name"] if response.canonical_idx == 0 else entity_b["name"]
    return DedupLLMDecision(
        same_entity=response.same_entity,
        confidence=response.confidence,
        winner_id="a" if response.canonical_idx == 0 else "b",
        merged_name=(response.new_name or fallback_name),
        new_aliases=response.new_aliases or [],
        reason=response.reason,
        outcome="merge" if response.same_entity else "reject",
        backend="llm",
        request_ms=request_ms,
    )


async def judge_pair_for_dedup(
    llm_client: Any,
    entity_a: Any,
    entity_b: Any,
    language: str = "zh",
    dedup_client: Any = None,
    sample_id: str = "",
) -> DedupLLMDecision:
    """对单对候选优先调用专用模型，异常时回退原 LLM。

    entity_a/entity_b 需有 name, entity_type, description, description_summary, aliases 属性。
    专用模型调用/响应异常才启动 LLM 兜底；每对最多一次兜底。
    """
    payload_a = _entity_payload(entity_a)
    payload_b = _entity_payload(entity_b)

    fallback_reason: Optional[str] = None
    if dedup_client is not None:
        started = time.perf_counter()
        try:
            result = await dedup_client.judge_pair(sample_id, payload_a, payload_b)
        except EntityDedupModelError as exc:
            fallback_reason = exc.reason
            logger.warning(
                "实体去重专用模型不可用，改用 LLM 兜底 sample_id=%s reason=%s",
                sample_id, fallback_reason,
            )
        except Exception:
            # 未预期的客户端错误仍只影响当前候选；取消异常不在此列（BaseException）。
            fallback_reason = "model_call_failed"
            logger.warning(
                "实体去重专用模型调用异常，改用 LLM 兜底 sample_id=%s",
                sample_id, exc_info=True,
            )
        else:
            return _decision_from_model(
                result, int((time.perf_counter() - started) * 1000)
            )

    try:
        decision = await _judge_with_llm(llm_client, payload_a, payload_b, language)
    except ReflectionBusinessError as exc:
        logger.error(
            "LLM 去重兜底失败，暂缓该候选对 sample_id=%s reason_code=%s",
            sample_id, exc.reason_code.value,
        )
        return DedupLLMDecision(
            same_entity=False,
            reason="LLM 兜底调用或解析失败，暂缓处理",
            outcome="deferred",
            backend="llm",
            fallback_reason=fallback_reason,
            reason_code=exc.reason_code.value,
        )

    decision.fallback_reason = fallback_reason
    return decision


async def judge_batch(
    llm_client: Any,
    pairs: List,
    concurrency: int = 5,
    language: str = "zh",
    dedup_client: Any = None,
) -> List[Tuple[Any, DedupLLMDecision]]:
    """批量判定，带并发控制；一个许可覆盖“专用模型 → 必要时 LLM 兜底”。"""
    sem = asyncio.Semaphore(concurrency)

    async def _judge_one(pair):
        async with sem:
            # 直接从 pair 构造简单对象供提示词/专用模型使用
            entity_a = SimpleNamespace(
                name=pair.a_name, entity_type=pair.entity_type,
                description_summary=pair.a_desc_summary,
                description=pair.a_desc, aliases=pair.a_aliases or [],
            )
            entity_b = SimpleNamespace(
                name=pair.b_name, entity_type=pair.entity_type,
                description_summary=pair.b_desc_summary,
                description=pair.b_desc, aliases=pair.b_aliases or [],
            )
            decision = await judge_pair_for_dedup(
                llm_client, entity_a, entity_b, language,
                dedup_client=dedup_client,
                sample_id=f"{pair.a_id}:{pair.b_id}",
            )
            return (pair, decision)

    tasks = [_judge_one(p) for p in pairs]
    return await asyncio.gather(*tasks)
