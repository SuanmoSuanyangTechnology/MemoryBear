"""回复侧情绪识别 + 情绪→回复策略映射 + 提示词注入。
职责边界：
- 只在 App 开关 ``features.emotion_reply.enabled`` 打开时工作。
- 对当前 user 消息调 BERT（``fast_write_emotion_client.predict``），硬超时 1s；
  超时/异常/未配置一律降级为 None，绝不影响正常回复。
- 本模块**永不抛异常**，返回值始终 ≥ base_prompt。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional

from app.services.fast_write_emotion_client import FastWriteEmotionResult

logger = logging.getLogger(__name__)

# 回复侧硬超时（秒）：比快写的 2s 更紧，回复链路在请求内，不能拖首字延迟
DETECT_TIMEOUT_SEC = 1.0

# 本体默认策略文件（key = default_emotion_policies）
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

# 模块级 policy 缓存（None 表示尚未加载）
_policies_cache: Optional[Dict[str, Any]] = None


def _policy_file_candidates() -> list[str]:
    """候选 policy 文件路径"""
    candidates: list[str] = []

    # 1) 用 ontology_services 包自身定位（不依赖本模块的目录层级深度）
    try:
        from app.core.memory import ontology_services as _ontology_pkg

        pkg_dir = os.path.dirname(os.path.abspath(_ontology_pkg.__file__))
        candidates.append(os.path.join(pkg_dir, _POLICY_FILE_NAME))
    except Exception:
        pass

    # 2) 历史交付位置兜底：api/ontology/ 与仓库根 ontology/
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
    """加载 ``default_emotion_policies``（模块级缓存）。

    文件缺失 / 解析失败 / 结构不符 → 空表，等价于"永不注入策略块"。
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
    """清空 policy 缓存（测试用）。"""
    global _policies_cache
    _policies_cache = None


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
            settings.FAST_WRITE_EMOTION_URL
            and settings.FAST_WRITE_EMOTION_API_KEY
            and settings.FAST_WRITE_EMOTION_MODEL
        )
    except Exception:
        return False


async def detect_emotion(
    text: str,
    timeout: Optional[float] = None,
) -> Optional[FastWriteEmotionResult]:
    """调 BERT 情绪服务，硬超时 ``timeout``（默认 1s）；失败/超时/未配置一律返回 None。"""
    if not text or not text.strip():
        return None
    if not _deployment_allows():
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


def _render_items(items: Any) -> str:
    if isinstance(items, (list, tuple)):
        return "、".join(str(i) for i in items if str(i).strip())
    return str(items or "")


def build_prompt(base_prompt: str, emotion_result: Optional[FastWriteEmotionResult]) -> str:
    """
    命中有效 policy 时，注入"固定执行原则 + 本轮回复策略"（两者成对出现）。
    永不抛异常；返回值一定包含 base_prompt。
    """
    base = base_prompt or ""
    try:
        emotion = getattr(emotion_result, "emotion", None) if emotion_result else None
        policy = load_policies().get(emotion) if emotion else None
        if not isinstance(policy, dict) or not policy:
            return base

        tone = str(policy.get("tone") or "").strip()
        do_items = _render_items(policy.get("do"))
        avoid_items = _render_items(policy.get("avoid"))
        strategy = ["## 本轮回复策略"]
        if tone:
            strategy.append(f"语气：{tone}")
        if do_items:
            strategy.append(f"应当：{do_items}")
        if avoid_items:
            strategy.append(f"避免：{avoid_items}")
        # policy 存在但三项均为空 → 无可注入内容，等同未命中
        if len(strategy) == 1:
            return base

        appended = "\n\n".join([_FIXED_PRINCIPLES, "\n".join(strategy)])
        return f"{base}\n\n{appended}" if base else appended
    except Exception as e:
        logger.warning("[EmotionReply] build_prompt failed, keeping base prompt: err=%s", e)
        return base


def start_detection(features: Any, message: str) -> Optional["asyncio.Task"]:
    """开关开启时启动后台情绪识别任务，与提示词/上下文/多模态准备并发执行。

    返回 None 表示开关关闭（调用方无需做任何情绪处理）。任务本身永不抛异常。
    """
    if not is_emotion_reply_enabled(features):
        return None
    try:
        return asyncio.ensure_future(detect_emotion(message))
    except Exception as e:
        logger.warning("[EmotionReply] failed to start detection task: err=%s", e)
        return None


async def apply_detection(
    base_prompt: str,
    detection: Optional["asyncio.Task"],
    message_id: Any,
    *,
    write_cache: bool = True,
) -> str:
    """等待情绪识别结果 → （可选）写缓存 → 注入提示词。"""
    if detection is None:
        return base_prompt
    result: Optional[FastWriteEmotionResult] = None
    try:
        result = await detection
    except Exception as e:
        logger.warning("[EmotionReply] detection task failed, degrading: err=%s", e)
        result = None

    if write_cache and result is not None and message_id:
        from app.core.memory.emotion.emotion_cache import set_cached_emotion

        # UUID → str，与快写侧 original_message_id 的 str 形态严格一致
        await set_cached_emotion(str(message_id), result)

    return build_prompt(base_prompt, result)


def cancel_detection(detection: Optional["asyncio.Task"]) -> None:
    """短路分支（如标注命中直接返回）的情绪任务收尾：直接取消。"""
    if detection is None:
        return
    try:
        detection.cancel()
    except Exception as e:  # pragma: no cover - cancel 几乎不会抛
        logger.warning("[EmotionReply] cancel detection failed: err=%s", e)


async def resolve_and_inject(
    base_prompt: str,
    message: str,
    message_id: Any,
    *,
    write_cache: bool = True,
) -> str:
    """串行编排（供不便并发的场景与测试使用）：detect → （可选）写缓存 → build_prompt。

    ``write_cache`` 语义同 :func:`apply_detection`。
    """
    result = await detect_emotion(message)
    if write_cache and result is not None and message_id:
        from app.core.memory.emotion.emotion_cache import set_cached_emotion

        await set_cached_emotion(str(message_id), result)
    return build_prompt(base_prompt, result)
