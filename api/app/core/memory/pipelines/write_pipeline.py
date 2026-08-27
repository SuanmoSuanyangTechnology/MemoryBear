"""
WritePipeline — 记忆写入流水线

编排完整的写入流程：预处理 → 萃取 → 存储 → 聚类 → 摘要。
不包含业务逻辑实现，只做步骤编排和数据传递。

设计原则：
- Pipeline 不直接操作数据库，通过 Engine / Repository 完成
- Pipeline 不包含 LLM 调用逻辑，通过 ExtractionOrchestrator 完成
- Pipeline 负责资源生命周期管理（客户端初始化 / 连接关闭）
- Pipeline 负责错误边界划分（哪些错误中断流程，哪些吞掉继续）

依赖方向：Facade → Pipeline → Engine → Repository（单向，不允许反向调用）
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional

from app.core.memory.exceptions import MemoryExtractionBusinessError
from app.core.memory.utils.log.bear_logger import BearLogger

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

if TYPE_CHECKING:
    from app.core.memory.models.message_models import DialogData
    from app.schemas.memory_config_schema import MemoryConfig

from app.core.memory.models.graph_models import (
    ChunkNode,
    DialogueNode,
    EntityEntityEdge,
    ExtractedEntityNode,
    PerceptualEdge,
    PerceptualNode,
    StatementChunkEdge,
    StatementEntityEdge,
    StatementNode,
)
from app.core.memory.storage_services.extraction_engine.knowledge_extraction.memory_summary import (
    memory_summary_generation,
)
from app.core.memory.utils.name_similarity_utils import cosine_similarity
from app.repositories.neo4j.add_nodes import add_memory_summary_nodes
from app.repositories.neo4j.add_edges import add_memory_summary_statement_edges

logger = logging.getLogger(__name__)
bear = BearLogger("memory.pipeline")


# ──────────────────────────────────────────────
# 数据结构
# ──────────────────────────────────────────────


def _convert_pruning_records(raw_records: list, end_user_id: str = "", source: str = "") -> List[dict]:
    """将剪枝记录转换为 graph_build_step 期望的格式。

    pruning_service.prune_messages() 产生的记录格式（用于快照/日志）：
      {
        "conversation_id": str,
        "message_seq": int,
        "source": "cache"|"llm",
        "input": {"msgs": [{"role": "User"/"Assistant", "msg": str}, ...]},
        "output"|"gold": {
            "assistant_memory_hint": str,
            "assistant_memory_type": str,
            ...
        },
      }

    graph_build_step 期望的格式（= AssistantPruningRecord.model_dump()）：
      {
        "pair_id": str,
        "original_text": str,
        "pruned_text": str,   # "NULL" 表示无记忆价值
        "memory_type": str,
        "created_at": str,
      }

    Args:
        raw_records: 原始剪枝记录列表
        end_user_id: 终端用户 ID，用于 API/MCP 路径生成确定性 pair_id
        source: 写入来源（service_api/mcp），用于 API/MCP 路径生成确定性 pair_id
    """
    from datetime import timezone as _tz

    result: List[dict] = []
    _now = datetime.now(_tz.utc).isoformat()
    for idx, r in enumerate(raw_records):
        # 只处理 assistant_pruning 类型的记录，user_pruning 不产生 AssistantOriginal 节点
        if r.get("type") != "assistant_pruning":
            continue

        conv_id = r.get("conversation_id", "")
        seq = r.get("message_seq", 0)
        # pair_id 基于 conversation_id + message_seq 确定性生成，
        # graph_build_step 用它构造 Neo4j 节点 ID（orig_{pair_id} / pruned_{pair_id}），确保 MERGE 幂等。
        # API/MCP 路径 conversation_id 为空，使用 (end_user_id, source, seq) 保证确定性。
        if conv_id and seq:
            pair_id = f"{conv_id}_{seq}"
        elif end_user_id and source and seq:
            pair_id = f"{end_user_id}_{source}_{seq}"
        else:
            pair_id = f"orphan_{uuid.uuid4().hex[:8]}_{idx}"

        # Assistant 消息是 input.msgs 中最后一条
        msgs = r.get("input", {}).get("msgs", [])
        assistant_msg = msgs[-1] if msgs else {}
        original_text = assistant_msg.get("msg", "")

        # 剪枝结果在 "output"（cache）或 "gold"（llm）键下
        prune_result = r.get("output") or r.get("gold") or {}
        pruned_text = prune_result.get("assistant_memory_hint", "")
        memory_type = prune_result.get("assistant_memory_type", "NULL")

        result.append({
            "pair_id": pair_id,
            "original_text": original_text,
            "pruned_text": pruned_text or "NULL",
            "memory_type": memory_type or "NULL",
            "created_at": _now,
        })

    return result



class ExtractionResult(BaseModel):
    """萃取 + 图构建 + 去重消歧后的结构化输出。

    作为 Pipeline 层的阶段间数据载体，确保下游步骤（_store、_cluster）
    接收到的图节点和边结构完整、类型正确。

    字段对应 ExtractionOrchestrator 产出的图节点/边：
      dialogue_nodes      — 对话节点
      chunk_nodes         — 分块节点
      statement_nodes     — 陈述句节点
      entity_nodes        — 实体节点（去重消歧后）
      perceptual_nodes    — 感知节点
      stmt_chunk_edges    — 陈述句 → 分块 边
      stmt_entity_edges   — 陈述句 → 实体 边
      entity_entity_edges — 实体 → 实体 边（去重消歧后）
      perceptual_edges    — 感知 → 分块 边
      dialog_data_list    — 原始 DialogData（供摘要阶段使用）
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    dialogue_nodes: List[DialogueNode]
    chunk_nodes: List[ChunkNode]
    statement_nodes: List[StatementNode]
    entity_nodes: List[ExtractedEntityNode]
    perceptual_nodes: List[PerceptualNode]
    stmt_chunk_edges: List[StatementChunkEdge]
    stmt_entity_edges: List[StatementEntityEdge]
    entity_entity_edges: List[EntityEntityEdge]
    perceptual_edges: List[PerceptualEdge]
    assistant_original_nodes: List[Any] = Field(default_factory=list)
    assistant_pruned_nodes: List[Any] = Field(default_factory=list)
    assistant_pruned_edges: List[Any] = Field(default_factory=list)
    conversation_nodes: List[Any] = Field(default_factory=list)
    assistant_conversation_edges: List[Any] = Field(default_factory=list)
    user_source_nodes: List[Any] = Field(default_factory=list)
    user_source_edges: List[Any] = Field(default_factory=list)
    dialog_data_list: List[Any] = Field(
        default_factory=list,
        description="原始 DialogData 列表，类型为 Any 以避免循环依赖",
    )

    @property
    def stats(self) -> Dict[str, int]:
        """返回统计摘要，用于 WriteResult 和日志"""
        return {
            "dialogue_count": len(self.dialogue_nodes),
            "chunk_count": len(self.chunk_nodes),
            "statement_count": len(self.statement_nodes),
            "entity_count": len(self.entity_nodes),
            "perceptual_count": len(self.perceptual_nodes),
            "relation_count": len(self.entity_entity_edges),
        }


