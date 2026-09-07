from __future__ import annotations

from .budget import DEFAULT_LIMITS, Limits
from .llm import StructuredLLM
from .recall import MemoryStream
from .schemas import EvidenceGate, QueryTerms

_GATE_SYSTEM = """判断记忆是否足以支撑关于当事人未来的推演。检查主题词命中是否真在谈问题主题，以及主语是否是当事人本人。别人的经历不能作为当事人的推演基础。不要求记忆已经写明未来计划，但必须至少有当事人的经历、态度、动作或相关方。covered_aspects 为空时必须拒绝。"""


async def check_evidence(llm: StructuredLLM, question: str, stream: MemoryStream, query_terms: QueryTerms, limits: Limits = DEFAULT_LIMITS) -> EvidenceGate:
    if not query_terms.core_terms:
        return EvidenceGate(has_topical_evidence=False, reason="未能提取可核验的主题关键词")
    hits = stream.term_hits(query_terms.core_terms)
    if not any(hits.values()):
        return EvidenceGate(
            has_topical_evidence=False,
            missing_aspects=query_terms.core_terms,
            reason=f"{len(stream.statements)} 条记忆中没有陈述涉及问题主题",
        )
    lines: list[str] = []
    for term, statements in hits.items():
        lines.append(f"关键词「{term}」命中 {len(statements)} 条：")
        lines.extend(f"[{item.id}] {item.text[:limits.recall_render_chars]}" for item in statements[: limits.gate_samples_per_term])
    ids = list(dict.fromkeys(item.id for values in hits.values() for item in values))
    try:
        result = await llm.structured(_GATE_SYSTEM, f"问题：{question}\n" + "\n".join(lines), EvidenceGate, temperature=0.1, max_tokens=limits.gate_tokens)
        assert isinstance(result, EvidenceGate)
    except Exception as exc:
        return EvidenceGate(has_topical_evidence=False, reason=f"证据判定失败，已保守拒答：{exc}")
    result.topical_statement_ids = ids
    if result.has_topical_evidence and not result.covered_aspects:
        result.has_topical_evidence = False
        result.reason = f"{result.reason}（未指出实际覆盖方面）".strip()
    return result
