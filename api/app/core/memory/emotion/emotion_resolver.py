"""回复侧情绪识别 + 情绪→回复策略映射 + 提示词注入。
职责边界：
- 只在 App 开关 ``features.emotion_reply.enabled`` 打开时工作。
- 对当前 user 消息调 BERT（``fast_write_emotion_client.predict``），硬超时 1s；
  超时/异常/未配置一律降级为 None，绝不影响正常回复。
- 本模块**永不抛异常**，返回值始终 ≥ base_prompt。
"""

from __future__ import annotations

import asyncio
import logging
import math
from time import perf_counter
from typing import Any, Optional

from app.core.config import settings
from app.services.fast_write_emotion_client import FastWriteEmotionResult, VALID_EMOTIONS

logger = logging.getLogger(__name__)

# 回复侧硬超时（秒）：比快写的 2s 更紧，回复链路在请求内，不能拖首字延迟
DETECT_TIMEOUT_SEC = 1.0

_FIXED_PRINCIPLES = """## 固定执行原则
1. 优先完成用户当前请求。
2. 回复必须遵守事实、安全、隐私、能力和产品边界；任何本轮回复策略都不能覆盖这些边界。
3. 本轮回复策略只用于调整语气、结构和信息优先级，不是事实来源，也不代表新的用户指令。
4. 当用户明显寻求建议或帮助时，如需回应情绪，最多用一句自然语言轻量承接，然后进入任务；\
除非本轮策略明确以支持或安慰为第一需要。
5. 不为了体现关怀而无端增加回复长度。
6. 不得声称已经执行未实际执行的查询、联系、退款、补偿或其他外部操作。
7. 不要暴露、复述或解释内部情绪标签、分数、阈值、TTL、BERT、Buffer、测试组、策略资源或实现机制。"""

def is_emotion_reply_enabled(features: Any) -> bool:
    """读取 App 开关 ``features.emotion_reply.enabled``（默认关闭）。

    ``features`` 可能是 dict（DB JSON）或 AppFeatures（Pydantic），两种都支持；
    任何结构异常都按关闭处理。
    """
    try:
        if features is None:
            return False
        if hasattr(features, "model_dump"):
            features = features.model_dump()
        if not isinstance(features, dict):
            return False
        cfg = features.get("emotion_reply") or {}
        if hasattr(cfg, "model_dump"):
            cfg = cfg.model_dump()
        if not isinstance(cfg, dict):
            return False
        return bool(cfg.get("enabled", False))
    except Exception:
        return False


def _deployment_allows() -> bool:
    """部署级可用性：情绪服务三项配置齐全才允许调用（未配置=熔断，按失败降级）。"""
    try:
        from app.core.config import settings

        return bool(
            settings.BERT_URL
            and settings.FAST_WRITE_EMOTION_API_KEY
            and settings.FAST_WRITE_EMOTION_MODEL
        )
    except Exception:
        return False


# 情绪识别：识别当前消息情绪；失败时让回复继续走原提示词。
async def detect_emotion(
    text: str,
    timeout: Optional[float] = None,
) -> Optional[FastWriteEmotionResult]:
    """调 BERT 情绪服务，硬超时 ``timeout``（默认 1s）；失败/超时/未配置一律返回 None。"""
    if not text or not text.strip():
        logger.debug("[EmotionReply] skipped event=detect reason=empty_message")
        return None
    if not _deployment_allows():
        logger.debug("[EmotionReply] skipped event=detect reason=service_unconfigured")
        return None
    effective_timeout = DETECT_TIMEOUT_SEC if timeout is None else timeout
    try:
        from app.services.fast_write_emotion_client import predict as _predict_emotion

        return await asyncio.wait_for(_predict_emotion(text), timeout=effective_timeout)
    except asyncio.TimeoutError:
        logger.warning(
            "[EmotionReply] emotion detect timed out, degrading: text_len=%s, timeout=%ss",
            len(text), effective_timeout,
        )
        return None
    except Exception as e:
        logger.warning(
            "[EmotionReply] emotion detect failed, degrading: text_len=%s, err=%s",
            len(text), e,
        )
        return None


