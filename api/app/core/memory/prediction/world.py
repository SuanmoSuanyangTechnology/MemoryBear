from __future__ import annotations

import asyncio
import math

from .budget import DEFAULT_LIMITS, Limits
from .llm import StructuredLLM
from .schemas import (
    AgencyBatch, AgencyVerdict, AgentCard, AgentCardDraft, EntityRecord, MemoryStatement,
    RelationRecord, UserProfile, WorldCast,
)

_SYSTEM_NAMES = {"assistant", "ai assistant", "chatgpt", "system", "memorybear"}
_AGENCY_SYSTEM = """逐个判断候选实体：actor 是能自主行动或表态的人/组织；environment 是构成约束的机构、场所或资源；concept 是物品或抽象概念。给出与问题的 relevance。泛指且无法指认成员的群体标记 is_vague_collective。"""
_CARD_SYSTEM = """仅依据给出的原始素材，为角色生成用于行为推演的 persona。不得补写素材中没有的身份、目标或关系。source_statement_ids 只能使用输入里出现的 id。"""


def _candidate_material(entity: EntityRecord, relations: list[RelationRecord], statements: list[MemoryStatement], limits: Limits) -> str:
    related = [
        item
        for item in relations
        if item.other_id == entity.id or item.source_id == entity.id
    ][: limits.persona_relations]
    memories = [item for item in statements if entity.id in item.entity_ids][-limits.persona_statements :]
    return (
        f"实体：{entity.name}；类型：{entity.entity_type or '未知'}；描述：{entity.description[:limits.persona_desc_chars]}\n"
        + "\n".join(
            f"关系：{item.source_name} -[{item.predicate}]-> {item.other_name}；证据：{item.evidence}"
            for item in related
        )
        + "\n"
        + "\n".join(f"[{item.id}] {item.text}" for item in memories)
    )


async def cast_world(
    llm: StructuredLLM,
    question: str,
    profile: UserProfile,
    entities: list[EntityRecord],
    relations: list[RelationRecord],
    statements: list[MemoryStatement],
    topical_statement_ids: list[str],
    limits: Limits = DEFAULT_LIMITS,
) -> WorldCast:
    candidates = [
        entity for entity in entities
        if entity.id != profile.entity_id and entity.name.strip().lower() not in _SYSTEM_NAMES
    ]
    verdicts = []
    for start in range(0, len(candidates), limits.agency_batch_size):
        batch = candidates[start : start + limits.agency_batch_size]
        prompt = f"问题：{question}\n\n" + "\n\n".join(_candidate_material(item, relations, statements, limits)[:900] for item in batch)
        try:
            result = await llm.structured(_AGENCY_SYSTEM, prompt, AgencyBatch, temperature=0.1, max_tokens=limits.agency_tokens)
            assert isinstance(result, AgencyBatch)
            verdicts.extend(result.verdicts)
        except Exception:
            continue
    verdict_names = {item.name for item in verdicts}
    actor_types = {"人", "人物", "用户", "person", "people", "organization", "组织"}
    environment_types = {"地点", "场所", "资源", "环境", "上下文", "place", "resource", "environment", "context"}
    topical_entity_ids = {
        entity_id
        for statement in statements
        if statement.id in topical_statement_ids
        for entity_id in statement.entity_ids
    }
    for entity in candidates:
        if entity.name in verdict_names:
            continue
        entity_type = (entity.entity_type or "").strip().lower()
        kind = (
            "actor"
            if entity_type in actor_types
            else "environment" if entity_type in environment_types else "concept"
        )
        is_direct = any(
            {relation.source_id, relation.other_id}
            == {profile.entity_id, entity.id}
            for relation in relations
        )
        verdicts.append(
            AgencyVerdict(
                name=entity.name,
                kind=kind,
                relevance=(
                    "medium"
                    if entity.id in topical_entity_ids or is_direct
                    else "low"
                ),
                reason="结构化分类缺失，按实体类型保守归类",
            )
        )
    relevance_by_name = {item.name: item.relevance for item in verdicts}
    relevant_candidates = [
        item
        for item in candidates
        if item.id in topical_entity_ids
        or relevance_by_name.get(item.name) in {"high", "medium"}
    ]
    relevant_names = {item.name for item in relevant_candidates}
    relevant_verdicts = [item for item in verdicts if item.name in relevant_names]
    environment = [
        item.name
        for item in relevant_verdicts
        if item.kind == "environment"
    ][:8]
    return WorldCast(
        actors=relevant_candidates,
        environment=environment,
        verdicts=relevant_verdicts,
    )


