"""正写继承快写 BERT 情绪（D1/D2）。

正写不再调用 LLM 提取 Statement 情绪，而是按 ``StatementNode.chunk_id → ChunkNode.dialog_id → Dialogue.id``
找到同一条 user 消息的快写 Dialogue，继承其 ``emotion / emotion_score``：

- 类型：``normalize_emotion_type(Dialogue.emotion)``（10 类 code，旧值按映射归一化）；
- 强度：BERT score，clamp 到 [0, 1] 后保留 4 位小数；``neutral`` 强度记 0.0；
- 关键词：按各自 Statement 原文从词典匹配（≤3 个，必须出现在原文中）；
- subject 固定 ``self``，target 为 None。

只继承 ``write_mode='fast'`` 的 Dialogue：节点已被正写接管（normal / None / 未知值）时，其情绪可能为空
或属于旧内容，一律不继承。

正写只查询一次（快写一般早于正写落库）；查不到时 Statement 情绪暂时为空，由「握手补写」兜底：
``DIALOGUE_NODE_SAVE`` 在节点写锁内返回写入前状态 ``prev_*``，同一 Dialogue 上快写 / 正写的写入被锁串行化，
后到的一方一定能看到先到一方已提交的结果，由后到的一方调用 ``backfill_statement_emotions`` 补写：

- 正写后到（``prev_write_mode='fast'`` 且 ``prev_emotion`` 非空）：正写提交后用 prev 情绪补本批 Statement；
- 快写后到（``prev_write_mode='normal'``，本次把情绪补进了 Dialogue）：快写提交后补该 Dialogue 下的 Statement。

补写与正写继承共用 ``build_statement_emotion_fields`` 规则，只补空、不覆盖。已知遗留：Dialogue id 没有内容哈希，
同一 conv_id + seq 内容变更后被重新处理时，补写分不清新旧内容。

运行时只依赖标准库、``emotion_buckets`` 和 ``dialogue_id_utils``；repository / graph_models / outbox 只在
``TYPE_CHECKING`` 下引用或在函数内延迟导入，避免与 ``dialog_repository`` 形成循环导入。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Mapping, Optional, Protocol, Sequence

from app.core.memory.emotion.emotion_buckets import match_emotion_keywords, normalize_emotion_type
from app.core.memory.utils.dialogue_id_utils import DIALOGUE_ID_PREFIX

if TYPE_CHECKING:
    from app.core.memory.models.graph_models import ChunkNode, StatementNode
    from app.core.memory.storage.outbox.repository import OutboxRepository
    from app.repositories.neo4j.dialog_repository import DialogRepository, DialogueEmotionRow

logger = logging.getLogger(__name__)

_NEUTRAL_CODE = "neutral"
_INHERITED_SUBJECT = "self"

# 只有快写写入的 Dialogue 才可继承情绪
FAST_WRITE_MODE = "fast"
NORMAL_WRITE_MODE = "normal"

# backfill_statement_emotions 的来源（只用于日志）
BACKFILL_SOURCE_FAST_SIDE = "fast_side"
BACKFILL_SOURCE_NORMAL_SIDE = "normal_side"

# fetch_dialogue_emotions 的汇总原因
REASON_QUERY_ERROR = "query_error"
REASON_NOT_FOUND = "not_found"
REASON_SUPERSEDED_BY_NORMAL = "superseded_by_normal"


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
        missing_reason: None / "timeout" / "query_error" / "not_found" / "superseded_by_normal" /
            "emotion_null" / "invalid_value" / "unmapped"；只由 ``WritePipeline._inherit_fast_write_emotion``
            按优先级计算。``emotion_null`` 仅指 fast 节点但 BERT 情绪为空
        fast_count: 查到的 fast Dialogue 数
        superseded_count: 节点存在但非 fast（已被正写接管）的 Dialogue 数
        not_found_count: 查询时尚未落库（或因查询异常未能确认）的 Dialogue 数
    """

    statement_count: int
    inherited_count: int
    dialogue_ids: tuple[str, ...]
    missing_reason: str | None
    fast_count: int = 0
    superseded_count: int = 0
    not_found_count: int = 0