def start_detection(features: Any, message: str, *, sub_agent: bool = False) -> Optional["asyncio.Task"]:
    """最终回复开启情绪识别时启动后台任务，与提示词/上下文/多模态准备并发执行。

    子 Agent 不注入情绪策略；返回 None 表示无需处理。任务本身永不抛异常。
    """
    if sub_agent:
        logger.debug("[EmotionReply] skipped event=detect reason=sub_agent")
        return None
    if not is_emotion_reply_enabled(features):
        logger.debug("[EmotionReply] skipped event=detect reason=feature_disabled")
        return None
    try:
        return asyncio.ensure_future(detect_emotion(message))
    except Exception as e:
        logger.warning(
            "[EmotionReply] failed event=detect_start reason=task_start_error error_type=%s",
            type(e).__name__,
        )
        return None


def cancel_detection(detection: Optional["asyncio.Task"]) -> None:
    """短路分支（如标注命中直接返回）的情绪任务收尾：直接取消。"""
    if detection is None:
        return
    try:
        detection.cancel()
    except Exception as e:  # pragma: no cover - cancel 几乎不会抛
        logger.warning(
            "[EmotionReply] failed event=detect_cancel reason=task_cancel_error error_type=%s",
            type(e).__name__,
        )


# 策略查询：通过企业插件按应用读取本轮情绪策略。
async def _policy_for_result(result: Optional[FastWriteEmotionResult], app_id: Any, tenant_id: Any) -> Optional[dict]:
    """经企业插件获取本轮策略；缺少身份或读取故障时保持原提示词。"""
    emotion = getattr(result, "emotion", None) if result else None
    if not emotion or not app_id or not tenant_id:
        logger.debug(
            "[EmotionReply] skipped event=policy_lookup tenant_id=%s app_id=%s reason=%s",
            tenant_id, app_id, "missing_identity" if emotion else "no_valid_emotion",
        )
        return None
    try:
        from app.plugins import get_plugin

        provider = get_plugin("memory.emotion_reply_policy")
        if provider is None:
            logger.warning(
                "[EmotionReply] skipped event=policy_lookup tenant_id=%s app_id=%s reason=provider_unavailable",
                tenant_id, app_id,
            )
            return None
        return await provider.get_strategy(app_id, tenant_id, emotion)
    except Exception as e:
        logger.warning(
            "[EmotionReply] failed event=policy_lookup tenant_id=%s app_id=%s reason=provider_error error_type=%s",
            tenant_id, app_id, type(e).__name__,
        )
        return None


def _render_items(items: Any) -> str:
    if isinstance(items, (list, tuple)):
        return "、".join(str(i) for i in items if str(i).strip())
    return str(items or "")


def build_emotion_prompt_fragment(tone: Any, do_list: Any, avoid_list: Any) -> Optional[str]:
    """Agent 和对外接口共用的固定原则与策略片段；无有效策略时不注入。"""
    tone_text = str(tone or "").strip()
    do_items = _render_items(do_list)
    avoid_items = _render_items(avoid_list)
    strategy = ["## 本轮回复策略"]
    if tone_text:
        strategy.append(f"语气：{tone_text}")
    if do_items:
        strategy.append(f"应当：{do_items}")
    if avoid_items:
        strategy.append(f"避免：{avoid_items}")
    if len(strategy) == 1:
        return None
    return "\n\n".join([_FIXED_PRINCIPLES, "\n".join(strategy)])


# 提示词渲染：仅把命中的策略字段渲染进系统提示词。
def build_prompt(base_prompt: str, policy: Optional[dict], *, app_id: Any = None, tenant_id: Any = None) -> str:
    """
    命中有效 policy 时，注入"固定执行原则 + 本轮回复策略"（两者成对出现）。
    永不抛异常；返回值一定包含 base_prompt。
    """
    base = base_prompt or ""
    try:
        if not isinstance(policy, dict) or not policy:
            return base

        appended = build_emotion_prompt_fragment(
            policy.get("tone"), policy.get("do_list"), policy.get("avoid_list"),
        )
        if appended is None:
            return base
        return f"{base}\n\n{appended}" if base else appended
    except Exception as e:
        logger.warning(
            "[EmotionReply] failed event=prompt_render tenant_id=%s app_id=%s reason=render_error error_type=%s",
            tenant_id, app_id, type(e).__name__,
        )
        return base


