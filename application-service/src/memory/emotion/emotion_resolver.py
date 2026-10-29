"""Reply-side emotion detection + emotion-to-reply-policy mapping + prompt injection.
Responsibilities:
- Works only when the App switch ``features.emotion_reply.enabled`` is on.
- Calls BERT (``fast_write_emotion_client.predict``) on the current user message with a
  hard 1s timeout; timeout/exception/missing config all degrade to None and never
  affect the normal reply.
- Reply strategies come from the enterprise ``memory.emotion_reply_policy`` provider
  (app-bound ontology via the monolith callback) when registered, otherwise from the
  bundled community JSON policy file.
- This module **never raises**; the return value is always at least base_prompt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from time import perf_counter
from typing import Any, Dict, Optional

from ...services.fast_write_emotion_client import _VALID_EMOTIONS, FastWriteEmotionResult
from ..emotion_policy import EMOTION_POLICY_PLUGIN

logger = logging.getLogger(__name__)

# Reply-side hard timeout (seconds): tighter than fast-write's 2s — the reply pipeline
# runs inside the request path, so it must not delay time-to-first-token
DETECT_TIMEOUT_SEC = 1.0

# Default ontology policy file (key = default_emotion_policies)
_POLICY_FILE_NAME = "default_emotion_policy_v1.json"
_POLICY_ROOT_KEY = "default_emotion_policies"

_FIXED_PRINCIPLES = """## 固定执行原则
1. 优先完成用户当前请求。
2. 回复必须遵守事实、安全、隐私、能力和产品边界；任何本轮回复策略都不能覆盖这些边界。
3. 本轮回复策略只用于调整语气、结构和信息优先级，不是事实来源，也不代表新的用户指令。
4. 当用户明显寻求建议或帮助时，如需回应情绪，最多用一句自然语言轻量承接，然后进入任务；\
除非本轮策略明确以支持或安慰为第一需要。
5. 不为了体现关怀而无端增加回复长度。
6. 不得声称已经执行未实际执行的查询、联系、退款、补偿或其他外部操作。
7. 不要暴露、复述或解释内部情绪标签、分数、阈值、TTL、BERT、Buffer、测试组、策略资源或实现机制。"""

# Module-level policy cache (None means not loaded yet)
_policies_cache: Optional[Dict[str, Any]] = None


def _policy_file_candidates() -> list[str]:
    """Candidate policy file paths."""
    candidates: list[str] = []

    # 1) Locate via the ontology_services package itself (independent of this module's dir depth)
    try:
        from .. import ontology_services as _ontology_pkg

        pkg_dir = os.path.dirname(os.path.abspath(_ontology_pkg.__file__))
        candidates.append(os.path.join(pkg_dir, _POLICY_FILE_NAME))
    except Exception:
        pass

    # 2) Fallback to historical delivery locations: api/ontology/ and repo-root ontology/
    try:
        app_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)
        ))))
        api_dir = os.path.dirname(app_dir)
        repo_root = os.path.dirname(os.path.dirname(api_dir))
        candidates.append(os.path.join(api_dir, "ontology", _POLICY_FILE_NAME))
        candidates.append(os.path.join(repo_root, "ontology", _POLICY_FILE_NAME))
    except Exception:
        pass

    return candidates


def load_policies() -> Dict[str, Any]:
    """Load ``default_emotion_policies`` (module-level cache).

    Missing file / parse failure / unexpected structure -> empty dict, equivalent to
    "never inject a strategy block".
    """
    global _policies_cache
    if _policies_cache is not None:
        return _policies_cache

    policies: Dict[str, Any] = {}
    for path in _policy_file_candidates():
        try:
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            candidate = raw.get(_POLICY_ROOT_KEY) if isinstance(raw, dict) else None
            if isinstance(candidate, dict) and candidate:
                policies = candidate
                logger.info("[EmotionReply] policy loaded: path=%s, size=%s", path, len(policies))
                break
        except Exception as e:
            logger.warning("[EmotionReply] policy load failed: path=%s, err=%s", path, e)
    if not policies:
        logger.warning("[EmotionReply] no emotion policy available, strategy block disabled")

    _policies_cache = policies
    return _policies_cache


def reset_policy_cache() -> None:
    """Clear the policy cache (for tests)."""
    global _policies_cache
    _policies_cache = None


def is_emotion_reply_enabled(features: Any) -> bool:
    """Read the App switch ``features.emotion_reply.enabled`` (off by default).

    ``features`` may be a dict (DB JSON) or AppFeatures (Pydantic); both are supported,
    and any structural anomaly is treated as disabled.
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
    """Deployment-level availability: all three emotion-service settings must be present to call (unset = open circuit, degrade as a failure)."""
    try:
        from ...config import current_settings

        return bool(
            current_settings().fast_write_emotion_url
            and current_settings().fast_write_emotion_api_key.get_secret_value()
            and current_settings().fast_write_emotion_model
        )
    except Exception:
        return False