@dataclass(frozen=True)
class DialogueEmotionFetchResult:
    """``fetch_dialogue_emotions`` 的结果。

    每个 id 的状态由三个集合推出：在 ``rows`` 中 = fast，在 ``superseded_ids`` 中 = 非 fast 终态，
    在 ``unresolved_ids`` 中 = 未找到（或因查询异常未能确认）。

    Attributes:
        rows: 只含 ``write_mode='fast'`` 的行，可直接传给 ``apply_inherited_emotions``
        superseded_ids: 节点存在但非 fast（normal / None / 未知值）的 id，按请求顺序
        unresolved_ids: 查询时节点尚不存在的 id；查询异常时为全部请求 id
        query_failed: 是否查询异常
    """

    rows: dict[str, DialogueEmotionRow]
    superseded_ids: tuple[str, ...] = ()
    unresolved_ids: tuple[str, ...] = ()
    query_failed: bool = False

    @property
    def reason(self) -> str | None:
        """汇总原因，优先级 ``query_error`` > ``not_found`` > ``superseded_by_normal`` > None。

        not_found 代表快写落库晚于正写查询（交给握手补写），排查价值高于 superseded_by_normal，
        所以混合批次优先报 not_found。
        """
        if self.query_failed:
            return REASON_QUERY_ERROR
        if self.unresolved_ids:
            return REASON_NOT_FOUND
        if self.superseded_ids:
            return REASON_SUPERSEDED_BY_NORMAL
        return None


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
        dialogue_emotions: ``fetch_dialogue_emotions(...).rows``；只继承 ``write_mode='fast'`` 的行。

    Returns:
        实际写入的条数。chunk / dialogue 映射缺失、行非 fast 或换算结果为 None 的 Statement 不写，
        保持原值。非 user Statement 不触碰。
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
        # 纵深防御：非 fast 行（已被正写接管）的情绪可能为空或属于旧内容，不继承
        if row.write_mode != FAST_WRITE_MODE:
            continue
        if dialog_id not in inherited_cache:
            inherited_cache[dialog_id] = build_inherited_emotion(row.emotion, row.emotion_score)
        inherited = inherited_cache[dialog_id]
        if inherited is None:
            continue
        for field, value in build_statement_emotion_fields(node.statement, inherited).items():
            setattr(node, field, value)
        written += 1
    return written


def build_statement_emotion_fields(
    statement_text: Optional[str],
    inherited: InheritedEmotion,
) -> dict[str, object]:
    """按单条 Statement 原文计算要写入的 5 个情绪字段（正写继承与握手补写共用同一套规则）。

    Args:
        statement_text: Statement 原文，用于从词典匹配关键词。
        inherited: ``build_inherited_emotion`` 换算出的继承情绪。

    Returns:
        ``emotion_type / emotion_intensity / emotion_keywords / emotion_subject / emotion_target`` 字典；
        关键词 ≤3 个且必须出现在原文中，subject 固定 ``self``，target 为 None。
    """
    return {
        "emotion_type": inherited.emotion_type,
        "emotion_intensity": inherited.emotion_intensity,
        "emotion_keywords": match_emotion_keywords(statement_text, inherited.emotion_type),
        "emotion_subject": _INHERITED_SUBJECT,
        "emotion_target": None,
    }


def should_backfill_on_fast_side(state: Any, emotion: object, emotion_score: object) -> bool:
    """快写提交后是否需要补写 Statement 情绪。

    条件与 ``DIALOGUE_NODE_SAVE`` 中迟到快写补 Dialogue 情绪的条件等价：节点在本次写入前已是 normal
    （正写先到、Statement 已提交）、节点当时还没有情绪、本次 BERT 情绪成对非空。``prev_emotion`` 非空只出现在
    快写重复投递或旧内容残留情绪（已知遗留：id 无内容哈希，分不清新旧内容），此时跳过更安全。

    Args:
        state: ``GraphWriteResult.dialogue_states`` 中本 Dialogue 的写入前状态（可为 None）。
        emotion: 本次快写的 BERT 情绪。
        emotion_score: 本次快写的 BERT 分数。

    Returns:
        是否调用 ``backfill_statement_emotions``。
    """
    return (
        state is not None
        and getattr(state, "prev_write_mode", None) == NORMAL_WRITE_MODE
        and getattr(state, "prev_emotion", None) is None
        and emotion is not None
        and emotion_score is not None
    )


