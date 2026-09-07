from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, timedelta

from .budget import DEFAULT_LIMITS, Limits
from .llm import StructuredLLM
from .recall import MemoryStream
from .schemas import ActionDraft, AgentCard, SimEvent, StopDraft, TurnRecord, WorldState

TurnSink = Callable[[TurnRecord], Awaitable[None]]

_ACTION_SYSTEM = """你正在进行基于记忆的未来行为推演。先选 response_type，再描述一个具体但克制的行动。只能使用给定记忆与已发生事件，不得补造专有名词、人物或经历。grounded_on 只能填给定的 statement id；无依据就留空。不要重复 recent_timeline 中已完成的事情，也不要复述触发你的上一行动。"""
_STOP_SYSTEM = """判断当前推演是否已形成足以回答问题的行为走向。至少完成三轮后才可停止。若只是重复、仍无关键反馈或没有分歧信息，应继续。"""


def _events_brief(world: WorldState, limit: int) -> str:
    events = world.event_log()[-limit:]
    return "\n".join(f"第{item.turn}轮 {item.actor} [{item.response_type}]：{item.action[:150]}" for item in events) or "（尚无事件）"


def _card_text(card: AgentCard) -> str:
    return (
        f"角色：{card.name}（{card.role}）\n目标：{'；'.join(card.goals)}\n"
        f"立场：{'；'.join(card.stance)}\n行为倾向：{'；'.join(card.behavior_tendency)}\n"
        f"已发生经历：{'；'.join(card.recent_timeline)}"
    )


def _time_window(turn: int, max_turns: int, deadline: str) -> str:
    start = date.today()
    end = date.fromisoformat(deadline)
    total_days = max((end - start).days, max_turns)
    left = start + timedelta(days=total_days * (turn - 1) // max_turns)
    right = start + timedelta(days=total_days * turn // max_turns)
    return f"{left.isoformat()} 至 {right.isoformat()}"


async def _act(llm: StructuredLLM, card: AgentCard, world: WorldState, stream: MemoryStream, turn: int, query_terms: list[str], trigger: str, limits: Limits) -> tuple[SimEvent, list[str]]:
    focus = [item.entity_id for item in world.all_agents()]
    memories = stream.recall(focus, [*query_terms, card.name, trigger, _events_brief(world, limits.events_brief)], limits.recall_top_k)
    recalled = [item.statement.id for item in memories]
    prompt = (
        f"问题：{world.question}\n预测截止时间：{world.prediction_deadline}\n当前轮：{turn}\n{_card_text(card)}\n"
        f"触发：{trigger or '请主动推进问题'}\n最近事件：\n{_events_brief(world, limits.events_brief)}\n"
        f"相关记忆：\n{stream.render(memories, limits.recall_render_chars)}"
    )
    try:
        draft = await llm.structured(_ACTION_SYSTEM, prompt, ActionDraft, temperature=0.35, max_tokens=limits.action_tokens)
        assert isinstance(draft, ActionDraft)
    except Exception as exc:
        draft = ActionDraft(response_type="delay", action="当前信息不足，暂不推进。", rationale=str(exc))
    grounded = [item for item in draft.grounded_on if item in recalled]
    event = SimEvent(
        turn=turn, actor=card.name, response_type=draft.response_type, action=draft.action,
        rationale=draft.rationale, grounded_on=grounded, low_grounding=not grounded,
        targets=[target for target in draft.targets if target != card.name],
    )
    return event, recalled


async def simulate(llm: StructuredLLM, world: WorldState, stream: MemoryStream, *, max_turns: int, query_terms: list[str], limits: Limits = DEFAULT_LIMITS, on_turn: TurnSink | None = None) -> None:
    others_by_name = {item.name: item for item in world.others if item.agent_kind == "actor"}
    for turn in range(1, max_turns + 1):
        protagonist_event, protagonist_recalled = await _act(
            llm, world.protagonist, world, stream, turn, query_terms,
            "外部约束：" + "；".join(world.environment[:4]), limits,
        )
        selected = [others_by_name[name] for name in protagonist_event.targets if name in others_by_name]
        if not selected:
            selected = [item for item in world.others if item.agent_kind == "actor"][: limits.reactors_per_turn]
        selected = selected[: limits.reactors_per_turn]
        trigger = f"{world.protagonist.name}：{protagonist_event.action}"
        reactions = await asyncio.gather(
            *(_act(llm, card, world, stream, turn, query_terms, trigger, limits) for card in selected)
        )
        events = [protagonist_event, *(item[0] for item in reactions)]
        recalled = {world.protagonist.name: protagonist_recalled, **{card.name: result[1] for card, result in zip(selected, reactions)}}
        grounded_ids = list(dict.fromkeys(item for event in events for item in event.grounded_on))
        state_agents = sorted(
            (item for item in world.others if item.agent_kind != "actor"),
            key=lambda item: item.influence_weight,
            reverse=True,
        )
        propagation_agents = [
            item for item in state_agents if item.relation_effects
        ][: limits.reactors_per_turn]
        state_propagations = [
            f"{item.name}（{item.agent_kind}）：{item.evolution_role}；"
            f"依据关系 {item.relation_effects[0]}"
            for item in propagation_agents
        ]
        graph_delta = [
            f"{event.actor} 的行动状态更新为：{event.action}" for event in events
        ]
        graph_delta.extend(state_propagations)
        focus_names = list(
            dict.fromkeys(
                [
                    *(item.name for item in selected),
                    *(item.name for item in propagation_agents),
                ]
            )
        )
        record = TurnRecord(
            turn=turn,
            title=f"第 {turn} 轮：进入受约束反馈循环",
            time_window=_time_window(turn, max_turns, world.prediction_deadline),
            events=events,
            recalled=recalled,
            environment_event=("外部约束持续生效：" + "；".join(world.environment[:4])) if world.environment else "未引入未经记忆支持的外部事实",
            evidence_ids=grounded_ids,
            main_agent_action=protagonist_event.action,
            state_changes=[f"{event.actor}：{event.action}" for event in events],
            graph_state_delta=graph_delta,
            state_propagations=state_propagations,
            propagation_summary=(
                f"调度全部 {len(world.all_agents())} 个实体状态；"
                f"本轮重点传播节点：{'、'.join(focus_names) or '主 Agent'}"
            ),
            focus_agent_names=focus_names,
            temporary_memory=f"主 Agent 在第 {turn} 轮执行：{protagonist_event.action}",
            entity_state_updates=len(world.all_agents()),
        )
        world.turns.append(record)
        if turn >= 3:
            prompt = f"问题：{world.question}\n截止时间：{world.prediction_deadline}\n推演事件：\n{_events_brief(world, limits.events_brief)}\n本轮依据：{grounded_ids}"
            try:
                stop = await llm.structured(_STOP_SYSTEM, prompt, StopDraft, temperature=0.1, max_tokens=limits.stop_tokens)
                assert isinstance(stop, StopDraft)
                record.should_stop = stop.should_stop
                record.stop_reason = stop.reason
            except Exception:
                pass
        if on_turn is not None:
            await on_turn(record)
        if record.should_stop:
            break
