"""正写继承快写 BERT 情绪（D1/D2）。

正写不再调用 LLM 提取 Statement 情绪，而是按 ``StatementNode.chunk_id → ChunkNode.dialog_id → Dialogue.id``
找到同一条 user 消息的快写 Dialogue，继承其 ``emotion / emotion_score``：

- 类型：``normalize_emotion_type(Dialogue.emotion)``（10 类 code，旧值按映射归一化）；
- 强度：BERT score，clamp 到 [0, 1] 后保留 4 位小数；``neutral`` 强度记 0.0；
- 关键词：按各自 Statement 原文从词典匹配（≤3 个，必须出现在原文中）；
- subject 固定 ``self``，target 为 None。

运行时只依赖标准库、``emotion_buckets`` 和 ``dialogue_id_utils``；repository / graph_models 只在
``TYPE_CHECKING`` 下引用，避免与 ``dialog_repository`` 形成循环导入。
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Optional, Sequence

from app.core.memory.emotion.emotion_buckets import match_emotion_keywords, normalize_emotion_type
from app.core.memory.utils.dialogue_id_utils import DIALOGUE_ID_PREFIX

if TYPE_CHECKING:
    from app.core.memory.models.graph_models import ChunkNode, StatementNode
    from app.repositories.neo4j.dialog_repository import DialogRepository, DialogueEmotionRow

logger = logging.getLogger(__name__)

_NEUTRAL_CODE = "neutral"
_INHERITED_SUBJECT = "self"


@dataclass(frozen=True)
class InheritedEmotion:
    """单条 Dialogue 换算后的继承情绪。"""

    emotion_type: str
    emotion_intensity: float | None


@dataclass(frozen=True)
class InheritanceReport:
    """一次正写继承的汇总结果（只用于日志）。

    Attributes:
        statement_count: 参与继承的 user Statement 数
        inherited_count: 实际写入情绪的 Statement 数
        dialogue_ids: 参与继承的 Dialogue id（已排序）
        missing_reason: None / "timeout" / "not_found" / "query_error" / "emotion_null" / "invalid_value" /
            "unmapped"；只由 ``WritePipeline._inherit_fast_write_emotion`` 按优先级计算
        waited_seconds: 等待快写落库的累计秒数
    """

    statement_count: int
    inherited_count: int
    dialogue_ids: tuple[str, ...]
    missing_reason: str | None
    waited_seconds: float


def build_inherited_emotion(emotion: object, score: object) -> Optional[InheritedEmotion]:
    """把快写 Dialogue 的 ``emotion / emotion_score`` 换算为继承情绪（D2）。

    Args:
        emotion: ``Dialogue.emotion`` 原值。
        score: ``Dialogue.emotion_score`` 原值（BERT 分类概率）。

    Returns:
        ``emotion`` 归一化失败（None、未知值、非 str）时返回 None。``neutral`` 的强度固定为 0.0；
        其他类别 score 为 None / 非数值 / NaN 时强度为 None，否则 clamp 到 [0, 1] 后 round 4 位。
    """
    code = normalize_emotion_type(emotion)
    if code is None:
        return None
    if code == _NEUTRAL_CODE:
        return InheritedEmotion(emotion_type=code, emotion_intensity=0.0)
    return InheritedEmotion(emotion_type=code, emotion_intensity=_normalize_score(score))


def _normalize_score(score: object) -> float | None:
    if score is None or isinstance(score, bool):
        return None
    try:
        value = float(score)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if math.isnan(value):
        return None
    return round(min(1.0, max(0.0, value)), 4)


def is_inheritable_dialogue_id(dialogue_id: object) -> bool:
    """是否可能有快写对应节点：以 ``DIALOGUE_ID_PREFIX`` 开头且不含撞号后缀 ``#``。"""
    return (
        isinstance(dialogue_id, str)
        and dialogue_id.startswith(DIALOGUE_ID_PREFIX)
        and "#" not in dialogue_id
    )


