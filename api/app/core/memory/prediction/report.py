from __future__ import annotations

from .budget import DEFAULT_LIMITS, Limits
from .llm import StructuredLLM
from .recall import MemoryStream, tokenize
from .schemas import PredictionReport, ReportDraft, WorldState

_REPORT_SYSTEM = """根据推演生成克制的预测报告。只能从有 grounded_on 且 low_grounding=false 的事件推出结论。每条预测必须引用给定溯源池中的 statement id，并通过 simulation_artifacts 引用 T-01 形式的临时演化记录；不得把模型自评置信度描述为统计概率。若材料不足，evidence_sufficient=false 且 predictions 为空。"""
_PERSPECTIVE = "他者画像来自当事人的记忆，仅代表当事人视角。"
_CONFIDENCE = "置信度为模型基于当前记忆的自评，不是统计概率。"


def _lexically_related(statement: str, evidence: str) -> bool:
    left, right = tokenize(statement), tokenize(evidence)
    return bool(left and right and left.intersection(right))


async def build_report(llm: StructuredLLM, world: WorldState, stream: MemoryStream, topical_statement_ids: list[str], limits: Limits = DEFAULT_LIMITS) -> PredictionReport:
    grounded_events = [event for event in world.event_log() if not event.low_grounding]
    if not grounded_events:
        return PredictionReport(
            question=world.question,
            end_user_id=world.end_user_id,
            as_of=world.as_of,
            prediction_deadline=world.prediction_deadline,
            evidence_sufficient=False,
            insufficiency_reason="推演没有产生任何可由记忆支撑的事件",
        )
    event_ids = [item for event in grounded_events for item in event.grounded_on]
    pool = list(dict.fromkeys([*topical_statement_ids, *event_ids]))[: limits.grounding_pool]
    evidence = "\n".join(f"[{item}] {stream.by_id[item].text}" for item in pool if item in stream.by_id)
    events = "\n".join(
        f"T-{item.turn:02d} {item.actor} [{item.response_type}]：{item.action}；依据={item.grounded_on}"
        for item in grounded_events
    )
    prompt = f"问题：{world.question}\n历史记忆截至：{world.as_of}\n预测截止：{world.prediction_deadline}\n\n推演：\n{events}\n\n可用溯源池：\n{evidence}"
    try:
        draft = await llm.structured(_REPORT_SYSTEM, prompt, ReportDraft, temperature=0.2, max_tokens=limits.report_tokens)
        assert isinstance(draft, ReportDraft)
    except Exception as exc:
        return PredictionReport(question=world.question, end_user_id=world.end_user_id, as_of=world.as_of, prediction_deadline=world.prediction_deadline, evidence_sufficient=False, insufficiency_reason=f"报告生成失败：{exc}")
    valid = set(pool).intersection(stream.by_id)
    valid_artifacts = {f"T-{turn.turn:02d}" for turn in world.turns}
    for prediction in draft.predictions:
        prediction.grounded_on = [
            item for item in prediction.grounded_on
            if item in valid and _lexically_related(prediction.statement + " " + prediction.reasoning, stream.by_id[item].text)
        ]
        if not prediction.grounded_on:
            prediction.llm_self_rated_confidence = "low"
        prediction.simulation_artifacts = [
            item for item in prediction.simulation_artifacts if item in valid_artifacts
        ]
    if not draft.evidence_sufficient:
        draft.predictions = []
    caveats = list(dict.fromkeys([*draft.caveats, _PERSPECTIVE, _CONFIDENCE]))
    return PredictionReport(
        question=world.question, end_user_id=world.end_user_id, as_of=world.as_of,
        prediction_deadline=world.prediction_deadline,
        evidence_sufficient=draft.evidence_sufficient,
        insufficiency_reason=draft.insufficiency_reason, headline=draft.headline,
        predictions=draft.predictions, divergence_points=draft.divergence_points,
        caveats=caveats,
    )


def render_markdown(report: PredictionReport, world: WorldState | None, stream: MemoryStream | None) -> str:
    lines = [f"# {report.headline or '预测报告'}", "", f"- 问题：{report.question}", f"- 历史数据截至：{report.as_of or '未知'}", f"- 预测截止：{report.prediction_deadline or '未知'}", ""]
    if not report.evidence_sufficient:
        lines.extend(["## 证据不足", "", report.insufficiency_reason or "现有记忆不足以支持预测。"])
        return "\n".join(lines)
    lines.extend(["## 预测", ""])
    for index, prediction in enumerate(report.predictions, 1):
        lines.extend([f"### {index}. {prediction.statement}", "", f"置信度（模型自评）：{prediction.llm_self_rated_confidence}", "", prediction.reasoning])
        if stream is not None:
            for item in prediction.grounded_on:
                if item in stream.by_id:
                    lines.append(f"- `{item}`：{stream.by_id[item].text}")
        if prediction.simulation_artifacts:
            lines.append(f"- 推演产物：{'、'.join(prediction.simulation_artifacts)}")
        lines.append("")
    if report.divergence_points:
        lines.extend(["## 关键分歧点", "", *(f"- {item}" for item in report.divergence_points), ""])
    if report.caveats:
        lines.extend(["## 限制", "", *(f"- {item}" for item in report.caveats)])
    return "\n".join(lines)
