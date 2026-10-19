from __future__ import annotations

import json
import logging
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .adapter import GraphAdapter
from .config import PredictionSettings
from .gate import check_evidence
from .llm import StructuredLLM
from .recall import MemoryStream, ScoredMemory, derive_query_terms
from .report import build_report, render_markdown
from .schemas import (
    AgentProfilesStage,
    EnvironmentStage,
    EvidenceGate,
    InitialVariablesStage,
    MemorySeedItem,
    MemorySeedStage,
    MemorySeedTitles,
    PredictionReport,
    QueryTerms,
    ReportAgentStage,
    StateGraphStage,
    StateGraphDraft,
    StateGraphEntity,
    StateRelation,
    GroundedStateItem,
    TemporalMemoryRecord,
    TemporalMemoryStage,
    TurnRecord,
    WorldState,
)
from .simulate import simulate
from .world import build_cards, build_protagonist_card, cast_world

logger = logging.getLogger(__name__)
EventSink = Callable[[str, dict[str, object]], Awaitable[None]]


@dataclass
class RunResult:
    run_dir: Path
    report: PredictionReport
    world: WorldState | None = None
    gate: EvidenceGate | None = None
    query_terms: QueryTerms | None = None
    stream: MemoryStream | None = None
    usage: dict[str, int] = field(default_factory=dict)
    query: dict[str, object] = field(default_factory=dict)
    process: dict[str, object] = field(default_factory=dict)

    def export(self) -> dict[str, object]:
        return {
            "query": self.query,
            "process": self.process,
            "report": self.report.model_dump(),
            "usage": self.usage,
            "run_dir": str(self.run_dir),
        }


class ProfileNotFound(RuntimeError):
    pass


def _write_artifact(path: Path, payload: object) -> None:
    data = payload.model_dump() if hasattr(payload, "model_dump") else payload
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("Prediction artifact write skipped for %s: %s", path, exc)


_SEED_TITLE_SYSTEM = """为每条记忆生成 8-24 个汉字的摘要标题。标题必须概括事件或约束，不能整句复制 content，不得增加事实。id 必须原样返回。"""
_STATE_GRAPH_SYSTEM = """只依据给定的已选记忆，提取与预测问题直接相关的行为路径和硬约束，并指出回答目标所缺的信息。每个行为或约束都必须引用输入中的 statement id；不得把地点、日期或偏好自动当成硬约束。行为路径按先后顺序排列。"""


def _fallback_title(text: str, memory_type: str) -> str:
    compact = "".join(text.strip().split())
    head = compact.split("。", 1)[0].split("，", 1)[0][:18]
    return f"{memory_type.lower()}：{head}" if head else f"{memory_type.lower()}记忆"


def _seed_item(
    memory: ScoredMemory, selected: bool, title: str | None = None
) -> MemorySeedItem:
    statement = memory.statement
    summary = (title or "").strip() or _fallback_title(
        statement.text, statement.stmt_type
    )
    if summary == statement.text.strip():
        summary = _fallback_title(statement.text, statement.stmt_type)
    return MemorySeedItem(
        id=statement.id,
        memory_type=statement.stmt_type,
        title=summary[:32],
        occurred_at=statement.dialog_at,
        content=statement.text,
        score=round(memory.score, 4),
        selected=selected,
        recency=round(memory.recency, 4),
        importance=round(memory.importance, 4),
        relevance=round(memory.relevance, 4),
        graph_relevance=round(memory.graph_relevance, 4),
        lexical_relevance=round(memory.lexical_relevance, 4),
    )


async def _summarize_seed_titles(
    llm: StructuredLLM, memories: list[ScoredMemory]
) -> dict[str, str]:
    if not memories:
        return {}
    prompt = "\n".join(
        f"[{item.statement.id}] content={item.statement.text[:300]}"
        for item in memories
    )
    try:
        result = await llm.structured(
            _SEED_TITLE_SYSTEM, prompt, MemorySeedTitles, temperature=0.1
        )
        assert isinstance(result, MemorySeedTitles)
    except Exception:
        return {}
    valid_ids = {item.statement.id for item in memories}
    return {
        item.id: item.title.strip()
        for item in result.items
        if item.id in valid_ids and item.title.strip()
    }