class WriteResult(BaseModel):
    """写入流水线的最终输出，返回给 MemoryService / MemoryAgentService"""

    status: str  # "success" | "pilot_complete" | "failed"
    extraction: Optional[Dict[str, int]] = None  # ExtractionResult.stats
    error: Optional[str] = None  # 失败时的错误信息
    elapsed_seconds: float = 0.0  # 总耗时（秒）
    _degraded_error: MemoryExtractionBusinessError | None = PrivateAttr(default=None)

    @property
    def degraded_error(self) -> MemoryExtractionBusinessError | None:
        return self._degraded_error


# ──────────────────────────────────────────────
# WritePipeline
# ──────────────────────────────────────────────


class WritePipeline:
    """
    记忆写入流水线

    编排完整的写入流程：预处理 → 萃取 → 存储 → 聚类 → 摘要。
    """

    def __init__(
        self,
        memory_config: MemoryConfig,
        end_user_id: str,
        language: str = "zh",
        progress_callback: Optional[
            Callable[[str, str, Optional[Dict[str, Any]]], Awaitable[None]]
        ] = None,
    ):
        """
        Args:
            memory_config: 不可变的记忆配置对象（从数据库加载）
            end_user_id: 终端用户 ID
            language: 语言 ("zh" | "en")
            progress_callback: 可选的进度回调，签名 (stage, message, data?) -> Awaitable[None] 供pilot run使用
        """
        self.memory_config = memory_config
        self.end_user_id = end_user_id
        self.language = language
        self.progress_callback = progress_callback

        # 延迟初始化的客户端
        self._llm_client = None
        self._embedder_client = None
        self._neo4j_connector = None

        # 存储阶段锁（延迟初始化）
        self._store_lock = None
        self._redis_client = None
        self._embedding_degraded_error: MemoryExtractionBusinessError | None = None

    # ──────────────────────────────────────────────
    # 执行写入
    # ──────────────────────────────────────────────

    async def run(
        self,
        target_message: dict,
        context_before: List[dict] = None,
        context_after: List[dict] = None,
        conversation_id: str = "",
        message_seq: int = 0,
        ref_id: str = "",
        is_pilot_run: bool = False,
        skip_cursor_advance: bool = False,
        dispatch_at: str = "",
        source: str = "",
    ) -> WriteResult:
        """
        执行写入流水线（滑动窗口模式）。

        统一入口，处理单条 target_message 及其上下文窗口。

        Args:
            target_message: 目标消息 {"role": "user", "content": "...", "dialog_at": "..."}
            context_before: 上文消息列表（按 message_seq 升序）
            context_after: 下文消息列表（按 message_seq 升序）
            conversation_id: 对话 ID
            message_seq: 目标消息的 message_seq
            ref_id: 引用 ID，为空则自动生成
            is_pilot_run: 试运行模式（只萃取不写入）
            skip_cursor_advance: 直接写入路径设为 True，跳过 write_cursor 推进
            dispatch_at: 任务派发时刻的 UTC ISO 8601 时间戳
            source: 写入来源（agent/service_api/mcp/workflow），用于快照路径和节点 ID 生成

        Returns:
            WriteResult 包含状态和统计信息
        """
        if context_before is None:
            context_before = []
        if context_after is None:
            context_after = []
        self._embedding_degraded_error = None

        if not ref_id:
            ref_id = uuid.uuid4().hex

        # 根据用户消息内容自动检测语言，确保输出语言与输入语言一致
        import re
        import langid
        # 从目标消息和上下文中提取 user 内容进行语言检测
        all_messages = context_before + [target_message] + context_after
        user_content = " ".join(
            re.sub(r"<input-file-summary>.*?</input-file-summary>", "", msg.get("content", ""), flags=re.DOTALL).strip()
            for msg in all_messages
            if isinstance(msg, dict) and msg.get("role") == "user" and msg.get("content")
        )
        if user_content:
            detected = langid.classify(user_content)[0]
            self.language = detected if detected in ("zh", "en") else "en"
            logger.info(f"[LanguageDetect] detected={detected}, language={self.language}, text_len={len(user_content)}")

        # 处理 dialog_at：三级降级，最终由 ensure_dialog_at 用服务端当前时间兜底
        from app.core.utils.datetime_utils import ensure_dialog_at
        _dialog_at = ensure_dialog_at(
            target_message.get("dialog_at")
            or dispatch_at
            or target_message.get("created_at")
        )
        target_message = {**target_message, "dialog_at": _dialog_at}

        mode = "试运行" if is_pilot_run else "滑动窗口"
        extraction_result = None

        try:
            async with bear.pipeline(
                "WritePipeline",
                mode=mode,
                config_name=self.memory_config.config_name,
                end_user_id=self.end_user_id,
                conversation_id=conversation_id,
                message_seq=message_seq,
            ):
                # 初始化客户端和连接
                self._init_clients()
                self._init_neo4j_connector()

                # 初始化快照记录器
                from app.core.memory.utils.debug.write_snapshot_recorder import (
                    WriteSnapshotRecorder,
                )

                _target_content = target_message.get("content", "") or ""
                _preview = _target_content[:200]
                self._recorder = WriteSnapshotRecorder(
                    end_user_id=self.end_user_id,
                    conversation_id=conversation_id,
                    message_seq=message_seq,
                    source=source or None,
                    extra_metadata={
                        "mode": "sliding_window",
                        "ref_id": ref_id,
                        "source": source,
                        "dispatch_at": dispatch_at,
                        "dialog_at": _dialog_at,
                        "language": self.language,
                        "target_content_preview": _preview,
                        "target_content_length": len(_target_content),
                        "context_before_count": len(context_before),
                        "context_after_count": len(context_after),
                    },
                )

                # Step 1: 剪枝 - 统一剪枝所有消息
                async with bear.step(1, 6, "剪枝", "统一剪枝") as s:
                    # 合并所有消息，记录 target 位置
                    target_idx = len(context_before)
                    all_messages = context_before + [target_message] + context_after

                    # 文件预处理：注入 file-summary（剪枝判断依赖此标签）
                    # 三次预处理操作不同 msg dict 对象，各自独立开启 DB session，并行安全
                    await asyncio.gather(
                        self._preprocess_files([all_messages[target_idx]]),
                        self._preprocess_files(context_before),
                        self._preprocess_files(context_after),
                    )

                    # 统一剪枝
                    from app.core.memory.storage_services.extraction_engine.data_preprocessing.pruning_service import prune_messages
                    all_pruned, pruning_records = await prune_messages(
                        messages=all_messages,
                        memory_config=self.memory_config,
                        end_user_id=self.end_user_id,
                        conversation_id=conversation_id,
                        source=source,
                        language=self.language,
                        persist=not is_pilot_run,
                        llm_client=self._llm_client,  # 复用 Pipeline 已创建的客户端
                    )

                    # 拆回三部分
                    context_before_pruned = all_pruned[:target_idx]
                    messages = [all_pruned[target_idx]]
                    context_after_pruned = all_pruned[target_idx + 1:]

                    s.metadata(
                        before_count=len(context_before_pruned),
                        after_count=len(context_after_pruned),
                        pruned_count=len(pruning_records),
                    )

                # 记录剪枝快照
                recorder = getattr(self, "_recorder", None)
                if recorder is not None and self.memory_config.pruning_enabled:
                    recorder.record_pruning_results(pruning_records)

                # 从 pruning_records 提取 target user 规整信息（用于后续构建 UserSource 节点）
                # 如果 user 内容有被剪枝，保存原文作为 UserSource 节点（类似 dialog 节点），
                # 可以直接取 dialog 节点的内容作为 UserSource 节点的内容
                self._user_pruning_info = None
                if pruning_records:
                    # 优先匹配 LLM 剪枝路径（source=llm, should_process_user_msg=True）
                    target_pruning = next(
                        (r for r in pruning_records
                         if r.get("message_seq") == message_seq
                         and r.get("type") == "user_pruning"
                         and r.get("source") == "llm"
                         and (r.get("output") or {}).get("should_process_user_msg")),
                        None,
                    )
                    if target_pruning:
                        _orig_text = target_pruning["input"]["msgs"][0]["msg"]
                        _output = target_pruning["output"]
                        self._user_pruning_info = {
                            "original_text": _orig_text,
                            "pruned_text": _output.get("processed_user_msg", ""),
                            "message_seq": message_seq,
                            "conversation_id": conversation_id or f"{self.end_user_id}_{source}",
                            "topic_entity_hint": _output.get("processed_user_topic_entity_hint", ""),
                        }
                    else:
                        # 回退：DB 缓存路径（source=db）— 前一次任务已完成 LLM 剪枝并回写 PG
                        target_pruning_db = next(
                            (r for r in pruning_records
                             if r.get("message_seq") == message_seq
                             and r.get("type") == "user_pruning"
                             and r.get("source") == "db"
                             and (r.get("output") or {}).get("topic_entity_hint")),
                            None,
                        )
                        if target_pruning_db:
                            _orig_text = target_pruning_db["input"]["msgs"][0]["msg"]
                            _output = target_pruning_db["output"]
                            self._user_pruning_info = {
                                "original_text": _orig_text,
                                "pruned_text": _output.get("pruned_content", ""),
                                "message_seq": message_seq,
                                "conversation_id": conversation_id or f"{self.end_user_id}_{source}",
                                "topic_entity_hint": _output.get("topic_entity_hint", ""),
                            }

                # Step 2: 预处理 - 消息分块
                async with bear.step(2, 6, "预处理", "消息分块") as s:
                    from app.core.memory.agent.utils.get_dialogs import get_chunked_dialogs
                    recorder = getattr(self, "_recorder", None)
                    snapshot = recorder.snapshot if recorder else None
                    chunked_dialogs = await get_chunked_dialogs(
                        chunker_strategy=self.memory_config.chunker_strategy,
                        end_user_id=self.end_user_id,
                        messages=messages,
                        ref_id=ref_id,
                        config_id=str(self.memory_config.config_id),
                        workspace_id=self.memory_config.workspace_id,
                        snapshot=snapshot,
                        context_before=context_before_pruned,
                        context_after=context_after_pruned,
                        # 传入分组信息，让 target dialog 出生即用与快写统一的确定性 id
                        # （api/mcp 路径 conversation_id 为空、走 source 分支），实现覆盖升级。
                        conversation_id=conversation_id,
                        message_seq=message_seq,
                        source=source,
                    )
                    # 注入剪枝记录到 dialog_data.metadata
                    if pruning_records:
                        converted = _convert_pruning_records(
                            pruning_records,
                            end_user_id=self.end_user_id,
                            source=source,
                        )
                        for dd in chunked_dialogs:
                            existing = dd.metadata.get("assistant_pruning_records", [])
                            dd.metadata["assistant_pruning_records"] = existing + converted

                    # 注入 conversation_id 到 metadata
                    # API/MCP 路径无 conversation_id，用 (end_user_id, source) 作为确定性 hub ID，
                    # 确保同一用户同一来源的所有 assistant 节点聚合到同一个 Conversation hub。
                    if conversation_id:
                        _conv_id = conversation_id
                    elif source:
                        _conv_id = f"{self.end_user_id}_{source}"
                    else:
                        _conv_id = f"batch_{self.end_user_id}_{ref_id}"
                    for dd in chunked_dialogs:
                        dd.metadata["conversation_id"] = _conv_id

                    s.metadata(chunks=sum(len(d.chunks) for d in chunked_dialogs))

                # Step 3: 萃取 + 摘要 LLM 生成并行启动
                summary_gen_task = None
                if not is_pilot_run:
                    summary_gen_task = asyncio.create_task(memory_summary_generation(
                        chunked_dialogs,
                        llm_client=self._llm_client,
                        embedder_client=self._embedder_client,
                        language=self.language,
                    ))

                try:
                    async with bear.step(3, 6, "萃取", "知识提取") as s:
                        extraction_result = await self._extract(chunked_dialogs, is_pilot_run)
                        stats = extraction_result.stats
                        s.metadata(
                            entities=stats["entity_count"],
                            statements=stats["statement_count"],
                            relations=stats["relation_count"],
                        )

                    # 试运行到此结束
                    if is_pilot_run:
                        return WriteResult(
                            status="pilot_complete",
                            extraction=extraction_result.stats,
                            elapsed_seconds=0.0,
                        )

                    # Step 3.5: 构建 UserSource 子图
                    if self._user_pruning_info:
                        await self._build_user_source_subgraph(extraction_result)

                    # Step 4: Store + Stats 并行（Neo4j + Redis 互不依赖）
                    async with bear.step(4, 6, "存储", "写入 Neo4j + 统计缓存"):
                        results = await asyncio.gather(
                            self._store(extraction_result),
                            self._update_stats_cache(extraction_result),
                            return_exceptions=True,
                        )
                        store_result, stats_result = results[0], results[1]

                        # 若 _store 抛异常，清理可能已写入的 Redis 统计缓存后重新抛出
                        if isinstance(store_result, Exception):
                            if not isinstance(stats_result, Exception):
                                await self._rollback_stats_cache()
                            raise store_result

                        stored = store_result

                        # 若 _update_stats_cache 失败，仅记录警告（统计为次级数据）
                        if isinstance(stats_result, Exception):
                            logger.warning(
                                f"[Stats] Redis 统计缓存写入异常（不影响主流程）: {stats_result}"
                            )

                    # Neo4j 事务成功后，触发引擎展示 PG 写入
                    if stored:
                        try:
                            from app.services.memory_engine_display_service import (
                                MemoryEngineDisplayService,
                            )
                            await MemoryEngineDisplayService.save_events(
                                end_user_id=self.end_user_id,
                                extraction_result=extraction_result,
                            )
                        except Exception as e:
                            logger.warning(
                                f"[EngineDisplay] 引擎展示 PG 写入异常（不影响主流程）: {e}",
                                exc_info=True,
                            )

                    # Step 5: 摘要写入（依赖 Step 4 写入的 CONTAINS 边）
                    async with bear.step(5, 6, "摘要", "写入情景记忆") as s:
                        try:
                            summaries = await summary_gen_task
                            await add_memory_summary_nodes(summaries, self._neo4j_connector)
                            await add_memory_summary_statement_edges(summaries, self._neo4j_connector)
                            s.metadata(summary_count=len(summaries))
                            # 摘要成功写入 Neo4j 后，同步保存为 PG 展示记录
                            if summaries:
                                try:
                                    from app.services.memory_display_record_service import (
                                        MemoryDisplayRecordService,
                                    )
                                    await MemoryDisplayRecordService.save_written(
                                        summaries=summaries,
                                        end_user_id=self.end_user_id,
                                    )
                                except Exception as e:
                                    logger.warning(
                                        f"[MemoryDisplayRecord] PG 展示记录写入异常（不影响主流程）: {e}",
                                        exc_info=True,
                                    )
                        except Exception as e:
                            logger.error(f"Memory summary step failed: {e}", exc_info=True)

                    # Step 6: 聚类（Celery 异步，不阻塞）
                    async with bear.step(6, 6, "聚类", "增量更新社区") as s:
                        await self._cluster(extraction_result)
                        s.metadata(mode="async")

                    result = WriteResult(
                        status="success",
                        extraction=extraction_result.stats,
                        elapsed_seconds=0.0,
                    )
                    if stored:
                        result._degraded_error = self._embedding_degraded_error
                    return result

                finally:
                    if summary_gen_task is not None and not summary_gen_task.done():
                        summary_gen_task.cancel()
                        try:
                            await summary_gen_task
                        except (asyncio.CancelledError, Exception):
                            pass

        except Exception:
            raise

        finally:
            await self._cleanup()

    # ──────────────────────────────────────────────
    # Step 1: 预处理
    # ──────────────────────────────────────────────

    async def _preprocess(self, messages: List[dict], ref_id: str) -> List[DialogData]:
        """
        预处理：消息校验 → 对话分块。

        委托给 get_chunked_dialogs()，保持现有预处理逻辑不变。
        get_dialogs.py 内部已包含：
          - 消息格式校验（role/content 必填）
          - DialogueChunker 分块
        """
        from app.core.memory.agent.utils.get_dialogs import get_chunked_dialogs

        recorder = getattr(self, "_recorder", None)
        snapshot = recorder.snapshot if recorder else None

        return await get_chunked_dialogs(
            chunker_strategy=self.memory_config.chunker_strategy,
            end_user_id=self.end_user_id,
            messages=messages,
            ref_id=ref_id,
            config_id=str(self.memory_config.config_id),
            workspace_id=self.memory_config.workspace_id,
            snapshot=snapshot,
        )

    # ──────────────────────────────────────────────
    # Step 2: 萃取
    # ──────────────────────────────────────────────

    async def _extract(
        self,
        chunked_dialogs: List[DialogData],
        is_pilot_run: bool,
    ) -> ExtractionResult:
        """
        萃取：初始化引擎 → 执行知识提取 → 构建图节点/边 → 去重 → 返回结构化结果。

        使用 NewExtractionOrchestrator（ExtractionStep 范式）完成 LLM 萃取，
        然后通过独立的 graph_build_step 和 dedup_step 完成图构建和去重，
        不依赖旧编排器 ExtractionOrchestrator。

        执行流程：
        1. NewExtractionOrchestrator.run() → 萃取并赋值到 DialogData
        2. build_graph_nodes_and_edges() → 从 DialogData 构建图节点和边
        3. run_dedup() → 两阶段去重消歧
        """
        from app.core.memory.storage_services.extraction_engine.steps.dedup_step import (
            run_dedup,
        )
        from app.core.memory.storage_services.extraction_engine.steps.graph_build_step import (
            build_graph_nodes_and_edges,
        )
        from app.core.memory.storage_services.extraction_engine.extraction_pipeline_orchestrator import (
            NewExtractionOrchestrator,
        )

        from app.core.memory.utils.config.config_utils import get_pipeline_config
        from app.core.memory.utils.debug.write_snapshot_recorder import (
            WriteSnapshotRecorder,
        )

        pipeline_config = get_pipeline_config(self.memory_config)
        ontology_types = self._load_ontology_types()

        # 复用 run() 中已创建的 recorder（剪枝阶段已使用同一实例）
        recorder = getattr(self, "_recorder", None) or WriteSnapshotRecorder(self.end_user_id)
        self._recorder = recorder

        # ── 新编排器：LLM 萃取 + 数据赋值 ──
        new_orchestrator = NewExtractionOrchestrator(
            llm_client=self._llm_client,
            embedder_client=self._embedder_client,
            config=pipeline_config,
            embedding_id=str(self.memory_config.embedding_model_id),
            ontology_types=ontology_types,
            language=self.language,
            is_pilot_run=is_pilot_run,
            progress_callback=self.progress_callback,
        )
        # step1: 执行知识提取
        dialog_data_list = await new_orchestrator.run(chunked_dialogs)
        self._embedding_degraded_error = new_orchestrator.embedding_step.degraded_error

        # ── Snapshot: 各阶段萃取结果 ──
        recorder.record_stage_outputs(new_orchestrator.last_stage_outputs)

        # step2: 构建图节点和边
        graph = await build_graph_nodes_and_edges(
            dialog_data_list=dialog_data_list,
            embedder_client=self._embedder_client,
            progress_callback=self.progress_callback,
        )

        # Snapshot: 图节点和边（去重前）
        recorder.record_graph_before_dedup(graph)

        # step3: 第一层去重消歧（同一轮对话内的实体碎片合并）
        # 仅按 (name, entity_type) 精确匹配，更精细的去重留给反思阶段
        dedup_result = await run_dedup(
            entity_nodes=graph.entity_nodes,
            statement_entity_edges=graph.stmt_entity_edges,
            entity_entity_edges=graph.entity_entity_edges,
            dialog_data_list=dialog_data_list,
            pipeline_config=pipeline_config,
            connector=None,
            llm_client=self._llm_client,
            is_pilot_run=True,
            progress_callback=self.progress_callback,
        )

        # Snapshot: 去重后
        recorder.record_dedup_result(dedup_result)

        # step4: 构造最终结果
        result = ExtractionResult(
            dialogue_nodes=graph.dialogue_nodes,
            chunk_nodes=graph.chunk_nodes,
            statement_nodes=graph.statement_nodes,
            entity_nodes=dedup_result.entity_nodes,
            perceptual_nodes=graph.perceptual_nodes,
            stmt_chunk_edges=graph.stmt_chunk_edges,
            stmt_entity_edges=dedup_result.statement_entity_edges,
            entity_entity_edges=dedup_result.entity_entity_edges,
            perceptual_edges=graph.perceptual_edges,
            assistant_original_nodes=graph.assistant_original_nodes,
            assistant_pruned_nodes=graph.assistant_pruned_nodes,
            assistant_pruned_edges=graph.assistant_pruned_edges,
            conversation_nodes=graph.conversation_nodes,
            assistant_conversation_edges=graph.assistant_conversation_edges,
            dialog_data_list=dialog_data_list,
        )

        recorder.record_summary(result.stats)
        return result

    # ──────────────────────────────────────────────
    # Step 3: 存储
    # ──────────────────────────────────────────────

    async def _store(self, result: ExtractionResult) -> bool:
        """存储：永久容量分配 → 别名清洗 → Neo4j 写入（含死锁重试）。"""
        from app.repositories.neo4j.cypher_queries import PERMANENT_MEMORY_COUNT
        from app.repositories.neo4j.graph_saver import (
            save_dialog_and_statements_to_neo4j,
        )
        from app.services.memory_value_ranking_service import (
            assign_permanent_memory_slots,
            disable_permanent_candidates,
        )

        await self._clean_cross_role_aliases(result.entity_nodes)

        permanent_candidates = [
            node
            for node in result.statement_nodes
            if bool(getattr(node, "is_permanent", False))
        ]
        if permanent_candidates:
            candidate_count = len(permanent_candidates)
            try:
                count_rows = await self._neo4j_connector.execute_query(
                    PERMANENT_MEMORY_COUNT,
                    end_user_id=self.end_user_id,
                )
                used = int(count_rows[0]["used"]) if count_rows else 0
                assigned = assign_permanent_memory_slots(
                    self.end_user_id,
                    result.statement_nodes,
                    used=used,
                )
                logger.info(
                    "Permanent-memory slots assigned in serialized write: "
                    "end_user_id=%s assigned=%d candidates=%d degraded=%d",
                    self.end_user_id,
                    assigned,
                    candidate_count,
                    candidate_count - assigned,
                )
            except Exception as exc:
                disable_permanent_candidates(result.statement_nodes)
                logger.error(
                    "Permanent-memory allocation failed; candidates degraded to normal memory: "
                    "end_user_id=%s error=%s",
                    self.end_user_id,
                    exc,
                    exc_info=True,
                )

        max_retries = 3
        for attempt in range(max_retries):
            try:
                success = await save_dialog_and_statements_to_neo4j(
                    dialogue_nodes=result.dialogue_nodes,
                    chunk_nodes=result.chunk_nodes,
                    statement_nodes=result.statement_nodes,
                    entity_nodes=result.entity_nodes,
                    perceptual_nodes=result.perceptual_nodes,
                    statement_chunk_edges=result.stmt_chunk_edges,
                    statement_entity_edges=result.stmt_entity_edges,
                    entity_edges=result.entity_entity_edges,
                    perceptual_edges=result.perceptual_edges,
                    connector=self._neo4j_connector,
                    assistant_original_nodes=result.assistant_original_nodes,
                    assistant_pruned_nodes=result.assistant_pruned_nodes,
                    assistant_pruned_edges=result.assistant_pruned_edges,
                    conversation_nodes=result.conversation_nodes,
                    assistant_conversation_edges=result.assistant_conversation_edges,
                    user_source_nodes=result.user_source_nodes,
                    user_source_edges=result.user_source_edges,
                )
                if success:
                    logger.debug("Successfully saved all data to Neo4j")
                    return True
                # 写入返回 False（部分失败）
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Neo4j 写入部分失败，重试 ({attempt + 2}/{max_retries})"
                    )
                    await asyncio.sleep(1 * (attempt + 1))
                else:
                    logger.error(f"Neo4j 写入在 {max_retries} 次尝试后仍部分失败")
                    return False
            except Exception as e:
                if self._is_deadlock(e) and attempt < max_retries - 1:
                    logger.warning(f"Neo4j 死锁，重试 ({attempt + 2}/{max_retries})")
                    await asyncio.sleep(1 * (attempt + 1))
                else:
                    raise
        return False

    # ──────────────────────────────────────────────
    # Step 3.5: 构建 UserSource 子图
    # ──────────────────────────────────────────────

    # 通用角色实体排除列表，这些实体不应与 UserSource 建边
    _USER_SOURCE_EXCLUDED_ENTITY_NAMES = frozenset({
        "用户", "我", "user", "i", "ai助手", "助手", "助理", "ai", "assistant", "ai回复", "ai assistant",
    })

    # UserSource → Entity 建边的余弦相似度阈值
    _USER_SOURCE_SIMILARITY_THRESHOLD = 0.5

    async def _build_user_source_subgraph(self, result: ExtractionResult) -> None:
        """构建 UserSource 节点和 HAS_ORIGINAL_CONTENT 边。

        当 user 消息被剪枝/规整后，创建 UserSource 节点保存原文（类似 dialog 节点），
        并通过 HAS_ORIGINAL_CONTENT 边连接到**语义相关的** Entity 节点，
        为检索侧提供 "Entity → 原文" 的回溯路径。

        连边逻辑：
        1. 预先排除通用角色实体（"用户""AI助手"等）
        2. 用 UserSourceNode.name（= processed_user_topic_entity_hint）的 embedding
           与每个 ExtractedEntity.name_embedding 做余弦相似度匹配
        3. 只对超过阈值的 entity 建 HAS_ORIGINAL_CONTENT 边

        节点 name 属性使用 LLM 产出的 processed_user_topic_entity_hint（实体锚点）。
        原文来源：直接取 dialog 节点的内容（memory_messages.content）。

        前置条件：self._user_pruning_info 已在 Step 1 后设置。
        """
        from app.core.memory.models.graph_models import (
            UserSourceNode,
            UserSourceEntityEdge,
        )

        info = self._user_pruning_info
        if not info:
            return

        # 生成 original_text 的 embedding（存入节点，供向量检索回溯）
        text_embedding = None
        if self._embedder_client and info["original_text"]:
            try:
                text_embedding = await self._embedder_client.aembed_query(info["original_text"])
            except Exception as e:
                logger.warning(f"[UserSource] 生成 text_embedding 失败（不影响写入）: {e}")

        # 构建节点
        node_id = uuid.uuid4().hex
        from app.core.utils.datetime_utils import to_iso_z, utcnow
        now_iso = to_iso_z(utcnow())
        run_id = result.dialogue_nodes[0].run_id if result.dialogue_nodes else uuid.uuid4().hex

        # name 使用 LLM 产出的实体锚点（processed_user_topic_entity_hint）
        node_name = info.get("topic_entity_hint") or f"UserSource_{node_id[:8]}"

        us_node = UserSourceNode(
            id=node_id,
            name=node_name,
            end_user_id=self.end_user_id,
            run_id=run_id,
            created_at=now_iso,
            message_seq=info["message_seq"],
            original_text=info["original_text"],
            pruned_text=info["pruned_text"],
            text_embedding=text_embedding,
        )

        # 选择性建边：排除角色实体 + embedding 相似度匹配
        matched = await self._select_entities_for_user_source(node_name, result.entity_nodes)

        # 为匹配的 entity 创建 HAS_ORIGINAL_CONTENT 边
        edges = [
            UserSourceEntityEdge(
                id=uuid.uuid4().hex,
                source=node_id,
                target=entity.id,
                end_user_id=self.end_user_id,
                run_id=run_id,
                created_at=now_iso,
            )
            for entity in matched
        ]

        result.user_source_nodes = [us_node]
        result.user_source_edges = edges

        logger.info(
            f"[UserSource] 构建完成 - node_id={node_id[:8]}, name={node_name}, "
            f"total_entities={len(result.entity_nodes)}, matched={len(matched)}, "
            f"original_len={len(info['original_text'])}"
        )

    async def _select_entities_for_user_source(
        self, hint_name: str, entities: list
    ) -> list:
        """筛选与 UserSource 语义相关的 entity（排除角色实体 + embedding 匹配）。

        Args:
            hint_name: UserSourceNode.name（= processed_user_topic_entity_hint）
            entities: 本次萃取出的 ExtractedEntityNode 列表

        Returns:
            通过过滤和相似度匹配的 entity 子集
        """
        # 生成 hint_name 的 embedding
        hint_emb = None
        if self._embedder_client and hint_name:
            try:
                hint_emb = await self._embedder_client.aembed_query(hint_name)
            except Exception as e:
                logger.warning(f"[UserSource] hint embedding 失败，降级全连: {e}")

        excluded = self._USER_SOURCE_EXCLUDED_ENTITY_NAMES
        threshold = self._USER_SOURCE_SIMILARITY_THRESHOLD

        matched = []
        skipped_role = 0
        skipped_sim = 0

        for entity in entities:
            # 排除通用角色实体
            if entity.name and entity.name.lower().strip() in excluded:
                skipped_role += 1
                continue
            # embedding 相似度匹配
            if hint_emb:
                emb = entity.name_embedding
                if emb and cosine_similarity(hint_emb, emb) < threshold:
                    skipped_sim += 1
                    continue
            matched.append(entity)

        logger.debug(
            f"[UserSource] entity 匹配 - hint={hint_name}, "
            f"skipped_role={skipped_role}, skipped_sim={skipped_sim}, matched={len(matched)}"
        )
        return matched

    # ──────────────────────────────────────────────
    # Step 4: 聚类
    # ──────────────────────────────────────────────

    async def _cluster(self, result: ExtractionResult) -> None:
        """
        聚类：提交 Celery 异步任务进行增量社区更新。

        聚类不阻塞主写入流程，失败不影响写入结果。
        通过 Celery 异步执行，由 LabelPropagationEngine 完成实际计算。

        注意：ExtractionResult.entity_nodes 已经是经过 _extract() 中
        两阶段去重消歧（_run_dedup_and_write_summary）后的结果，
        聚类直接基于去重后的实体 ID 执行。

        派发方式：直接使用 run_incremental_clustering.apply_async 投递到
        memory_heavy_tasks 队列（不再经 scheduler.push_task）。
        同用户聚类并发导致的 Neo4j 锁竞争，由 P0 批量 UNWIND（事务毫秒级）
        + Neo4j 死锁自动重试两层防御兜底。
        """
        if not result.entity_nodes:
            return

        try:
            from app.tasks import run_incremental_clustering

            new_entity_ids = [e.id for e in result.entity_nodes]
            task = run_incremental_clustering.apply_async(
                kwargs={
                    "end_user_id": self.end_user_id,
                    "new_entity_ids": new_entity_ids,
                    "config_id": str(self.memory_config.config_id), # 跨进程只传入config_id
                    "language": self.language,
                },
                priority=3,
            )
            logger.info(
                f"[Clustering] 增量聚类任务已提交 - "
                f"task_id={task.id}, "
                f"entity_count={len(new_entity_ids)}, "
                f"source=dedup"
            )
        except Exception as e:
            logger.error(
                f"[Clustering] 提交聚类任务失败（不影响主流程）: {e}",
                exc_info=True,
            )

    # ──────────────────────────────────────────────
    # (已内联到 run() 中：摘要 LLM 生成与萃取并行，Neo4j 写入在 _store 之后)
    # ──────────────────────────────────────────────

    # ──────────────────────────────────────────────
    # 文件预处理（与旧路径 memory_agent_service._preprocess_files 一脉相承）
    # ──────────────────────────────────────────────
    async def _preprocess_files(self, messages: List[dict]) -> None:
        """处理消息中附带的文件，生成 Perceptual 记录并注入 summary 到 content。

        与旧路径 memory_agent_service._preprocess_files 逻辑一致：
        1. 遍历消息中的 files 列表
        2. 对每个文件，先查找已有的 Perceptual 记录（通过 URL）
        3. 若不存在，调用 generate_perceptual_memory 创建（调用多模态 LLM 生成 summary）
        4. 将 file_object.summary 以 <input-file-summary> 标签注入到 message["content"]
        5. 将 (file_object, file_type) 挂载到 message["file_content"]

        Args:
            messages: 消息列表，每条消息含 files 字段（List[FileInput dict]）。
                      函数会原地修改 content 和 file_content。
        """
        if not messages:
            return

        from app.db import get_db_context
        from app.repositories.memory_perceptual_repository import MemoryPerceptualRepository
        from app.schemas.app_schema import FileInput
        from app.services.memory_perceptual_service import MemoryPerceptualService, _PerceptualSnapshot

        for msg in messages:
            files = msg.get("files") or []
            if not files:
                msg["file_content"] = []
                continue

            msg["file_content"] = []
            for file_info in files:
                url = file_info.get("url", "")
                if not url:
                    # local_file 场景：memory_messages.files 只带 upload_file_id
                    # （FileInput.model_dump(exclude_none=True) 剔除了空 url），
                    # 必须先解析为可访问 URL，否则整条感知记忆会被跳过。
                    url = await self._resolve_upload_file_url(file_info)
                    if url:
                        file_info = {**file_info, "url": url}
                if not url:
                    logger.warning(
                        "[WritePipeline] 文件缺少可访问 URL，跳过感知记忆: "
                        "type=%s transfer_method=%s upload_file_id=%s",
                        file_info.get("type"),
                        file_info.get("transfer_method"),
                        file_info.get("upload_file_id"),
                    )
                    continue

                try:
                    with get_db_context() as db:
                        # 先查找已有的 Perceptual 记录
                        repo = MemoryPerceptualRepository(db)
                        memories = repo.get_by_url(self.end_user_id, url)

                        if memories:
                            # 已存在，复用最新的一条
                            memory = max(
                                memories,
                                key=lambda m: m.created_time if m.created_time else datetime.min,
                            )
                            # 在 Session 内提取所有需要的属性到一个简单对象中，
                            # 彻底避免 DetachedInstanceError（使用模块级 _PerceptualSnapshot）
                            snap = _PerceptualSnapshot(
                                id=memory.id,
                                end_user_id=self.end_user_id,  # 使用当前 pipeline 的 end_user_id，确保 Perceptual 节点归属当前用户
                                perceptual_type=memory.perceptual_type,
                                file_path=memory.file_path,
                                file_name=memory.file_name,
                                file_ext=memory.file_ext,
                                summary=memory.summary,
                                meta_data=memory.meta_data,
                                created_time=memory.created_time,
                            )
                            file_object = snap
                        else:
                            # 不存在，创建 Perceptual 记录
                            perceptual_service = MemoryPerceptualService(db)
                            # 补充 transfer_method（memory_messages.files 中可能缺失此字段）
                            file_data = dict(file_info)
                            if "transfer_method" not in file_data:
                                file_data["transfer_method"] = "remote_url" if file_data.get("url") else "local_file"
                            file_input = FileInput(**file_data)
                            memory = await perceptual_service.generate_perceptual_memory(
                                end_user_id=self.end_user_id,
                                memory_config=self.memory_config,
                                file=file_input,
                                content=msg.get("content") or None,
                            )
                            # 在 Session 内提取属性到快照，避免 DetachedInstanceError
                            if memory is not None:
                                file_object = _PerceptualSnapshot(
                                    id=memory.id,
                                    end_user_id=self.end_user_id,
                                    perceptual_type=memory.perceptual_type,
                                    file_path=memory.file_path,
                                    file_name=memory.file_name,
                                    file_ext=memory.file_ext,
                                    summary=memory.summary,
                                    meta_data=memory.meta_data,
                                    created_time=memory.created_time,
                                )
                            else:
                                file_object = None

                        if file_object is None:
                            continue

                        # 注入 summary 到 content
                        # 跳过已包含 summary 的情况（API 路径在写入 memory_messages 前已注入）
                        if file_object.summary:
                            summary_tag = f"<input-file-summary>{file_object.summary}</input-file-summary>"
                            current_content = msg.get("content") or ""
                            if summary_tag not in current_content:
                                msg["content"] = current_content + summary_tag

                        msg["file_content"].append((file_object, file_info.get("type", "")))

                except Exception as e:
                    logger.warning(
                        f"[WritePipeline] 文件预处理失败: url={url}, err={e}",
                        exc_info=True,
                    )

    async def _resolve_upload_file_url(self, file_info: dict) -> str:
        """把 local_file 的 upload_file_id 解析为可访问 URL。

        API 写入路径（/v1/memory/write）的 files 只带 upload_file_id，
        与试运行路径 ``pilot_run_service._resolve_file_url`` 保持一致，这里补齐 URL。

        Args:
            file_info: memory_messages.files 中的单个文件 dict

        Returns:
            str: 可访问 URL；非 local_file 或解析失败时返回空串
        """
        upload_file_id = file_info.get("upload_file_id")
        if not upload_file_id:
            return ""

        from app.db import get_db_read
        from app.schemas.app_schema import FileInput
        from app.services.multimodal_service import MultimodalService

        try:
            file_data = {k: v for k, v in file_info.items() if k != "url"}
            file_input = FileInput(**file_data)
            with get_db_read() as db:
                url = await MultimodalService(db, api_config=None).get_file_url(file_input)
            return url or ""
        except Exception as e:
            logger.warning(
                "[WritePipeline] 文件 URL 解析失败: upload_file_id=%s err=%s",
                upload_file_id,
                e,
            )
            return ""

    # ──────────────────────────────────────────────
    # 辅助方法
    # ──────────────────────────────────────────────

    def _init_clients(self) -> None:
        """
        从 MemoryConfig 构建 LLM 和 Embedding 客户端。

        使用 ModelClientMixin 静态方法，需要短暂的 DB session 来
        查询模型配置（API key、base_url 等），查询完毕立即释放。

        幂等：若已初始化则跳过，避免重复 DB 查询。
        """
        from app.core.memory.pipelines.base_pipeline import ModelClientMixin
        from app.db import get_db_context

        with get_db_context() as db: # 考虑写入是否需要增加异步方法，get_async_db_context
            self._llm_client = ModelClientMixin.get_llm_client(
                db, self.memory_config.llm_model_id, self.memory_config.tenant_id
            )
            self._embedder_client = ModelClientMixin.get_embedding_client(
                db, self.memory_config.embedding_model_id, self.memory_config.tenant_id
            )
        logger.info("LLM and embedding clients constructed")

    def _init_neo4j_connector(self) -> None:
        """初始化 Neo4j 连接器（幂等：若已存在则跳过）。"""
        if self._neo4j_connector is not None:
            return

        from app.repositories.neo4j.neo4j_connector import Neo4jConnector
        self._neo4j_connector = Neo4jConnector()

    def _load_ontology_types(self):
        """
        加载本体类型配置。

        如果 memory_config 中配置了 scene_id，则从数据库加载
        该场景关联的本体类型列表，用于指导三元组提取。
        """
        if not self.memory_config.scene_id:
            return None

        try:
            from app.core.memory.ontology_services.ontology_type_loader import (
                load_ontology_types_for_scene,
            )
            from app.db import get_db_context

            with get_db_context() as db:
                ontology_types = load_ontology_types_for_scene(
                    scene_id=self.memory_config.scene_id,
                    workspace_id=self.memory_config.workspace_id,
                    db=db,
                )
            if ontology_types:
                logger.info(
                    f"Loaded {len(ontology_types.types)} ontology types "
                    f"for scene_id: {self.memory_config.scene_id}"
                )
            return ontology_types
        except Exception as e:
            logger.warning(
                f"Failed to load ontology types for scene_id "
                f"{self.memory_config.scene_id}: {e}",
                exc_info=True,
            )
            return None

    async def _clean_cross_role_aliases(
        self, entity_nodes: List[ExtractedEntityNode]
    ) -> None:
        """
        清洗用户/AI助手实体之间的别名交叉污染。

        从 Neo4j 查询已有的 AI 助手别名，与本轮实体中的 AI 助手别名合并，
        确保用户实体的 aliases 不包含 AI 助手的名字。
        失败不中断主流程。
        """
        try:
            from app.core.memory.storage_services.extraction_engine.deduplication.deduped_and_disamb import (
                clean_cross_role_aliases,
                fetch_neo4j_assistant_aliases,
            )

            neo4j_assistant_aliases = set()
            if entity_nodes:
                eu_id = entity_nodes[0].end_user_id
                if eu_id:
                    neo4j_assistant_aliases = await fetch_neo4j_assistant_aliases(
                        self._neo4j_connector, eu_id
                    )
            clean_cross_role_aliases(
                entity_nodes,
                external_assistant_aliases=neo4j_assistant_aliases,
            )
            logger.info(
                f"别名清洗完成，AI助手别名排除集大小: {len(neo4j_assistant_aliases)}"
            )
        except Exception as e:
            logger.warning(f"别名清洗失败（不影响主流程）: {e}")

    @staticmethod
    def _is_deadlock(e: Exception) -> bool:
        """判断异常是否为 Neo4j 死锁错误"""
        msg = str(e).lower()
        return "deadlockdetected" in msg or "deadlock" in msg

    async def _rollback_stats_cache(self) -> None:
        """
        回滚 Redis 活动统计缓存。

        当 _store（Neo4j 写入）失败但 _update_stats_cache 已成功时调用，
        清理本次写入产生的统计快照，避免缓存领先于实际图数据。
        失败不中断异常传播。
        """
        from redis.exceptions import RedisError

        try:
            from app.cache.memory.activity_stats_cache import ActivityStatsCache

            await ActivityStatsCache.delete_activity_stats(
                workspace_id=str(self.memory_config.workspace_id),
            )
            logger.info(
                f"[Stats] Neo4j 写入失败，已回滚 Redis 统计缓存: "
                f"workspace_id={self.memory_config.workspace_id}"
            )
        except (RedisError, OSError) as e:
            # RedisError: Redis 连接/命令层面失败
            # OSError: 网络层面不可达（ConnectionRefusedError 等是 OSError 子类）
            logger.warning(f"[Stats] 回滚统计缓存失败（不影响异常传播）: {e}")

    async def _update_stats_cache(self, result: ExtractionResult) -> None:
        """
        将提取统计写入 Redis 活动缓存，按 workspace_id 存储。
        失败不中断主流程。
        """
        try:
            from app.cache.memory.activity_stats_cache import (
                ActivityStatsCache,
            )

            stats = {
                "chunk_count": result.stats["chunk_count"],
                "statements_count": result.stats["statement_count"],
                "triplet_entities_count": result.stats["entity_count"],
                "triplet_relations_count": result.stats["relation_count"],
                "temporal_count": 0,
            }
            await ActivityStatsCache.set_activity_stats(
                workspace_id=str(self.memory_config.workspace_id),
                stats=stats,
            )
            logger.info(
                f"活动统计已写入 Redis: workspace_id={self.memory_config.workspace_id}"
            )
        except Exception as e:
            logger.warning(f"写入活动统计缓存失败（不影响主流程）: {e}")

    async def _cleanup(self) -> None:
        """
        清理资源：关闭 Neo4j 连接器。

        LLM/Embedding 客户端不在此处清理——它们在 WritePipeline 生命周期内
        跨多次 run_with_window 调用复用，由 GC 最终回收。
        """
        if self._neo4j_connector:
            try:
                await self._neo4j_connector.close()
            except Exception as e:
                logger.error(f"Error closing Neo4j connector: {e}")

        self._neo4j_connector = None