def apply_inherited_emotions(
    statement_nodes: Sequence[StatementNode],
    chunk_nodes: Sequence[ChunkNode],
    dialogue_emotions: Mapping[str, DialogueEmotionRow],
) -> int:
    """原地把继承情绪写入 ``speaker == "user"`` 的 StatementNode。

    Args:
        statement_nodes: 本次正写的 StatementNode。
        chunk_nodes: 本次正写的 ChunkNode，用于 ``chunk_id → dialog_id`` 映射。
        dialogue_emotions: ``fetch_dialogue_emotions`` 的结果。

    Returns:
        实际写入的条数。chunk / dialogue 映射缺失或换算结果为 None 的 Statement 不写，保持原值。
        非 user Statement 不触碰。
    """
    chunk_to_dialog = {chunk.id: chunk.dialog_id for chunk in chunk_nodes}
    inherited_cache: dict[str, Optional[InheritedEmotion]] = {}
    written = 0
    for node in statement_nodes:
        if getattr(node, "speaker", None) != "user":
            continue
        dialog_id = chunk_to_dialog.get(node.chunk_id)
        if dialog_id is None:
            continue
        row = dialogue_emotions.get(dialog_id)
        if row is None:
            continue
        if dialog_id not in inherited_cache:
            inherited_cache[dialog_id] = build_inherited_emotion(row.emotion, row.emotion_score)
        inherited = inherited_cache[dialog_id]
        if inherited is None:
            continue
        node.emotion_type = inherited.emotion_type
        node.emotion_intensity = inherited.emotion_intensity
        node.emotion_keywords = match_emotion_keywords(node.statement, inherited.emotion_type)
        node.emotion_subject = _INHERITED_SUBJECT
        node.emotion_target = None
        written += 1
    return written


async def fetch_dialogue_emotions(
    repo: DialogRepository,
    end_user_id: str,
    dialogue_ids: Sequence[str],
    wait_seconds: float,
    poll_interval: float = 0.5,
) -> tuple[dict[str, DialogueEmotionRow], str | None, float]:
    """批量读取快写 Dialogue 情绪；节点尚未落库时有界轮询等待。

    首次查询全部 id；之后每 ``poll_interval`` 秒只重查缺失的 id，直到全部出现或累计等待 ≥ ``wait_seconds``。
    节点已存在（无论 emotion 是否为 null）即视为终态，不再重查。

    Args:
        repo: DialogRepository（由调用方构造并传入）。
        end_user_id: 终端用户 ID。
        dialogue_ids: 待查询的 Dialogue id。
        wait_seconds: 最长等待秒数，负数按 0 处理。
        poll_interval: 轮询间隔秒数，下限 0.01（防忙轮询）。

    Returns:
        ``(rows, fetch_reason, waited_seconds)``；``fetch_reason`` 为 None（全部查到节点）、
        ``"not_found"``（等待结束仍有缺失）或 ``"query_error"``（查询异常，不重试，返回异常前的结果）。
    """
    wait_seconds = max(float(wait_seconds), 0.0)
    poll_interval = max(float(poll_interval), 0.01)

    rows: dict[str, DialogueEmotionRow] = {}
    missing = list(dict.fromkeys(dialogue_ids))
    started = time.monotonic()
    waited = 0.0
    while True:
        try:
            found = await repo.get_dialogue_emotions(end_user_id, missing)
        except Exception as exc:
            logger.debug("[EmotionInherit] dialogue emotion query failed: %s", type(exc).__name__)
            return rows, "query_error", waited
        rows.update(found)
        missing = [dialogue_id for dialogue_id in missing if dialogue_id not in rows]
        if not missing:
            return rows, None, waited
        if waited >= wait_seconds:
            return rows, "not_found", waited
        await asyncio.sleep(min(poll_interval, wait_seconds - waited))
        waited = time.monotonic() - started
