"""
FastWritePipeline — 快速写入流水线

三步流水线：清洗 → Embedding → 写入 :Dialogue 节点。

- 不继承、不复用 WritePipeline 编排代码，避免耦合。
- 共用底层能力（embedder / neo4j connector）单独懒加载。
- 产品语义约束：1 条 user message → 1 个 DialogueNode，不切块、不截断。
- 关键失败向上抛出异常（不降级为业务 failed 结果），finally 中 _cleanup。
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import TYPE_CHECKING, List, Optional, Tuple

if TYPE_CHECKING:
    from datetime import datetime

    from app.schemas.memory_config_schema import MemoryConfig

from app.core.memory.models.graph_models import DialogueNode
from app.core.memory.utils.dialogue_id_utils import build_dialogue_id
from app.core.memory.utils.log.bear_logger import BearLogger
from app.core.utils.datetime_utils import (
    as_utc_aware,
    ensure_dialog_at,
    parse_iso_to_utc_naive,
)
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

logger = logging.getLogger(__name__)
# 与 WritePipeline 共用同一个结构化步骤日志器（同名 logger，输出风格一致）
bear = BearLogger("memory.pipeline")

# 纯标点/空白匹配（中英文标点 + 空白）。fullmatch 命中即视为无有效内容。
_PUNCT_PATTERN = re.compile(
    r'''^[\s.,!?;:'"()\[\]{}。，！？；：''""（）【】《》…—-]+$'''
)


def _is_punct_only(text: str) -> bool:
    """判断字符串是否仅由标点/空白构成。"""
    return bool(_PUNCT_PATTERN.fullmatch(text))


class FastWritePipeline:
    """快速写入三步流水线编排器。"""

    CONTENT_WARN_THRESHOLD = 8_000   # 超长告警阈值（不阻止写入）
    NEO4J_MERGE_MAX_RETRY = 3
    # Embedding 单步硬上限（秒）。底层 LangChain client 自带 max_retries * timeout，
    # 导致 worker 被 SIGKILL 而非降级。此处用 asyncio.wait_for 兜底，超时即降级为 None。
    EMBED_TIMEOUT_SEC = 15

    def __init__(self, memory_config: "MemoryConfig", end_user_id: str, language: str = "zh"):
        self.memory_config = memory_config
        self.end_user_id = end_user_id
        self.language = language

        # 懒加载底层能力，首次使用时初始化
        self._embedder = None
        self._neo4j = None

    async def run(
        self,
        target_message: dict,
        conversation_id: str = "",
        message_seq: int = 0,
        source: str = "",
        dispatch_at: str = "",
    ) -> dict:
        """驱动三步流水线。

        用 BearLogger 输出与 WritePipeline 一致的结构化步骤日志：pipeline 起止用
        ═══ 分隔线，每步完成输出 ``▶ [i/3] 分类：描述 ── ✔ 秒数`` 一行；步骤/流水线
        失败由 BearLogger 记 ✘ 行并向上抛出（堆栈由 Celery 任务层 logger.exception 兜底）。

        正常返回 {"status": "success"|"dropped", "dialog_id": str | None}。
        关键失败不返回 failed 字典，而是向上抛出异常。finally 中执行 _cleanup。

        dispatch_at 为任务派发时刻的 UTC ISO 8601 字符串，用于 DialogueNode.created_at
        的时间来源降级（与 Normal Write 一致）：
        target_message.dialog_at → dispatch_at → 服务端当前时间（ensure_dialog_at 兜底）。
        """
        async with bear.pipeline(
            "FastWritePipeline",
            mode="快速写入",
            end_user_id=self.end_user_id,
            conv=conversation_id or "-",
            seq=message_seq,
            source=source or "-",
        ):
            try:
                # Step 1/3 — 轻量清洗
                async with bear.step(1, 3, "清洗", "轻量清洗") as s:
                    content = target_message.get("content", "") if target_message else ""
                    cleaned, drop_reason = self._clean(content)
                    s.metadata(
                        text_len=len(cleaned),
                        dropped=bool(drop_reason),
                        reason=drop_reason or "-",
                    )
                if drop_reason:
                    return {"status": "dropped", "reason": drop_reason, "dialog_id": None}

                # Step 2/3 — Embedding（失败降级为 None，不重试）
                async with bear.step(2, 3, "Embedding", "向量化") as s:
                    embedding = await self._embed(cleaned)
                    s.metadata(has_embedding=embedding is not None)

                # 时间来源降级：dialog_at → dispatch_at → 当前时间（ensure_dialog_at 兜底）
                created_at = as_utc_aware(
                    parse_iso_to_utc_naive(
                        ensure_dialog_at(target_message.get("dialog_at") or dispatch_at)
                    )
                )

                # Step 3/3 — 构造并写入 DialogueNode
                async with bear.step(3, 3, "存储", "写入 Dialogue") as s:
                    node = self._build_dialogue_node(
                        content=cleaned,
                        embedding=embedding,
                        conversation_id=conversation_id,
                        message_seq=message_seq,
                        source=source,
                        created_at=created_at,
                    )
                    dialog_id = await self._persist(node)
                    s.metadata(dialog_id=dialog_id)

                return {"status": "success", "dialog_id": dialog_id}
            finally:
                await self._cleanup()

    # ──────────────────────────────────────────────
    # 以下为本任务范围外的方法占位，后续任务填充实现
    # ──────────────────────────────────────────────

    def _clean(self, content: str) -> Tuple[str, Optional[str]]:
        """Step 1 — 轻量清洗（纯规则）。

        返回 (cleaned_content, drop_reason)。drop_reason 非空表示应丢弃。

        规则：
        - content 为空 → ("", "empty")
        - strip 后为空或纯标点 → ("", "punct_only")
        - langid 语言检测，命中 "zh"/"en" 时更新 self.language，否则保留默认
        - 超长（> CONTENT_WARN_THRESHOLD）→ 告警日志，但不截断、不丢弃
        - 正常 → (cleaned, None)
        """
        if not content:
            return "", "empty"

        cleaned = content.strip()
        if not cleaned or _is_punct_only(cleaned):
            return "", "punct_only"

        import langid

        detected = langid.classify(cleaned)[0]
        self.language = detected if detected in ("zh", "en") else self.language

        if len(cleaned) > self.CONTENT_WARN_THRESHOLD:
            logger.warning(
                "[FastWrite] content exceeds warn threshold: text_len=%s, threshold=%s, "
                "end_user_id=%s (not truncated)",
                len(cleaned),
                self.CONTENT_WARN_THRESHOLD,
                self.end_user_id,
            )

        return cleaned, None

    async def _embed(self, text: str) -> Optional[List[float]]:
        """Step 2 — Embedding。

        - text 为空 → 返回 None。
        - 懒加载 embedder：与正路径 WritePipeline 一致，通过 ModelClientMixin.get_embedding_client
          用 memory_config.embedding_model_id / tenant_id 构造，保证向量空间一致。
        - 完整文本单次 aembed_query（不做前置截断）。
        - 硬上限 EMBED_TIMEOUT_SEC：底层 client 的 max_retries * timeout 可能突破 celery
          任务超时；用 asyncio.wait_for 兜底，超时即视为失败降级。
        - 失败降级：任何异常（含超时）记录 warning 日志（含 text_len）并返回 None，不重试。
        """
        if not text:
            return None

        try:
            if self._embedder is None:
                from app.core.memory.pipelines.base_pipeline import ModelClientMixin
                from app.db import get_db_context

                with get_db_context() as db:
                    self._embedder = ModelClientMixin.get_embedding_client(
                        db,
                        self.memory_config.embedding_model_id,
                        self.memory_config.tenant_id,
                    )

            return await asyncio.wait_for(
                self._embedder.aembed_query(text),
                timeout=self.EMBED_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "[FastWrite] embedding timed out, degrading to None: text_len=%s, "
                "timeout=%ss, end_user_id=%s",
                len(text),
                self.EMBED_TIMEOUT_SEC,
                self.end_user_id,
            )
            return None
        except Exception as e:
            logger.warning(
                "[FastWrite] embedding failed, degrading to None: text_len=%s, "
                "end_user_id=%s, error=%s",
                len(text),
                self.end_user_id,
                e,
            )
            return None

    def _build_dialogue_node(
        self,
        content: str,
        embedding: Optional[List[float]],
        conversation_id: str,
        message_seq: int,
        source: str,
        created_at: "datetime",
    ) -> DialogueNode:
        """Step 3 — 构造 DialogueNode。

        - 确定性 id：build_dialogue_id（会话/无会话两种模板），保证幂等 MERGE。
        - 随机 ref_id / run_id：uuid.uuid4().hex（与正路径一致）。
        - content 为完整原文（不切块、不截断，即使超过告警阈值）。
        - dialog_embedding 可为 None。
        - config_id 取自 memory_config.config_id（UUID → str）。
        - created_at 为消息真实发生时间（dialog_at → dispatch_at → 当前时间降级结果），
          不再使用 worker 执行时刻，保证可回溯。
        - 固定属性：write_mode="fast"、emotion=None。
        """
        dialog_id = build_dialogue_id(
            conversation_id, message_seq, source, self.end_user_id
        )
        return DialogueNode(
            id=dialog_id,
            name=dialog_id,
            ref_id=uuid.uuid4().hex,
            end_user_id=self.end_user_id,
            run_id=uuid.uuid4().hex,
            created_at=created_at,
            content=content,
            dialog_embedding=embedding,
            config_id=str(self.memory_config.config_id),
            write_mode="fast",
            emotion=None,
        )

    async def _persist(self, dialog_node: DialogueNode) -> str:
        """Step 3 — 持久化 DialogueNode 到 Neo4j。

        - 懒加载独立 connector（不共享 driver，由 _cleanup 负责关闭）。
        - 用 add_dialogue_nodes([node], connector)，保证 Cypher / 摊平一致。
        - 死锁重试：与 WritePipeline._is_deadlock 保持一致的宽松判定——异常消息中
          包含 "deadlock"（大小写不敏感）即视为死锁，最多重试
          NEO4J_MERGE_MAX_RETRY 次，退避 0.1*(attempt+1) 秒。
        - 快速失败：非死锁异常、或返回节点数 != 1（RuntimeError），首次即抛出，不重试。
        """
        from app.repositories.neo4j.add_nodes import add_dialogue_nodes

        if self._neo4j is None:
            self._neo4j = Neo4jConnector()

        for attempt in range(self.NEO4J_MERGE_MAX_RETRY):
            try:
                result = await add_dialogue_nodes([dialog_node], self._neo4j)
                if len(result) != 1:
                    raise RuntimeError(
                        f"expected one dialogue UUID, got {len(result)}"
                    )
                logger.info(
                    "[FastWrite] persisted: dialog_id=%s, end_user_id=%s, "
                    "has_embedding=%s, attempt=%s",
                    dialog_node.id,
                    self.end_user_id,
                    dialog_node.dialog_embedding is not None,
                    attempt + 1,
                )
                return result[0]
            except Exception as e:
                # 与 WritePipeline._is_deadlock 一致的宽松判定：异常消息中包含
                # "deadlock"（大小写不敏感）即视为死锁。
                is_deadlock = "deadlock" in str(e).lower()
                has_next_attempt = attempt < self.NEO4J_MERGE_MAX_RETRY - 1
                if not is_deadlock or not has_next_attempt:
                    raise
                logger.warning(
                    "[FastWrite] neo4j deadlock detected, retrying: attempt=%s/%s, "
                    "end_user_id=%s, dialog_id=%s",
                    attempt + 1,
                    self.NEO4J_MERGE_MAX_RETRY,
                    self.end_user_id,
                    dialog_node.id,
                )
                await asyncio.sleep(0.1 * (attempt + 1))

    async def _cleanup(self) -> None:
        """释放懒加载的底层资源（关闭独立的 neo4j connector）。

        关闭失败不影响主流程：记录 warning 后继续；最终清空引用便于 GC。
        """
        if self._neo4j is not None:
            try:
                await self._neo4j.close()
            except Exception as e:
                logger.warning(
                    "[FastWrite] failed to close neo4j connector: end_user_id=%s, error=%s",
                    self.end_user_id,
                    e,
                )
            finally:
                self._neo4j = None