def select_normal_side_backfills(
    dialogue_states: Mapping[str, Any],
    statement_nodes: Sequence[StatementNode],
    chunk_nodes: Sequence[ChunkNode],
) -> list[tuple[str, object, object]]:
    """正写提交后，选出需要用快写情绪补写 Statement 的 Dialogue。

    入选条件：id 可继承（``is_inheritable_dialogue_id``）、写入前节点为 fast 且有情绪（快写插在正写查询与提交
    之间落库），并且本批映射到该 Dialogue 的 user Statement 中至少有一条情绪为空。Step 3.4 已继承成功的
    Dialogue 在内存里就被过滤掉，不再多发一次 Neo4j 往返。

    Args:
        dialogue_states: ``GraphWriteResult.dialogue_states``。
        statement_nodes: 本次正写的 StatementNode（已经过 Step 3.4 继承）。
        chunk_nodes: 本次正写的 ChunkNode，用于 ``chunk_id → dialog_id`` 映射。

    Returns:
        ``(dialogue_id, prev_emotion, prev_emotion_score)`` 列表，按 id 排序。
    """
    chunk_to_dialog = {chunk.id: chunk.dialog_id for chunk in chunk_nodes}
    pending_dialogues = {
        chunk_to_dialog[node.chunk_id]
        for node in statement_nodes
        if getattr(node, "speaker", None) == "user"
        and getattr(node, "emotion_type", None) is None
        and node.chunk_id in chunk_to_dialog
    }
    selected: list[tuple[str, object, object]] = []
    for dialogue_id in sorted(pending_dialogues):
        if not is_inheritable_dialogue_id(dialogue_id):
            continue
        state = dialogue_states.get(dialogue_id)
        if state is None or getattr(state, "prev_write_mode", None) != FAST_WRITE_MODE:
            continue
        prev_emotion = getattr(state, "prev_emotion", None)
        if prev_emotion is None:
            continue
        selected.append((dialogue_id, prev_emotion, getattr(state, "prev_emotion_score", None)))
    return selected


class WriteTransactionRunner(Protocol):
    """能在一个托管写事务中执行回调的连接（``Neo4jConnector`` / ``Neo4jClient`` 均满足）。"""

    async def execute_write_transaction(self, transaction_func: Callable[[Any], Awaitable[Any]]) -> Any:
        ...


async def backfill_statement_emotions(
    connector: WriteTransactionRunner,
    *,
    end_user_id: str,
    dialogue_id: str,
    emotion: object,
    emotion_score: object,
    source: str,
    outbox_repository: Optional[OutboxRepository] = None,
) -> int:
    """把快写 BERT 情绪补写到某 Dialogue 下尚无情绪的 user Statement（握手补写，只补空、不覆盖）。

    在一个写事务里先读候选（``Chunk {dialog_id}-[:CONTAINS]->Statement``，按 ``end_user_id`` 隔离），
    按各自原文用 ``build_statement_emotion_fields`` 计算情绪，再 UNWIND 批量写入（写时加锁后再判一次
    ``emotion_type IS NULL``）。提交后只对实际更新的 Statement 入 outbox upsert，由 projector 同步 ES。

    Args:
        connector: 提供 ``execute_write_transaction`` 的 Neo4j 连接。
        end_user_id: 终端用户 ID（租户隔离）。
        dialogue_id: Dialogue id。
        emotion: 快写 BERT 情绪原值。
        emotion_score: 快写 BERT 分数原值。
        source: ``BACKFILL_SOURCE_FAST_SIDE`` / ``BACKFILL_SOURCE_NORMAL_SIDE``，只用于日志。
        outbox_repository: 可选 outbox 仓储（测试注入）；None 时由 ``enqueue_events`` 自行创建。

    Returns:
        Neo4j 中实际补写的 Statement 数。任何异常只打 warning 并返回 0，不向上抛。
    """
    inherited = build_inherited_emotion(emotion, emotion_score)
    if inherited is None or not end_user_id or not dialogue_id:
        return 0

    updated_ids: list[str] = []
    candidate_count = 0
    try:
        from app.repositories.neo4j.cypher_queries import (
            STATEMENT_EMOTION_BACKFILL_CANDIDATES,
            STATEMENT_EMOTION_BACKFILL_SAVE,
        )

        async def _tx(tx: Any) -> tuple[int, list[str]]:
            result = await tx.run(
                STATEMENT_EMOTION_BACKFILL_CANDIDATES,
                end_user_id=end_user_id,
                dialogue_id=dialogue_id,
            )
            candidates = await result.data()
            rows = [
                {"id": record["id"], **build_statement_emotion_fields(record.get("statement"), inherited)}
                for record in candidates
                if record.get("id")
            ]
            if not rows:
                return len(candidates), []
            result = await tx.run(STATEMENT_EMOTION_BACKFILL_SAVE, rows=rows, end_user_id=end_user_id)
            saved = await result.data()
            return len(candidates), [str(record["id"]) for record in saved if record.get("id")]

        candidate_count, updated_ids = await connector.execute_write_transaction(_tx)
    except Exception as exc:
        logger.warning(
            "[EmotionBackfill] failed: source=%s end_user_id=%s dialogue_id=%s error=%s",
            source,
            end_user_id,
            dialogue_id,
            type(exc).__name__,
        )
        return 0

    updated_ids = list(dict.fromkeys(updated_ids))
    if not updated_ids:
        return 0

    try:
        from app.core.memory.storage.enums import MemoryNodeType
        from app.core.memory.storage.outbox.producer import enqueue_events
        from app.core.memory.storage.outbox.types import OutboxEventInput, OutboxOperation

        await enqueue_events(
            [
                OutboxEventInput(
                    label=MemoryNodeType.STATEMENT,
                    node_id=statement_id,
                    operation=OutboxOperation.UPSERT,
                )
                for statement_id in updated_ids
            ],
            repository=outbox_repository,
        )
    except Exception as exc:
        # Neo4j 已补写，ES 要等这些 Statement 下一次 upsert 才会同步
        logger.warning(
            "[EmotionBackfill] outbox enqueue failed: source=%s end_user_id=%s dialogue_id=%s "
            "neo4j_updated=%d outbox=failed error=%s",
            source,
            end_user_id,
            dialogue_id,
            len(updated_ids),
            type(exc).__name__,
        )
        return 0

    logger.info(
        "[EmotionBackfill] source=%s end_user_id=%s dialogue_id=%s candidates=%d updated=%d",
        source,
        end_user_id,
        dialogue_id,
        candidate_count,
        len(updated_ids),
    )
    return len(updated_ids)