def build_protagonist_card(profile: UserProfile) -> AgentCard:
    timeline = [*profile.events, *profile.anchors][-16:]
    return AgentCard(
        entity_id=profile.entity_id, name=profile.name, role="当事人", goals=profile.goals,
        stance=profile.beliefs_or_stances, behavior_tendency=profile.traits,
        recent_timeline=timeline, source_statement_ids=[], is_protagonist=True,
        agent_kind="actor", activity=1.0, response_delay="immediate",
        influence_weight=1.0, evolution_role="主导演化",
        response_policy="每轮发起行动并汇总全图状态",
        configuration_basis=["End User 主实体", "用户画像的目标、立场与历史事件"],
    )


async def build_cards(llm: StructuredLLM, actors: list[EntityRecord], verdicts: list[AgencyVerdict], statements: list[MemoryStatement], relations: list[RelationRecord], question: str, limits: Limits = DEFAULT_LIMITS) -> list[AgentCard]:
    valid_ids = {item.id for item in statements}
    kind_by_name = {item.name: item.kind for item in verdicts}
    peak_refs = max((item.ref_count for item in actors), default=1)
    relation_degree = {
        actor.id: sum(
            relation.source_id == actor.id or relation.other_id == actor.id
            for relation in relations
        )
        for actor in actors
    }
    peak_degree = max(relation_degree.values(), default=1)
    persona_limit = asyncio.Semaphore(8)

    async def build(actor: EntityRecord) -> AgentCard:
        material = _candidate_material(actor, relations, statements, limits)
        kind = kind_by_name.get(actor.name, "concept")
        if kind == "actor":
            try:
                async with persona_limit:
                    draft = await llm.structured(_CARD_SYSTEM, f"问题：{question}\n{material}", AgentCardDraft, temperature=0.25, max_tokens=limits.card_tokens)
                assert isinstance(draft, AgentCardDraft)
            except Exception:
                draft = AgentCardDraft(role=actor.entity_type or "相关角色")
        else:
            draft = AgentCardDraft(
                role=actor.entity_type or ("环境约束" if kind == "environment" else "状态节点"),
                stance=[actor.description] if actor.description else [],
            )
        ids = [item for item in draft.source_statement_ids if item in valid_ids]
        timeline = [item.text for item in statements if actor.id in item.entity_ids][-limits.timeline_items :]
        activity = math.log1p(max(actor.ref_count, 0)) / math.log1p(max(peak_refs, 1))
        centrality = math.log1p(relation_degree.get(actor.id, 0)) / math.log1p(
            max(peak_degree, 1)
        )
        influence = 0.6 * centrality + 0.4 * activity
        related = [
            item
            for item in relations
            if item.source_id == actor.id or item.other_id == actor.id
        ][: limits.persona_relations]
        relation_effects = [
            (
                f"{item.source_name} -[{item.predicate}]-> {item.other_name}"
                + (f"；图谱证据：{item.evidence}" if item.evidence else "")
            )
            for item in related
            if item.predicate
        ]
        kind_role = {
            "actor": "参与演化",
            "environment": "约束传播",
            "concept": "状态传播",
        }[kind]
        response_policy = {
            "actor": "被主 Agent 指向或进入重点关系时生成回应",
            "environment": "不生成角色对白；状态变化时沿关系边传播约束",
            "concept": "不生成角色对白；作为目标、项目或概念状态被更新",
        }[kind]
        return AgentCard(
            entity_id=actor.id, name=actor.name, entity_type=actor.entity_type,
            role=draft.role, goals=draft.goals, stance=draft.stance,
            attitude_to_user=draft.attitude_to_user, behavior_tendency=draft.behavior_tendency,
            speaking_style=draft.speaking_style, recent_timeline=timeline,
            source_statement_ids=ids,
            perspective_caveat="该角色画像来自当事人的记忆，仅代表当事人视角。",
            agent_kind=kind, activity=round(activity, 3),
            response_delay=(
                "short"
                if kind == "actor" and activity >= 0.6
                else "medium" if kind == "actor" else "immediate"
            ),
            influence_weight=round(influence, 3), evolution_role=kind_role,
            response_policy=response_policy, relation_effects=relation_effects,
            configuration_basis=[
                f"entity_type={actor.entity_type or '未分类'}",
                f"agency_kind={kind}",
                f"statement_refs={actor.ref_count}",
                f"relation_degree={relation_degree.get(actor.id, 0)}",
            ],
        )

    return list(await asyncio.gather(*(build(actor) for actor in actors)))