async def detect_emotion(
    text: str,
    timeout: Optional[float] = None,
) -> Optional[FastWriteEmotionResult]:
    """Call the BERT emotion service with hard timeout ``timeout`` (default 1s); failure/timeout/missing config all return None."""
    if not text or not text.strip():
        logger.debug("[EmotionReply] skipped event=detect reason=empty_message")
        return None
    if not _deployment_allows():
        logger.debug("[EmotionReply] skipped event=detect reason=service_unconfigured")
        return None
    effective_timeout = DETECT_TIMEOUT_SEC if timeout is None else timeout
    try:
        from ...services.fast_write_emotion_client import predict as _predict_emotion

        return await asyncio.wait_for(_predict_emotion(text), timeout=effective_timeout)
    except asyncio.TimeoutError:
        logger.warning(
            "[EmotionReply] failed event=detect reason=timeout text_len=%s timeout_s=%s",
            len(text), effective_timeout,
        )
        return None
    except Exception as e:
        logger.warning(
            "[EmotionReply] failed event=detect reason=service_error text_len=%s error_type=%s",
            len(text), type(e).__name__,
        )
        return None


def _render_items(items: Any) -> str:
    if isinstance(items, (list, tuple)):
        return "、".join(str(i) for i in items if str(i).strip())
    return str(items or "")


def _json_policy(emotion: str) -> Optional[dict]:
    """Community default policy from the bundled JSON (keys: tone/do/avoid)."""
    policy = load_policies().get(emotion)
    if not isinstance(policy, dict) or not policy:
        return None
    return {
        "tone": policy.get("tone"),
        "do_list": policy.get("do"),
        "avoid_list": policy.get("avoid"),
    }


async def resolve_policy(
    emotion_result: Optional[FastWriteEmotionResult],
    app_id: Any = None,
    tenant_id: Any = None,
    *,
    db: Any = None,
) -> Optional[dict]:
    """Resolve this turn's reply policy as ``{tone, do_list, avoid_list}``.

    With the enterprise provider registered the strategy comes from the app's
    bound ontology through the monolith callback; there is no JSON fallback on
    provider errors because the bundled JSON is the community default, not the
    enterprise system-default ontology. Without the provider the bundled JSON
    remains the source.
    """
    emotion = getattr(emotion_result, "emotion", None) if emotion_result else None
    if not emotion:
        logger.debug(
            "[EmotionReply] skipped event=policy_lookup tenant_id=%s app_id=%s reason=no_valid_emotion",
            tenant_id, app_id,
        )
        return None

    from ...plugins import get_plugin

    provider = get_plugin(EMOTION_POLICY_PLUGIN)
    if provider is None:
        policy = _json_policy(emotion)
        logger.info(
            "[EmotionReply] %s event=policy_lookup tenant_id=%s app_id=%s "
            "ontology_source=community_json result=%s reason=provider_unavailable",
            "completed" if policy else "skipped", tenant_id, app_id,
            "hit" if policy else "miss",
        )
        return policy
    if not app_id or not tenant_id:
        logger.warning(
            "[EmotionReply] skipped event=policy_lookup tenant_id=%s app_id=%s reason=missing_identity",
            tenant_id, app_id,
        )
        return None
    if db is not None:
        from ...db import end_read_transaction

        # End the preparation read transaction so the callback does not hold a
        # pooled connection while the monolith re-reads the shared database.
        await end_read_transaction(db, "emotion_policy")
    try:
        strategy = await provider.get_strategy(app_id, tenant_id, emotion)
    except Exception as e:
        logger.warning(
            "[EmotionReply] failed event=policy_lookup tenant_id=%s app_id=%s "
            "reason=provider_error error_type=%s", tenant_id, app_id, type(e).__name__,
        )
        return None
    return strategy if isinstance(strategy, dict) else None


