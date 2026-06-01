"""WriteSnapshotRecorder — 写入流水线快照记录器。

将 WritePipeline 中所有 snapshot 序列化逻辑集中到此模块，
让 Pipeline 只做编排，不关心调试输出的数据格式。

Pipeline 侧调用示例：
    recorder = WriteSnapshotRecorder(end_user_id="abc123")
    recorder.record_stage_outputs(orchestrator.last_stage_outputs)
    recorder.record_graph_before_dedup(graph)
    recorder.record_dedup_result(dedup_result)
    recorder.record_summary(extraction_result.stats)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.core.memory.utils.debug.pipeline_snapshot import PipelineSnapshot

logger = logging.getLogger(__name__)


class WriteSnapshotRecorder:
    """写入流水线各阶段的快照记录器。

    内部持有一个 PipelineSnapshot 实例，对外暴露语义化方法，
    每个方法对应流水线中的一个可观测阶段。

    当 PIPELINE_SNAPSHOT_ENABLED=false 时，所有方法均为空操作（no-op）。
    """

    def __init__(
        self,
        end_user_id: str,
        conversation_id: Optional[str] = None,
        message_seq: Optional[int] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ):
        self._snapshot = PipelineSnapshot(
            end_user_id=end_user_id,
            conversation_id=conversation_id,
            message_seq=message_seq,
            extra_metadata=extra_metadata,
        )

    # ── 属性 ──

    @property
    def enabled(self) -> bool:
        return self._snapshot.enabled

    @property
    def snapshot_dir(self) -> Optional[str]:
        """快照输出目录的绝对路径，未启用时返回 None。"""
        return self._snapshot.directory

    @property
    def snapshot(self) -> PipelineSnapshot:
        """暴露底层 PipelineSnapshot，供需要直接传递的场景使用（如 SemanticPruner）。"""
        return self._snapshot

    # ── Stage 1: 剪枝结果 ──

    def record_pruning_results(self, pruning_records: list) -> None:
        """记录 PruningPipeline 对 assistant 消息的剪枝结果。

        每条记录包含：
        - conversation_id: 对话 ID
        - message_seq: 消息序号
        - original_content: 原始 assistant 消息内容
        - pruned_content: 剪枝后内容（A'）
        - source: "llm"（本轮 LLM 剪枝）或 "cache"（Redis 缓存命中）

        Args:
            pruning_records: 剪枝记录列表，每条为 dict
        """
        if not pruning_records:
            return
        self._snapshot.save_stage("1_assistant_pruning", pruning_records)

    # ── Stage 2-5: 萃取阶段各步骤输出 ──

    def record_stage_outputs(self, stage_outputs: Optional[Dict[str, Any]]) -> None:
        """记录 NewExtractionOrchestrator 各步骤的输出。

        对应原 write_pipeline._extract() 中 stage_outputs 的序列化逻辑，
        包括 statement / triplet / emotion / embedding 四个阶段。
        """
        if not stage_outputs:
            return

        self._record_statements(
            stage_outputs.get("statement_results", {}),
            stage_outputs.get("statement_inputs", {}),
        )
        self._record_triplets(stage_outputs.get("triplet_results", {}))
        self._record_emotions(stage_outputs.get("emotion_results", {}))
        self._record_embeddings(stage_outputs.get("embedding_output"))

    # ── Stage 6: 图构建（去重前） ──

    def record_graph_before_dedup(self, graph: Any) -> None:
        """记录 build_graph_nodes_and_edges 的输出（去重前）。"""
        self._snapshot.save_stage(
            "6_nodes_edges_before_dedup",
            {
                "dialogue_nodes_count": len(graph.dialogue_nodes),
                "chunk_nodes_count": len(graph.chunk_nodes),
                "statement_nodes_count": len(graph.statement_nodes),
                "entity_nodes": [
                    {
                        "id": e.id,
                        "name": e.name,
                        "entity_type": e.entity_type,
                        "type_id": e.type_id,
                        "description": e.description,
                    }
                    for e in graph.entity_nodes
                ],
                "entity_entity_edges": [
                    {
                        "source": e.source,
                        "target": e.target,
                        "relation_type": e.relation_type,
                        "relation_type_id": e.relation_type_id,
                        "relation_type_surface": e.relation_type_surface,
                        "statement": e.statement,
                    }
                    for e in graph.entity_entity_edges
                ],
                "stmt_entity_edges_count": len(graph.stmt_entity_edges),
            },
        )

    # ── Stage 7: 去重后 ──

    def record_dedup_result(self, dedup_result: Any) -> None:
        """记录两阶段去重消歧后的实体和关系。"""
        self._snapshot.save_stage(
            "7_after_dedup",
            {
                "entity_nodes": [
                    {
                        "id": e.id,
                        "name": e.name,
                        "entity_type": e.entity_type,
                        "description": e.description,
                    }
                    for e in dedup_result.entity_nodes
                ],
                "entity_entity_edges": [
                    {
                        "source": e.source,
                        "target": e.target,
                        "relation_type": e.relation_type,
                        "statement": e.statement,
                    }
                    for e in dedup_result.entity_entity_edges
                ],
            },
        )

    # ── Stage 8: 别名归并后（异步，由 Celery PostStore 任务写入） ──

    @staticmethod
    def save_alias_merge_result(snapshot_dir: str, entity_rows: List[Dict]) -> None:
        """将别名归并+节点删除后的 Neo4j 实体状态写入 8_after_alias_merge.json。

        由 Celery post_store_dedup_and_alias_merge 任务在完成归并和删除后调用，
        直接写入已有的 snapshot 目录前缀下，无需重建 WriteSnapshotRecorder 实例。

        Args:
            snapshot_dir: 主流水线创建的本地 snapshot 目录路径（如
                          "<...>/snapshots/{end_user_id}/{conversation_id}/seq_xxx_时间戳"）。
            entity_rows:  从 Neo4j 查询到的实体属性列表，每项包含
                          id / name / entity_type / description / aliases 字段。
        """
        from app.core.memory.utils.debug.pipeline_snapshot import (
            upload_stage_snapshot,
        )

        data = {
            "entity_nodes": [
                {
                    "id": row.get("id"),
                    "name": row.get("name"),
                    "entity_type": row.get("entity_type"),
                    "description": row.get("description"),
                    "aliases": row.get("aliases", []),
                }
                for row in entity_rows
            ],
            "entity_count": len(entity_rows),
        }
        upload_stage_snapshot(snapshot_dir, "8_after_alias_merge", data)

    # ── Stage 0: 汇总 ──

    def record_summary(self, stats: Dict[str, int]) -> None:
        """记录流水线最终统计摘要。"""
        self._snapshot.save_summary(stats)

    # ── 内部方法 ──

    def _record_statements(
        self,
        stmt_results: Dict,
        stmt_inputs: Optional[Dict] = None,
    ) -> None:
        """记录 statement 抽取结果，并附带 LLM 输入上下文。

        新格式按 chunk 分组，每条记录包含：
        - dialog_id / chunk_id：定位信息
        - input：本次 extract_statement 注入给 LLM 的上下文
            (target_content / target_message_date / dialog_at /
             supporting_context.msgs)
        - outputs：LLM 抽取出来的 statement 列表
        便于人工核对每个 chunk 的输入是否正确、输出是否合理。
        """
        stmt_inputs = stmt_inputs or {}
        snapshot_data: List[Dict] = []
        for did, chunk_stmts in stmt_results.items():
            chunk_inputs = stmt_inputs.get(did, {})
            for cid, stmts in chunk_stmts.items():
                step_input = chunk_inputs.get(cid)
                input_dump: Optional[Dict[str, Any]] = None
                if step_input is not None and hasattr(step_input, "model_dump"):
                    full = step_input.model_dump()
                    # 仅保留与 extract_statement 提示词相关的字段，避免冗余
                    input_dump = {
                        "target_content": full.get("target_content"),
                        "target_message_date": full.get("target_message_date"),
                        "dialog_at": full.get("dialog_at"),
                        "supporting_context": full.get("supporting_context"),
                    }
                snapshot_data.append(
                    {
                        "dialog_id": did,
                        "chunk_id": cid,
                        "input": input_dump,
                        "outputs": [s.model_dump() for s in stmts],
                    }
                )
        self._snapshot.save_stage("2_statement_outputs", snapshot_data)

    def _record_triplets(self, triplet_results: Dict) -> None:
        snapshot_data: Dict[str, Any] = {}
        for _did, stmt_triplets in triplet_results.items():
            for stmt_id, t_out in stmt_triplets.items():
                snapshot_data[stmt_id] = t_out.model_dump()
        self._snapshot.save_stage("3_triplet_outputs", snapshot_data)

    def _record_emotions(self, emotion_results: Dict) -> None:
        snapshot_data: Dict[str, Any] = {}
        for stmt_id, emo in emotion_results.items():
            if hasattr(emo, "model_dump"):
                snapshot_data[stmt_id] = emo.model_dump()
        self._snapshot.save_stage("4_emotion_outputs", snapshot_data)

    def _record_embeddings(self, emb_output: Any) -> None:
        if not emb_output or not hasattr(emb_output, "model_dump"):
            return

        emb_data = emb_output.model_dump()

        # 截断向量，只保留前 5 维用于调试
        for key in ("statement_embeddings", "chunk_embeddings", "entity_embeddings"):
            if key in emb_data and isinstance(emb_data[key], dict):
                emb_data[key] = {
                    k: v[:5] if isinstance(v, list) else v
                    for k, v in emb_data[key].items()
                }
        if "dialog_embeddings" in emb_data and isinstance(
            emb_data["dialog_embeddings"], list
        ):
            emb_data["dialog_embeddings"] = [
                v[:5] if isinstance(v, list) else v
                for v in emb_data["dialog_embeddings"]
            ]

        self._snapshot.save_stage("5_embedding_outputs", emb_data)
