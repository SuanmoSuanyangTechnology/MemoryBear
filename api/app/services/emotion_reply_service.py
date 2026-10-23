"""单轮情绪识别 → 默认策略 → 提示词片段，不访问对话记忆。"""

import logging
from typing import cast

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.memory.emotion import emotion_resolver
from app.core.memory.emotion.policy_contracts import (
    DEFAULT_EMOTION_POLICY_PLUGIN,
    DefaultEmotionPolicyProvider,
    DefaultPolicyLoadError,
)
from app.core.trace import get_trace_id
from app.plugins import get_plugin
from app.schemas.emotion_reply_schema import EmotionReplyData

logger = logging.getLogger(__name__)


def _log_result(data: EmotionReplyData, fault_type: str | None = None) -> EmotionReplyData:
    log = logger.warning if fault_type else logger.info
    log(
        "[EmotionReplyAPI] request_id=%s emotion_code=%s injected=%s fault_type=%s",
        get_trace_id(), data.emotion_code, data.prompt_injection is not None, fault_type,
    )
    return data


async def resolve_emotion_reply(message: str) -> EmotionReplyData:
    provider = cast(DefaultEmotionPolicyProvider | None, get_plugin(DEFAULT_EMOTION_POLICY_PLUGIN))
    if provider is None:
        _log_result(EmotionReplyData(), "provider_unavailable")
        raise BusinessException("情绪回应策略能力不可用", BizCode.SERVICE_UNAVAILABLE)

    result = await emotion_resolver.detect_emotion(message)
    if result is None:
        return _log_result(EmotionReplyData(), "emotion_unavailable")

    data = EmotionReplyData(emotion_code=result.emotion, score=result.emotion_score)
    try:
        snapshot = await provider.get_default_policy_snapshot()
    except DefaultPolicyLoadError as exc:
        return _log_result(data, exc.fault_type)

    policy = snapshot.policies.get(result.emotion)
    if policy is None:
        return _log_result(data)
    fragment = emotion_resolver.build_emotion_prompt_fragment(
        policy.tone, policy.do_list, policy.avoid_list,
    )
    if fragment is not None:
        data.tone = policy.tone
        data.do = list(policy.do_list)
        data.avoid = list(policy.avoid_list)
        data.prompt_injection = fragment
    return _log_result(data)
