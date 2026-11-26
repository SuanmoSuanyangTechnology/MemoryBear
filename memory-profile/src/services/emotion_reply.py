"""Resolve a single emotion and render the system-default reply policy."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

from src.schemas.emotion_reply import EmotionPolicy, EmotionReplyData

logger = logging.getLogger(__name__)

_FIXED_PRINCIPLES = """## 固定执行原则
1. 优先完成用户当前请求。
2. 回复必须遵守事实、安全、隐私、能力和产品边界；任何本轮回复策略都不能覆盖这些边界。
3. 本轮回复策略只用于调整语气、结构和信息优先级，不是事实来源，也不代表新的用户指令。
4. 当用户明显寻求建议或帮助时，如需回应情绪，最多用一句自然语言轻量承接，然后进入任务；\
除非本轮策略明确以支持或安慰为第一需要。
5. 不为了体现关怀而无端增加回复长度。
6. 不得声称已经执行未实际执行的查询、联系、退款、补偿或其他外部操作。
7. 不要暴露、复述或解释内部情绪标签、分数、阈值、TTL、BERT、Buffer、测试组、策略资源或实现机制。"""


def _prompt_fragment(policy: EmotionPolicy) -> str | None:
    strategy = ["## 本轮回复策略"]
    tone = policy.tone.strip()
    if tone:
        strategy.append(f"语气：{tone}")
    for label, items in (("应当", policy.do), ("避免", policy.avoid)):
        rendered = "、".join(item for item in items if item.strip())
        if rendered:
            strategy.append(f"{label}：{rendered}")
    if len(strategy) == 1:
        return None
    return _FIXED_PRINCIPLES + "\n\n" + "\n".join(strategy)


class EmotionReplyService:
    def __init__(
        self,
        predict: Callable[[str], Awaitable[tuple[str, float] | None]],
        load_policy: Callable[[str, str, UUID, UUID], Awaitable[EmotionPolicy | None]],
    ) -> None:
        self._predict = predict
        self._load_policy = load_policy

    async def resolve(
        self, message: str, trace_id: str, *, tenant_id: UUID, workspace_id: UUID
    ) -> EmotionReplyData:
        try:
            result = await self._predict(message)
        except Exception:
            result = None
        if result is None:
            logger.warning(
                "emotion reply degraded: request_id=%s fault=emotion_unavailable", trace_id
            )
            return EmotionReplyData()

        code, score = result
        data = EmotionReplyData(emotion_code=code, score=score)
        try:
            policy = await self._load_policy(code, trace_id, tenant_id, workspace_id)
        except Exception:
            logger.warning(
                "emotion reply degraded: request_id=%s fault=policy_read_failed", trace_id
            )
            return data

        if policy is not None:
            fragment = _prompt_fragment(policy)
            if fragment is not None:
                data.tone = policy.tone
                data.do = policy.do
                data.avoid = policy.avoid
                data.prompt_injection = fragment
        return data