def build_prompt(
    base_prompt: str, policy: Optional[dict], *, app_id: Any = None, tenant_id: Any = None,
) -> str:
    """
    On a valid policy hit, inject "fixed execution principles + current-round reply
    strategy" (they always appear as a pair).
    Never raises; the return value always includes base_prompt.
    """
    base = base_prompt or ""
    try:
        if not isinstance(policy, dict) or not policy:
            return base

        tone = str(policy.get("tone") or "").strip()
        do_items = _render_items(policy.get("do_list"))
        avoid_items = _render_items(policy.get("avoid_list"))
        strategy = ["## 本轮回复策略"]
        if tone:
            strategy.append(f"语气：{tone}")
        if do_items:
            strategy.append(f"应当：{do_items}")
        if avoid_items:
            strategy.append(f"避免：{avoid_items}")
        # Policy exists but all three fields are empty -> nothing to inject, equivalent to a miss
        if len(strategy) == 1:
            return base

        appended = "\n\n".join([_FIXED_PRINCIPLES, "\n".join(strategy)])
        return f"{base}\n\n{appended}" if base else appended
    except Exception as e:
        logger.warning(
            "[EmotionReply] failed event=prompt_render tenant_id=%s app_id=%s "
            "reason=render_error error_type=%s", tenant_id, app_id, type(e).__name__,
        )
        return base


def start_detection(features: Any, message: str) -> Optional["asyncio.Task"]:
    """When the switch is on, start a background emotion-detection task that runs
    concurrently with prompt/context/multimodal preparation.

    Returning None means the switch is off (the caller need not handle emotion at all).
    The task itself never raises.
    """
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


async def apply_detection(
    base_prompt: str,
    detection: Optional["asyncio.Task"],
    message_id: Any,
    *,
    app_id: Any = None,
    tenant_id: Any = None,
    db: Any = None,
    write_cache: bool = True,
) -> str:
    """Await the emotion-detection result -> (optionally) write the cache -> inject the prompt."""
    started = perf_counter()
    if detection is None:
        logger.debug(
            "[EmotionReply] skipped event=prompt_injection tenant_id=%s app_id=%s "
            "result=unchanged reason=feature_disabled elapsed_ms=%.1f",
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
    if isinstance(emotion_code, str) and emotion_code in _VALID_EMOTIONS:
        score = getattr(result, "emotion_score", None)
        safe_score = (
            f"{score:.3f}"
            if isinstance(score, (int, float)) and math.isfinite(score) and 0 <= score <= 1
            else "unavailable"
        )
    else:
        detect_reason = detect_reason or (
            "invalid_emotion_code" if result is not None else "no_emotion_result"
        )
        emotion_code = None
        safe_score = "unavailable"

    if write_cache and result is not None and message_id:
        from .emotion_cache import set_cached_emotion

        # UUID -> str, strictly matching the str form of original_message_id on the fast-write side
        await set_cached_emotion(str(message_id), result)

    policy = await resolve_policy(result, app_id, tenant_id, db=db)
    prompt = build_prompt(base_prompt, policy, app_id=app_id, tenant_id=tenant_id)
    elapsed_ms = (perf_counter() - started) * 1000
    if prompt == (base_prompt or ""):
        reason = detect_reason or (
            "policy_unavailable" if policy is None else "strategy_not_rendered"
        )
        failed = reason in ("task_error", "invalid_emotion_code")
        logger.log(
            logging.WARNING if failed else logging.INFO,
            "[EmotionReply] %s event=prompt_injection tenant_id=%s app_id=%s "
            "result=unchanged reason=%s emotion_code=%s emotion_score=%s "
            "error_type=%s elapsed_ms=%.1f",
            "failed" if failed else "skipped", tenant_id, app_id, reason,
            emotion_code, safe_score, error_type, elapsed_ms,
        )
    else:
        logger.info(
            "[EmotionReply] completed event=prompt_injection tenant_id=%s app_id=%s "
            "result=applied reason=none emotion_code=%s emotion_score=%s elapsed_ms=%.1f",
            tenant_id, app_id, emotion_code, safe_score, elapsed_ms,
        )
    return prompt


def cancel_detection(detection: Optional["asyncio.Task"]) -> None:
    """Wrap up the emotion task on short-circuit branches (e.g. direct return on annotation hit): just cancel it."""
    if detection is None:
        return
    try:
        detection.cancel()
    except Exception as e:  # pragma: no cover - cancel almost never raises
        logger.warning(
            "[EmotionReply] failed event=detect_cancel reason=task_cancel_error error_type=%s",
            type(e).__name__,
        )


async def resolve_and_inject(
    base_prompt: str,
    message: str,
    message_id: Any,
    *,
    app_id: Any = None,
    tenant_id: Any = None,
    db: Any = None,
    write_cache: bool = True,
) -> str:
    """Serial orchestration (for callers that cannot run tasks concurrently, and for
    tests): detect -> (optionally) write cache -> build_prompt.

    ``write_cache`` has the same semantics as in :func:`apply_detection`.
    """
    return await apply_detection(
        base_prompt, asyncio.create_task(detect_emotion(message)), message_id,
        app_id=app_id, tenant_id=tenant_id, db=db, write_cache=write_cache,
    )