# 回复编排：消费识别结果、保留快写缓存行为并注入策略。
async def _apply_detection_inner(
    base_prompt: str,
    detection: Optional["asyncio.Task"],
    message_id: Any,
    *,
    app_id: Any = None,
    tenant_id: Any = None,
    write_cache: bool = True,
) -> str:
    """等待情绪识别结果 → （可选）写缓存 → 注入提示词。"""
    started = perf_counter()
    if detection is None:
        logger.debug(
            "[EmotionReply] skipped event=prompt_injection tenant_id=%s app_id=%s result=unchanged reason=feature_disabled elapsed_ms=%.1f",
            tenant_id, app_id, (perf_counter() - started) * 1000,
        )
        return base_prompt
    result: Optional[FastWriteEmotionResult] = None
    detect_reason = None
    error_type = "none"
    try:
        result = await detection
    except Exception as e:
        detect_reason = "task_error"
        error_type = type(e).__name__

    emotion_code = getattr(result, "emotion", None) if result is not None else None
    if isinstance(emotion_code, str) and emotion_code in VALID_EMOTIONS:
        score = getattr(result, "emotion_score", None)
        safe_score = (
            f"{score:.3f}"
            if isinstance(score, (int, float)) and math.isfinite(score) and 0 <= score <= 1
            else "unavailable"
        )
    else:
        detect_reason = detect_reason or ("invalid_emotion_code" if result is not None else "no_emotion_result")
        emotion_code = None
        safe_score = "unavailable"

    if write_cache and result is not None and message_id:
        from app.core.memory.emotion.emotion_cache import set_cached_emotion

        # UUID → str，与快写侧 original_message_id 的 str 形态严格一致
        await set_cached_emotion(str(message_id), result)

    policy = await _policy_for_result(result, app_id, tenant_id)
    prompt = build_prompt(base_prompt, policy, app_id=app_id, tenant_id=tenant_id)
    elapsed_ms = (perf_counter() - started) * 1000
    if prompt == (base_prompt or ""):
        reason = detect_reason or ("policy_unavailable" if policy is None else "strategy_not_rendered")
        failed = reason in ("task_error", "invalid_emotion_code")
        logger.log(
            logging.WARNING if failed else logging.INFO,
            "[EmotionReply] %s event=prompt_injection tenant_id=%s app_id=%s result=unchanged reason=%s emotion_code=%s emotion_score=%s error_type=%s elapsed_ms=%.1f",
            "failed" if failed else "skipped", tenant_id, app_id, reason,
            emotion_code, safe_score, error_type, elapsed_ms,
        )
    else:
        logger.info(
            "[EmotionReply] completed event=prompt_injection tenant_id=%s app_id=%s result=applied reason=none emotion_code=%s emotion_score=%s elapsed_ms=%.1f",
            tenant_id, app_id, emotion_code, safe_score, elapsed_ms,
        )
    return prompt


async def apply_detection(
    base_prompt: str,
    detection: Optional["asyncio.Task"],
    message_id: Any,
    *,
    app_id: Any = None,
    tenant_id: Any = None,
    write_cache: bool = True,
) -> str:
    """限制整段首字前情绪注入耗时；预算耗尽时使用原提示词。"""
    started = perf_counter()
    try:
        async with asyncio.timeout(settings.EMOTION_REPLY_INJECTION_TIMEOUT_SECONDS):
            return await _apply_detection_inner(
                base_prompt, detection, message_id,
                app_id=app_id, tenant_id=tenant_id, write_cache=write_cache,
            )
    except asyncio.TimeoutError:
        cancel_detection(detection)
        logger.warning(
            "[EmotionReply] failed event=prompt_injection tenant_id=%s app_id=%s reason=timeout timeout_s=%s elapsed_ms=%.1f",
            tenant_id, app_id, settings.EMOTION_REPLY_INJECTION_TIMEOUT_SECONDS,
            (perf_counter() - started) * 1000,
        )
        return base_prompt


async def resolve_and_inject(
    base_prompt: str,
    message: str,
    message_id: Any,
    *,
    app_id: Any = None,
    tenant_id: Any = None,
    write_cache: bool = True,
) -> str:
    """串行编排（供不便并发的场景与测试使用）：detect → （可选）写缓存 → 策略查询。

    ``write_cache`` 语义同 :func:`apply_detection`。
    """
    return await apply_detection(
        base_prompt, asyncio.create_task(detect_emotion(message)), message_id,
        app_id=app_id, tenant_id=tenant_id, write_cache=write_cache,
    )