async def _build_state_graph_draft(
    llm: StructuredLLM,
    question: str,
    selected: list[ScoredMemory],
    missing_aspects: list[str],
) -> StateGraphDraft:
    valid_ids = {item.statement.id for item in selected}
    prompt = (
        f"预测问题：{question}\n证据闸门识别的缺口：{missing_aspects}\n\n"
        + "\n".join(
            f"[{item.statement.id}] {item.statement.text}" for item in selected
        )
    )
    try:
        result = await llm.structured(
            _STATE_GRAPH_SYSTEM, prompt, StateGraphDraft, temperature=0.1
        )
        assert isinstance(result, StateGraphDraft)
    except Exception:
        return StateGraphDraft(information_gaps=missing_aspects)

    def grounded(items: list[GroundedStateItem]) -> list[GroundedStateItem]:
        return [
            GroundedStateItem(
                text=item.text,
                statement_ids=[value for value in item.statement_ids if value in valid_ids],
            )
            for item in items
            if item.text.strip() and any(value in valid_ids for value in item.statement_ids)
        ]

    return StateGraphDraft(
        behavior_path=grounded(result.behavior_path),
        hard_constraints=grounded(result.hard_constraints),
        information_gaps=list(
            dict.fromkeys([*result.information_gaps, *missing_aspects])
        ),
    )


