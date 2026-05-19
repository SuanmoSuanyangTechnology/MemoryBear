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
from datetime import datetime
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional

from app.core.memory.utils.log.bear_logger import BearLogger

from pydantic import BaseModel, Field, ConfigDict

if TYPE_CHECKING:
    from app.core.memory.models.message_models import DialogData
    from app.schemas.memory_config_schema import MemoryConfig
    from app.core.memory.pipelines.pruning_pipeline import PruningPipeline

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

logger = logging.getLogger(__name__)
bear = BearLogger("memory.pipeline")


# ──────────────────────────────────────────────
# 数据结构
# ──────────────────────────────────────────────


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
    assistant_dialog_edges: List[Any] = Field(default_factory=list)
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

    # ──────────────────────────────────────────────
    # 公开接口
    # ──────────────────────────────────────────────

    async def run(
        self,
        messages: List[dict],
        ref_id: str = "",
        is_pilot_run: bool = False,
    ) -> WriteResult:
        """
        执行完整的写入流水线。

        Args:
            messages: 结构化消息 [{"role": "user"/"assistant", "content": "..."}]
            ref_id: 引用 ID，为空则自动生成
            is_pilot_run: 试运行模式（只萃取不写入）

        Returns:
            WriteResult 包含状态和统计信息
        """
        if not ref_id:
            ref_id = uuid.uuid4().hex

        # 根据用户消息内容自动检测语言，确保输出语言与输入语言一致
        import re
        import langid
        user_content = " ".join(
            re.sub(r"<input-file-summary>.*?</input-file-summary>", "", msg.get("content", ""), flags=re.DOTALL).strip()
            for msg in messages
            if isinstance(msg, dict) and msg.get("role") == "user" and msg.get("content")
        )
        if user_content:
            detected = langid.classify(user_content)[0]
            self.language = detected if detected in ("zh", "en") else "en"
            logger.info(f"[LanguageDetect] detected={detected}, language={self.language}, text_len={len(user_content)}")

        mode = "试运行" if is_pilot_run else "正式"
        extraction_result = None

        try:
            async with bear.pipeline(
                "WritePipeline",
                mode=mode,
                config_name=self.memory_config.config_name,
                end_user_id=self.end_user_id,
            ):
                # 初始化客户端和连接
                self._init_clients()
                self._init_neo4j_connector()

                # 初始化快照记录器（提前创建，供预处理阶段的剪枝使用）
                from app.core.memory.utils.debug.write_snapshot_recorder import (
                    WriteSnapshotRecorder,
                )

                self._recorder = WriteSnapshotRecorder("new")

                # Step 1: 预处理 - 消息分块 + AI消息语义剪枝
                async with bear.step(1, 5, "预处理", "消息分块") as s:
                    chunked_dialogs = await self._preprocess(messages, ref_id)
                    s.metadata(chunks=sum(len(d.chunks) for d in chunked_dialogs))

                # Step 2: 萃取 - 知识提取 + 第一层去重 + 别名归并（内存侧）
                async with bear.step(2, 5, "萃取", "知识提取") as s:
                    extraction_result = await self._extract(
                        chunked_dialogs, is_pilot_run
                    )
                    # 别名归并（内存侧）：在写入前完成，确保写入的数据已归并
                    self._merge_alias_in_memory(extraction_result)
                    stats = extraction_result.stats
                    s.metadata(
                        entities=stats["entity_count"],
                        statements=stats["statement_count"],
                        relations=stats["relation_count"],
                    )

                # 试运行模式到此结束
                if is_pilot_run:
                    return WriteResult(
                        status="pilot_complete",
                        extraction=extraction_result.stats,
                        elapsed_seconds=0.0,
                    )

                # Step 3: 存储 - 写入 Neo4j
                async with bear.step(3, 5, "存储", "写入 Neo4j"):
                    await self._store(extraction_result)

                # Step 3.5: 异步后处理（别名归并 Neo4j 侧 + 第二层去重 + 情绪 + 元数据）
                await self._post_store_async_tasks(extraction_result)

                # Step 4: 聚类 - 增量更新社区（异步，不阻塞）
                async with bear.step(4, 5, "聚类", "增量更新社区") as s:
                    await self._cluster(extraction_result)
                    s.metadata(mode="async")

                # Step 5: 摘要 - 生成情景记忆摘要
                async with bear.step(5, 5, "摘要", "生成情景记忆"):
                    await self._summarize(chunked_dialogs)

                # 更新活动统计缓存
                await self._update_stats_cache(extraction_result)

                return WriteResult(
                    status="success",
                    extraction=extraction_result.stats,
                    elapsed_seconds=0.0,
                )

        except Exception:
            raise

        finally:
            await self._cleanup()

    # ──────────────────────────────────────────────
    # Step 1: 预处理
    # ──────────────────────────────────────────────

    async def _preprocess(self, messages: List[dict], ref_id: str) -> List[DialogData]:
        """
        预处理：消息校验 → AI消息语义剪枝 → 对话分块。

        委托给 get_chunked_dialogs()，保持现有预处理逻辑不变。
        get_dialogs.py 内部已包含：
          - 消息格式校验（role/content 必填）
          - AI消息语义剪枝（根据 config 中 pruning_enabled 决定）
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
        recorder = getattr(self, "_recorder", None) or WriteSnapshotRecorder("new")
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

        # 收集需要异步情绪提取的 statements（由编排器在 Phase 4 后收集）
        # 注意：实际 dispatch 在 _store 之后，确保 Statement 节点已写入 Neo4j
        self._emotion_statements = new_orchestrator.emotion_statements

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
        # 第二层（Neo4j 联合去重）后移到 _store 之后异步执行
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
            assistant_dialog_edges=graph.assistant_dialog_edges,
            dialog_data_list=dialog_data_list,
        )

        recorder.record_summary(result.stats)
        return result

    # ──────────────────────────────────────────────
    # Step 3: 存储
    # ──────────────────────────────────────────────

    async def _store(self, result: ExtractionResult) -> None:
        """
        存储：别名清洗 → Neo4j 写入（含死锁重试）。

        错误策略：
        - 别名清洗失败 → 警告日志，继续写入
        - Neo4j 写入死锁 → 指数退避重试 3 次
        - Neo4j 写入非死锁异常 → 直接抛出，中断流程
        """
        from app.repositories.neo4j.graph_saver import (
            save_dialog_and_statements_to_neo4j,
        )

        # 1. 写入前别名清洗（失败不中断）
        await self._clean_cross_role_aliases(result.entity_nodes)

        # 2. Neo4j 写入（含死锁重试）
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
                    assistant_dialog_edges=result.assistant_dialog_edges,
                )
                if success:
                    logger.debug("Successfully saved all data to Neo4j")
                    return
                # 写入返回 False（部分失败）
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Neo4j 写入部分失败，重试 ({attempt + 2}/{max_retries})"
                    )
                    await asyncio.sleep(1 * (attempt + 1))
                else:
                    logger.error(f"Neo4j 写入在 {max_retries} 次尝试后仍部分失败")
            except Exception as e:
                if self._is_deadlock(e) and attempt < max_retries - 1:
                    logger.warning(f"Neo4j 死锁，重试 ({attempt + 2}/{max_retries})")
                    await asyncio.sleep(1 * (attempt + 1))
                else:
                    raise

    # ──────────────────────────────────────────────
    # Step 3.2: 别名归并（内存侧）
    # ──────────────────────────────────────────────

    def _merge_alias_in_memory(self, result: ExtractionResult) -> None:
        """别名归并（内存侧）：处理 predicate="别名属于" 的边。

        在写入 Neo4j 之前执行，确保写入的数据已经完成别名归并：
        - 别名属于：将别名实体的 name 追加到目标实体的 aliases
        - 别名属于：将别名实体的 description 拼接到目标实体的 description
        - 重定向指向别名节点的边到目标节点

        纯内存操作，不涉及 Neo4j。
        """
        ALIAS_PREDICATE = "别名属于"

        alias_edges = [
            e
            for e in result.entity_entity_edges
            if getattr(e, "relation_type", "") == ALIAS_PREDICATE
            or getattr(e, "predicate", "") == ALIAS_PREDICATE
        ]

        if not alias_edges:
            logger.debug("[AliasMerge] 无 '别名属于' 关系，跳过")
            return

        try:
            entity_map = {e.id: e for e in result.entity_nodes}
            alias_to_target: dict[str, str] = {}

            # ── 处理 别名属于：追加 aliases ──
            for edge in alias_edges:
                source_node = entity_map.get(edge.source)
                target_node = entity_map.get(edge.target)
                if not source_node or not target_node:
                    continue

                alias_to_target[edge.source] = edge.target

                # 将 source.name 追加到 target.aliases（去重，忽略大小写）
                source_name = (source_node.name or "").strip()
                if source_name:
                    existing_lower = {a.lower() for a in (target_node.aliases or [])}
                    if source_name.lower() not in existing_lower:
                        target_node.aliases = list(target_node.aliases or []) + [
                            source_name
                        ]

                # 将 source.description 拼接到 target.description（分号分隔，去重）
                src_desc = (source_node.description or "").strip()
                if src_desc:
                    tgt_desc = (target_node.description or "").strip()
                    if src_desc not in tgt_desc:
                        target_node.description = (
                            f"{tgt_desc}；{src_desc}" if tgt_desc else src_desc
                        )

            # 重定向指向别名节点的边到目标节点
            alias_ids = set(alias_to_target.keys())
            redirected_ee_count = 0
            redirected_se_count = 0

            for edge in result.entity_entity_edges:
                rel_type = getattr(edge, "relation_type", "")
                if rel_type == ALIAS_PREDICATE:
                    continue
                if edge.source in alias_ids:
                    edge.source = alias_to_target[edge.source]
                    redirected_ee_count += 1
                if edge.target in alias_ids:
                    edge.target = alias_to_target[edge.target]
                    redirected_ee_count += 1

            for edge in result.stmt_entity_edges:
                if edge.target in alias_ids:
                    edge.target = alias_to_target[edge.target]
                    redirected_se_count += 1

            logger.info(
                f"[AliasMerge] 内存归并完成，处理 {len(alias_edges)} 条 '别名属于' 边，"
                f"重定向 entity_entity 边 {redirected_ee_count} 次，"
                f"重定向 stmt_entity 边 {redirected_se_count} 次"
            )

        except Exception as e:
            logger.warning(
                f"[AliasMerge] 内存归并失败（不影响主流程）: {e}", exc_info=True
            )

    # ──────────────────────────────────────────────
    # Step 3.5: 异步后处理（Neo4j 别名归并 + 第二层去重）
    # ──────────────────────────────────────────────

    async def _post_store_async_tasks(self, result: ExtractionResult) -> None:
        """提交写入后的异步 Celery 任务（全部 fire-and-forget，失败不影响主流程）：

        1. Neo4j 别名归并 + 第二层去重
        2. 异步情绪提取
        3. 异步元数据提取
        """
        from app.core.memory.storage_services.extraction_engine.knowledge_extraction.metadata_extractor import (
            collect_user_entities_for_metadata,
        )

        llm_model_id = (
            str(self.memory_config.llm_model_id)
            if self.memory_config.llm_model_id
            else None
        )
        recorder = getattr(self, "_recorder", None)
        snapshot_dir = (
            recorder.snapshot_dir
            if recorder is not None and recorder.enabled
            else None
        )

        # ── 1. Neo4j 别名归并 + 第二层去重 ──
        self._submit_celery_task(
            "PostStore",
            "app.tasks.post_store_dedup_and_alias_merge",
            {
                "end_user_id": self.end_user_id,
                "entity_ids": [e.id for e in result.entity_nodes],
                "llm_model_id": llm_model_id,
                "snapshot_dir": snapshot_dir,
            },
        )

        # ── 2. 异步情绪提取 ──
        emotion_statements = getattr(self, "_emotion_statements", [])
        if emotion_statements and llm_model_id:
            self._submit_celery_task(
                "Emotion",
                "app.tasks.extract_emotion_batch",
                {
                    "statements": emotion_statements,
                    "llm_model_id": llm_model_id,
                    "language": self.language,
                    "snapshot_dir": snapshot_dir,
                },
            )

        # ── 3. 异步元数据提取 ──
        user_entities = collect_user_entities_for_metadata(result.entity_nodes)
        if user_entities and llm_model_id:
            self._submit_celery_task(
                "Metadata",
                "app.tasks.extract_metadata_batch",
                {
                    "user_entities": user_entities,
                    "llm_model_id": llm_model_id,
                    "language": self.language,
                    "snapshot_dir": snapshot_dir,
                },
            )

    def _submit_celery_task(
        self, label: str, task_name: str, kwargs: dict
    ) -> None:
        """提交 Celery 异步任务的通用方法。失败只记日志，不抛异常。"""
        try:
            from app.celery_app import celery_app

            task_result = celery_app.send_task(task_name, kwargs=kwargs)
            logger.info(f"[{label}] 异步任务已提交 - task_id={task_result.id}")
        except Exception as e:
            logger.error(
                f"[{label}] 提交异步任务失败（不影响主流程）: {e}",
                exc_info=True,
            )

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
                    "llm_model_id": (
                        str(self.memory_config.llm_model_id)
                        if self.memory_config.llm_model_id
                        else None
                    ),
                    "embedding_model_id": (
                        str(self.memory_config.embedding_model_id)
                        if self.memory_config.embedding_model_id
                        else None
                    ),
                    "language": self.language,
                },
                priority=3,
            )
            logger.info(
                f"[Clustering] 增量聚类任务已提交 - "
                f"task_id = {task.id}, "
                f"entity_count = {len(new_entity_ids)}, "
                f"source=dedup"
            )
        except Exception as e:
            logger.error(
                f"[Clustering] 提交聚类任务失败（不影响主流程）: {e}",
                exc_info=True,
            )

    # ──────────────────────────────────────────────
    # Step 5: 摘要
    # （+ entity_description）+ meta_data部分在此提取
    # ──────────────────────────────────────────────
    # TODO 乐力齐 需要做成异步celery任务
    async def _summarize(self, chunked_dialogs: List[DialogData]) -> None:
        """
        摘要：生成情景记忆摘要 → 写入 Neo4j。

        摘要生成失败不影响主流程（try/except 吞掉异常）。
        使用独立的 Neo4j 连接器，避免与主连接器的事务冲突。
        """
        from app.core.memory.storage_services.extraction_engine.knowledge_extraction.memory_summary import (
            memory_summary_generation,
        )
        from app.repositories.neo4j.add_edges import (
            add_memory_summary_statement_edges,
        )
        from app.repositories.neo4j.add_nodes import add_memory_summary_nodes
        from app.repositories.neo4j.neo4j_connector import Neo4jConnector

        try:
            summaries = await memory_summary_generation(
                chunked_dialogs,
                llm_client=self._llm_client,
                embedder_client=self._embedder_client,
                language=self.language,
            )
            ms_connector = Neo4jConnector()
            try:
                await add_memory_summary_nodes(summaries, ms_connector)
                await add_memory_summary_statement_edges(summaries, ms_connector)
            finally:
                try:
                    await ms_connector.close()
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"Memory summary step failed: {e}", exc_info=True)

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
        from app.services.memory_perceptual_service import MemoryPerceptualService

        for msg in messages:
            files = msg.get("files") or []
            if not files:
                msg["file_content"] = []
                continue

            msg["file_content"] = []
            for file_info in files:
                url = file_info.get("url", "")
                if not url:
                    continue

                try:
                    with get_db_context() as db:
                        # 先查找已有的 Perceptual 记录
                        repo = MemoryPerceptualRepository(db)
                        memories = repo.get_by_url(url)

                        if memories:
                            # 已存在，复用最新的一条
                            memory = max(
                                memories,
                                key=lambda m: m.created_time if m.created_time else datetime.min,
                            )
                            # 在 Session 内提取所有需要的属性到一个简单对象中，
                            # 彻底避免 DetachedInstanceError
                            class _PerceptualSnapshot:
                                pass
                            snap = _PerceptualSnapshot()
                            snap.id = memory.id
                            snap.summary = memory.summary
                            snap.meta_data = memory.meta_data
                            snap.file_path = memory.file_path
                            snap.file_name = memory.file_name
                            snap.file_ext = memory.file_ext
                            snap.perceptual_type = memory.perceptual_type
                            snap.end_user_id = memory.end_user_id
                            snap.created_time = memory.created_time
                            file_object = snap
                        else:
                            # 不存在，创建 Perceptual 记录
                            perceptual_service = MemoryPerceptualService(db)
                            # 补充 transfer_method（memory_messages.files 中可能缺失此字段）
                            file_data = dict(file_info)
                            if "transfer_method" not in file_data:
                                file_data["transfer_method"] = "remote_url" if file_data.get("url") else "local_file"
                            file_input = FileInput(**file_data)
                            file_object = await perceptual_service.generate_perceptual_memory(
                                end_user_id=self.end_user_id,
                                memory_config=self.memory_config,
                                file=file_input,
                                content=msg.get("content") or None,
                            )

                        if file_object is None:
                            continue

                        # 注入 summary 到 content
                        if file_object.summary:
                            msg["content"] = (msg.get("content") or "") + f"<input-file-summary>{file_object.summary}</input-file-summary>"

                        msg["file_content"].append((file_object, file_info.get("type", "")))

                except Exception as e:
                    logger.warning(
                        f"[WritePipeline] 文件预处理失败: url={url}, err={e}",
                        exc_info=True,
                    )

    # ──────────────────────────────────────────────
    # 辅助方法
    # ──────────────────────────────────────────────

    def _init_clients(self) -> None:
        """
        从 MemoryConfig 构建 LLM 和 Embedding 客户端。

        使用 MemoryClientFactory 工厂模式，需要短暂的 DB session 来
        查询模型配置（API key、base_url 等），查询完毕立即释放。
        """
        from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
        from app.db import get_db_context

        with get_db_context() as db:
            factory = MemoryClientFactory(db)
            self._llm_client = factory.get_llm_client_from_config(self.memory_config)
            self._embedder_client = factory.get_embedder_client_from_config(
                self.memory_config
            )
        logger.info("LLM and embedding clients constructed")

    def _init_neo4j_connector(self) -> None:
        """初始化 Neo4j 连接器。"""
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

    # ──────────────────────────────────────────────
    # 滑动窗口写入：公开入口
    # ──────────────────────────────────────────────

    async def run_with_window(
        self,
        target_message: dict,
        context_before: List[dict],
        context_after: List[dict],
        conversation_id: str,
        message_seq: int,
        ref_id: str = "",
        dispatch_at: str = "",
    ) -> WriteResult:
        """滑动窗口写入入口。

        Args:
            target_message: 目标 user 消息 {"role": "user", "content": "...", ...}
            context_before: 上文消息列表（按 message_seq 升序）
            context_after: 下文消息列表（按 message_seq 升序）
            conversation_id: 对话 ID
            message_seq: 目标消息的 message_seq
            ref_id: 引用 ID，为空则自动生成
            dispatch_at: 任务派发时刻的 UTC ISO 8601 时间戳

        Returns:
            WriteResult 包含状态和统计信息
        """
        if not ref_id:
            ref_id = uuid.uuid4().hex

        _dialog_at = (
            dispatch_at
            or target_message.get("created_at", "")
            or ""
        )
        target_message = {**target_message, "dialog_at": _dialog_at}

        extraction_result = None

        try:
            async with bear.pipeline(
                "WritePipeline.run_with_window",
                mode="滑动窗口",
                config_name=self.memory_config.config_name,
                end_user_id=self.end_user_id,
                conversation_id=conversation_id,
                message_seq=message_seq,
            ):
                self._init_clients()
                self._init_neo4j_connector()

                from app.core.memory.utils.debug.write_snapshot_recorder import (
                    WriteSnapshotRecorder,
                )
                self._recorder = WriteSnapshotRecorder("new")

                from app.core.memory.pipelines.pruning_pipeline import PruningPipeline
                pruning_pipeline = PruningPipeline(
                    memory_config=self.memory_config,
                    end_user_id=self.end_user_id,
                    language=self.language,
                )

                async with bear.step(1, 5, "剪枝", "构建 Pruned_Context") as s:
                    pruning_records: list = []
                    _before = context_before or []
                    _after = context_after or []
                    context_before_pruned = await self._build_pruned_context(
                        messages=_before,
                        conversation_id=conversation_id,
                        pruning_pipeline=pruning_pipeline,
                        pruning_records=pruning_records,
                    )
                    context_after_pruned = await self._build_pruned_context(
                        messages=_after,
                        conversation_id=conversation_id,
                        pruning_pipeline=pruning_pipeline,
                        pruning_records=pruning_records,
                    )
                    s.metadata(
                        before_count=len(context_before_pruned),
                        after_count=len(context_after_pruned),
                        pruned_count=len(pruning_records),
                    )
                    recorder = getattr(self, "_recorder", None)
                    if recorder is not None:
                        recorder.record_pruning_results(pruning_records)

                messages = [target_message]

                # 文件预处理：生成 Perceptual 记录并注入 summary 到 content
                # 与旧路径 memory_agent_service._preprocess_files 逻辑一致
                await self._preprocess_files(messages)
                await self._preprocess_files(context_before_pruned)
                await self._preprocess_files(context_after_pruned)

                async with bear.step(2, 5, "预处理", "消息分块") as s:
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
                    )
                    s.metadata(chunks=sum(len(d.chunks) for d in chunked_dialogs))

                async with bear.step(3, 5, "萃取", "知识提取") as s:
                    extraction_result = await self._extract(chunked_dialogs, is_pilot_run=False)
                    self._merge_alias_in_memory(extraction_result)
                    stats = extraction_result.stats
                    s.metadata(
                        entities=stats["entity_count"],
                        statements=stats["statement_count"],
                        relations=stats["relation_count"],
                    )

                async with bear.step(4, 5, "存储", "写入 Neo4j"):
                    await self._store(extraction_result)

                await self._post_store_async_tasks(extraction_result)

                async with bear.step(5, 5, "聚类", "增量更新社区") as s:
                    await self._cluster(extraction_result)
                    s.metadata(mode="async")

                await self._advance_write_cursor(
                    conversation_id=conversation_id,
                    message_seq=message_seq,
                )

                await self._update_stats_cache(extraction_result)

                return WriteResult(
                    status="success",
                    extraction=extraction_result.stats,
                    elapsed_seconds=0.0,
                )

        except Exception:
            raise

        finally:
            await self._cleanup()

    # ──────────────────────────────────────────────
    # 滑动窗口写入：辅助方法
    # ──────────────────────────────────────────────

    async def _build_pruned_context(
        self,
        messages: List[dict],
        conversation_id: str,
        pruning_pipeline: "PruningPipeline",
        pruning_records: Optional[list] = None,
    ) -> List[dict]:
        """将消息列表中的 assistant 消息替换为剪枝后版本（A'），同时处理 user 消息规整。

        assistant 消息剪枝并发执行（asyncio.gather），窗口上下文中多条
        assistant 消息之间无依赖关系。单条失败时回退到原文，不阻塞其他剪枝任务。

        当 LLM 判断 user 消息需要规整（should_process_user_msg=true）时，
        用 processed_user_msg 替换原始 user 消息内容进入下一阶段。

        Args:
            messages: 消息列表，每条消息包含 role、content、message_seq 等字段
            conversation_id: 对话 ID
            pruning_pipeline: 已初始化的 PruningPipeline 实例
            pruning_records: 可选的列表，若传入则将剪枝记录追加到其中

        Returns:
            替换后的消息列表（Pruned_Context）

        Requirements: 2.1, 2.3
        """
        # 预计算每条 assistant 消息紧邻的 user 消息内容
        last_user_content = ""
        last_user_idx = -1
        prune_tasks: list = []  # (index, user_idx, coroutine)
        result: List[Optional[dict]] = [None] * len(messages)
        for i, msg in enumerate(messages):
            if msg.get("role") == "user":
                last_user_content = msg.get("content", "")
                last_user_idx = i
                result[i] = msg
            elif msg.get("role") == "assistant":
                user_ctx = last_user_content
                user_idx = last_user_idx
                prune_tasks.append((i, user_idx, pruning_pipeline.prune(
                    conversation_id=conversation_id,
                    message_seq=msg.get("message_seq", 0),
                    content=msg.get("content", ""),
                    user_content=user_ctx,
                    _pruning_records=pruning_records,
                )))
                last_user_content = ""
                last_user_idx = -1
            else:
                result[i] = msg

        # 并发执行所有剪枝任务
        if prune_tasks:
            coros = [coro for _, _, coro in prune_tasks]
            pruned_results = await asyncio.gather(*coros, return_exceptions=True)
            for (idx, user_idx, _), prune_result in zip(prune_tasks, pruned_results):
                if isinstance(prune_result, BaseException):
                    logger.warning(
                        f"[WritePipeline] 剪枝失败，回退原文: "
                        f"conv={conversation_id}, seq={messages[idx].get('message_seq')}, err={prune_result}"
                    )
                    result[idx] = messages[idx]
                else:
                    pruned_content, should_process_user_msg, processed_user_msg = prune_result
                    # 替换 assistant 消息为剪枝后内容
                    result[idx] = {**messages[idx], "content": pruned_content}
                    # 如果 user 消息需要规整，用 processed_user_msg 替换
                    if (
                        should_process_user_msg
                        and processed_user_msg is not None
                        and processed_user_msg.strip() != ""
                        and user_idx >= 0
                        and result[user_idx] is not None
                    ):
                        result[user_idx] = {**result[user_idx], "content": processed_user_msg}

        return [m for m in result if m is not None]

    async def _advance_write_cursor(
        self,
        conversation_id: str,
        message_seq: int,
    ) -> None:
        """原子性推进对话的 write_cursor。

        执行原子性 UPDATE conversations SET write_cursor = :new_seq
        WHERE id = :conv_id AND write_cursor < :new_seq，
        确保 write_cursor 只能单调递增，防止并发任务乱序覆盖。

        更新失败时记录警告日志，不抛出异常（fire-and-forget 语义）。

        Args:
            conversation_id: 对话 ID
            message_seq: 目标 user 消息的 message_seq，作为新的 write_cursor 值

        Requirements: 3.2
        """
        try:
            from sqlalchemy import update
            from app.models.conversation_model import Conversation
            from app.db import get_db_context

            with get_db_context() as db:
                result = db.execute(
                    update(Conversation)
                    .where(
                        Conversation.id == conversation_id,
                        Conversation.write_cursor < message_seq,
                    )
                    .values(write_cursor=message_seq)
                )
                db.commit()

                if result.rowcount == 0:
                    logger.warning(
                        f"[WritePipeline] write_cursor 未更新（可能已被更大的 seq 覆盖）: "
                        f"conv={conversation_id}, seq={message_seq}"
                    )
                else:
                    logger.info(
                        f"[WritePipeline] write_cursor 已推进: "
                        f"conv={conversation_id}, new_cursor={message_seq}"
                    )
        except Exception as e:
            logger.warning(
                f"[WritePipeline] _advance_write_cursor 失败（不影响主流程）: "
                f"conv={conversation_id}, seq={message_seq}, err={e}",
                exc_info=True,
            )

    async def _cleanup(self) -> None:
        """
        清理资源：关闭 Neo4j 连接器和 HTTP 客户端。
        在 run() 的 finally 块中调用，确保资源释放。
        """
        # 关闭 Neo4j 连接器
        if self._neo4j_connector:
            try:
                await self._neo4j_connector.close()
            except Exception as e:
                logger.error(f"Error closing Neo4j connector: {e}")

        # 关闭 LLM/Embedder 底层 httpx 客户端
        # 防止 'RuntimeError: Event loop is closed' 在垃圾回收时触发
        for client_obj in (self._llm_client, self._embedder_client):
            try:
                underlying = getattr(client_obj, "client", None) or getattr(
                    client_obj, "model", None
                )
                if underlying is None:
                    continue
                inner = getattr(underlying, "_model", underlying)
                http_client = getattr(inner, "async_client", None)
                if http_client is not None and hasattr(http_client, "aclose"):
                    await http_client.aclose()
            except Exception:
                pass