async def fetch_dialogue_emotions(
    repo: DialogRepository,
    end_user_id: str,
    dialogue_ids: Sequence[str],
) -> DialogueEmotionFetchResult:
    """单次批量读取快写 Dialogue 情绪，不等待、不轮询。

    快写一般早于正写落库；查不到的 id（快写慢于正写）由握手补写兜底，见模块说明。
    仅 ``write_mode='fast'`` 的节点可继承，查到的节点按 write_mode 分类：

    - fast：进入 ``rows``；
    - 节点存在但非 fast（normal / None / 未知值）：进入 ``superseded_ids``。这类节点已被正写接管，
      情绪可能为空（快写被 canWrite 守卫挡掉），也可能属于旧内容，均不可继承；
    - 节点不存在：进入 ``unresolved_ids``。

    查询不过滤 ``delete_at``：``DIALOGUE_NODE_SAVE`` 不会清掉 ``delete_at``，同一槽位软删后再快写，节点内容
    和情绪已是新的但 ``delete_at`` 仍在；若过滤，这类节点会被误判为 not_found。

    Args:
        repo: DialogRepository（由调用方构造并传入）。
        end_user_id: 终端用户 ID。
        dialogue_ids: 待查询的 Dialogue id（重复 id 去重，保持首次出现顺序）。

    Returns:
        ``DialogueEmotionFetchResult``；``reason`` 优先级 ``query_error``（查询异常，全部 id 记为 unresolved）
        > ``not_found`` > ``superseded_by_normal`` > None。
    """
    ids = list(dict.fromkeys(dialogue_ids))
    if not ids:
        return DialogueEmotionFetchResult(rows={})

    try:
        found = await repo.get_dialogue_emotions(end_user_id, ids)
    except Exception as exc:
        logger.debug("[EmotionInherit] dialogue emotion query failed: %s", type(exc).__name__)
        return DialogueEmotionFetchResult(rows={}, unresolved_ids=tuple(ids), query_failed=True)

    rows: dict[str, DialogueEmotionRow] = {}
    superseded: list[str] = []
    unresolved: list[str] = []
    for dialogue_id in ids:
        row = found.get(dialogue_id)
        if row is None:
            unresolved.append(dialogue_id)
        elif row.write_mode == FAST_WRITE_MODE:
            rows[dialogue_id] = row
        else:
            superseded.append(dialogue_id)

    return DialogueEmotionFetchResult(
        rows=rows,
        superseded_ids=tuple(superseded),
        unresolved_ids=tuple(unresolved),
    )