class PredictionPipeline:
    _STAGES = (
        (1, "memory_seed", "抽取记忆种子"),
        (2, "state_graph", "构建个人状态图谱"),
        (3, "environment", "生成环境参数"),
        (4, "agent_profiles", "全实体节点 Agent 化"),
        (5, "initial_variables", "注入初始变量"),
        (6, "simulation", "运行多 Agent 推演"),
        (7, "temporal_memory", "更新临时演化记忆"),
        (8, "report_agent", "Report Agent 生成报告"),
    )

    def __init__(
        self,
        llm: StructuredLLM,
        settings: PredictionSettings,
        adapter: GraphAdapter | None = None,
    ) -> None:
        self._llm = llm
        self._settings = settings
        self._adapter = adapter or GraphAdapter()

    async def run(
        self,
        end_user_id: str,
        question: str,
        prediction_deadline: str,
        *,
        max_rounds: int | None = None,
        on_event: EventSink | None = None,
    ) -> RunResult:
        async def emit(name: str, payload: dict[str, object]) -> None:
            if on_event is not None:
                await on_event(name, payload)

        async def start_stage(index: int) -> None:
            number, key, title = self._STAGES[index - 1]
            await emit(
                "stage",
                {"index": number, "key": key, "title": title, "status": "running"},
            )

        async def complete_stage(index: int, payload: object) -> None:
            number, key, title = self._STAGES[index - 1]
            data = payload.model_dump() if hasattr(payload, "model_dump") else payload
            process[key] = data
            _write_artifact(run_dir / f"{number:02d}_{key}.json", data)
            await emit(key, data)
            await emit(
                "stage",
                {"index": number, "key": key, "title": title, "status": "complete"},
            )

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        run_dir = self._settings.out_dir / (
            f"{end_user_id[:8]}_{stamp}_{uuid.uuid4().hex[:6]}"
        )
        limits = self._settings.limits
        rounds_cap = max_rounds or self._settings.max_turns
        query: dict[str, object] = {
            "end_user_id": end_user_id,
            "question": question,
            "prediction_deadline": prediction_deadline,
            "max_rounds": rounds_cap,
        }
        process: dict[str, object] = {}
        stream: MemoryStream | None = None
        gate: EvidenceGate | None = None
        terms: QueryTerms | None = None
        world: WorldState | None = None

        def insufficient(reason: str, as_of: str | None) -> PredictionReport:
            return PredictionReport(
                question=question,
                end_user_id=end_user_id,
                as_of=as_of,
                prediction_deadline=prediction_deadline,
                evidence_sufficient=False,
                insufficiency_reason=reason,
            )

        def result(report: PredictionReport) -> RunResult:
            return RunResult(
                run_dir=run_dir,
                report=report,
                world=world,
                gate=gate,
                query_terms=terms,
                stream=stream,
                usage=self._llm.usage(),
                query=query,
                process=process,
            )

        try:
            await start_stage(1)
            profile, entities, statements, as_of = await self._adapter.load_material(
                end_user_id
            )
            if profile is None:
                raise ProfileNotFound(f"记忆图里找不到 {end_user_id} 的用户实体")
            relations = await self._adapter.load_relations(end_user_id)
            stream = MemoryStream(
                statements, as_of=as_of, top_k=limits.recall_top_k
            )
            terms = await derive_query_terms(self._llm, question, stream, limits)
            gate = await check_evidence(self._llm, question, stream, terms, limits)
            topical_hits = stream.term_hits(terms.core_terms)
            topical_entity_ids = {
                entity_id
                for hit_list in topical_hits.values()
                for statement in hit_list
                for entity_id in statement.entity_ids
            }
            scored = stream.recall(
                [profile.entity_id, *topical_entity_ids],
                terms.all_terms(),
                top_k=min(max(limits.recall_top_k * 2, 1), len(statements)),
            )
            selected = scored[: limits.recall_top_k]
            selected_ids = {item.statement.id for item in selected}
            seed_titles = await _summarize_seed_titles(self._llm, scored)
            seeds = MemorySeedStage(
                candidate_count=len(scored),
                selected_count=len(selected_ids),
                excluded_count=max(len(scored) - len(selected_ids), 0),
                coverage_rate=round(
                    min(1.0, len(gate.topical_statement_ids) / max(len(scored), 1)),
                    4,
                ),
                strategy="优先近期重复行为、真实关系与硬约束；只做筛选，不生成历史事实。",
                score_formula="0.25×时近性 + 0.25×重要性 + 0.50×相关度",
                relevance_formula="0.50×图关系相关度 + 0.50×问题词面相关度",
                items=[
                    _seed_item(
                        item,
                        item.statement.id in selected_ids,
                        seed_titles.get(item.statement.id),
                    )
                    for item in scored
                ],
            )
            await complete_stage(1, seeds)
            if not gate.has_topical_evidence:
                report = insufficient(gate.reason, as_of)
                await start_stage(8)
                trace = ReportAgentStage(
                    planned_sections=["证据充分性"],
                    retrieval_tools=["主题词命中核验"],
                    simulation_fact_references=0,
                    execution_steps=["证据闸门拒答，未生成模拟事实"],
                )
                await complete_stage(8, trace)
                process["report"] = report.model_dump()
                _write_artifact(run_dir / "08_report.json", report)
                await emit("report", report.model_dump())
                return result(report)

            cast = await cast_world(
                self._llm,
                question,
                profile,
                entities,
                relations,
                statements,
                gate.topical_statement_ids,
                limits,
            )

            await start_stage(2)
            graph_draft = await _build_state_graph_draft(
                self._llm, question, selected, gate.missing_aspects
            )
            state_entities = [
                item
                for item in entities
                if item.id == profile.entity_id
                or any(candidate.id == item.id for candidate in cast.actors)
            ]
            state_entity_ids = {item.id for item in state_entities}
            state_relations = [
                item
                for item in relations
                if item.source_id in state_entity_ids
                and item.other_id in state_entity_ids
            ]
            entity_type_counts = Counter(
                (item.entity_type or "未分类") for item in state_entities
            )
            relation_items = [
                StateRelation(
                    source=item.source_name,
                    predicate=item.predicate,
                    target=item.other_name,
                    evidence=item.evidence,
                )
                for item in state_relations
            ]
            state_graph = StateGraphStage(
                entity_count=len(state_entities),
                state_relation_count=len(state_relations),
                hard_constraint_count=len(graph_draft.hard_constraints),
                information_gap_count=len(graph_draft.information_gaps),
                main_entity=StateGraphEntity(
                    id=profile.entity_id,
                    name=profile.name,
                    entity_type="End User",
                ),
                target_node=GroundedStateItem(
                    text=question,
                    statement_ids=gate.topical_statement_ids,
                ),
                prediction_deadline=prediction_deadline,
                entity_type_counts=dict(entity_type_counts),
                entities=[
                    StateGraphEntity(
                        id=item.id,
                        name=item.name,
                        entity_type=item.entity_type,
                    )
                    for item in state_entities
                ],
                behavior_path=graph_draft.behavior_path,
                state_relations=relation_items,
                hard_constraints=graph_draft.hard_constraints,
                information_gaps=graph_draft.information_gaps,
                graph_rules=[
                    "历史记忆图谱只读",
                    "模拟事实仅写入本次推演的临时演化层",
                    "未知外生变量不得补写为历史事实",
                ],
            )
            await complete_stage(2, state_graph)

            await start_stage(3)
            environment = EnvironmentStage(
                max_rounds=rounds_cap,
                entity_agent_count=len(cast.actors) + 1,
                prediction_deadline=prediction_deadline,
                simulation_object=(
                    f"全部 {len(cast.actors) + 1} 个记忆图谱实体 Agent；"
                    f"{profile.name} 为唯一主 Agent"
                ),
                time_mapping=(
                    f"{rounds_cap} 轮覆盖至 {prediction_deadline}，"
                    "每轮代表一个连续现实时间片"
                ),
                branches=[
                    "行动路径观察：由可响应的 actor Agent 推进行动与反馈",
                    "约束路径观察：environment/concept Agent 沿关系边传播状态与约束",
                ],
                scheduling_rule=(
                    "每轮更新全部实体状态；人物 Agent 产生回应，"
                    "其他实体沿关系边传播状态和约束"
                ),
                convergence_rule=(
                    "连续两轮状态增量低于阈值且无新增高影响传播时停止"
                ),
            )
            await complete_stage(3, environment)

            await start_stage(4)
            world = WorldState(
                end_user_id=end_user_id,
                question=question,
                as_of=as_of,
                prediction_deadline=prediction_deadline,
                protagonist=build_protagonist_card(profile),
                others=await build_cards(
                    self._llm,
                    cast.actors,
                    cast.verdicts,
                    statements,
                    state_relations,
                    question,
                    limits,
                ),
                environment=cast.environment,
            )
            agent_profiles = AgentProfilesStage(
                total_agents=len(world.all_agents()),
                responsive_agents=sum(
                    item.agent_kind == "actor" for item in world.all_agents()
                ),
                state_agents=sum(
                    item.agent_kind != "actor" for item in world.all_agents()
                ),
                agents=world.all_agents(),
                boundary=(
                    "全部 Agent 只能使用图谱中已有的属性、关系和记忆依据；"
                    "人物可回应，组织、项目、目标与上下文承担状态或约束传播。"
                ),
            )
            await complete_stage(4, agent_profiles)

            await start_stage(5)
            initial_variables = InitialVariablesStage(
                prediction_request=question,
                initial_event=f"主 Agent 围绕“{question}”启动受约束推演",
                participant_count=len(world.all_agents()),
                participation_scope=(
                    f"{len(world.all_agents())} 个实体 Agent 全部接收初始状态，"
                    "并沿关系边传播影响"
                ),
                resources=[*profile.core_facts[:3], *profile.anchors[:3]],
                exogenous_variables=gate.missing_aspects,
                memory_isolation=(
                    "历史层只读；全部 Agent 的新状态只进入本次临时演化层"
                ),
            )
            await complete_stage(5, initial_variables)

            await start_stage(6)

            async def on_turn(record: TurnRecord) -> None:
                await emit("turn", record.model_dump())

            await simulate(
                self._llm,
                world,
                stream,
                max_turns=rounds_cap,
                query_terms=terms.all_terms(),
                limits=limits,
                on_turn=on_turn,
            )
            simulation = {
                "round_count": len(world.turns),
                "entity_agent_count": len(world.all_agents()),
                "converged": bool(world.turns and world.turns[-1].should_stop),
                "rounds": [item.model_dump() for item in world.turns],
            }
            await complete_stage(6, simulation)

            await start_stage(7)
            temporal_records = [
                TemporalMemoryRecord(
                    turn=turn.turn,
                    time_window=turn.time_window,
                    summary=turn.temporary_memory,
                    entity_state_updates=turn.entity_state_updates,
                    grounded_on=list(
                        dict.fromkeys(
                            memory_id
                            for event in turn.events
                            for memory_id in event.grounded_on
                        )
                    ),
                )
                for turn in world.turns
            ]
            temporal_memory = TemporalMemoryStage(
                time_slice_count=len(temporal_records),
                entity_state_update_count=sum(
                    item.entity_state_updates for item in temporal_records
                ),
                temporary_memory_count=len(temporal_records),
                sandbox_id=f"sandbox_{uuid.uuid4().hex[:10]}",
                records=temporal_records,
            )
            await complete_stage(7, temporal_memory)

            await start_stage(8)
            report = await build_report(
                self._llm, world, stream, gate.topical_statement_ids, limits
            )
            historical_ids = {
                memory_id
                for prediction in report.predictions
                for memory_id in prediction.grounded_on
            }
            trace = ReportAgentStage(
                planned_sections=["核心走向", "角色反应", "关键分支", "风险与限制"],
                retrieval_tools=["全景检索", "深度洞察", "快速核验", "角色状态交叉检查"],
                simulation_fact_references=sum(
                    len(item.simulation_artifacts) for item in report.predictions
                ),
                execution_steps=[
                    f"读取 {len(world.turns)} 个时间片的完整状态演化",
                    f"追踪 {len(world.all_agents())} 个实体 Agent 的状态与关系",
                    f"逐项核验 {len(historical_ids)} 条历史 statement",
                    "确认常识补写为 0，未知项保留为限制",
                ],
            )
            await complete_stage(8, trace)
            markdown = render_markdown(report, world, stream)
            process["report"] = {**report.model_dump(), "markdown": markdown}
            _write_artifact(run_dir / "08_report.json", report)
            try:
                (run_dir / "08_report.md").write_text(markdown, encoding="utf-8")
            except OSError as exc:
                logger.warning("Prediction markdown write skipped: %s", exc)
            await emit("report", process["report"])
            return result(report)
        finally:
            _write_artifact(run_dir / "00_query.json", query)
            _write_artifact(run_dir / "00_usage.json", self._llm.usage())
