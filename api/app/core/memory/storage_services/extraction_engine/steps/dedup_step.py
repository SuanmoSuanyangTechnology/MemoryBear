"""Independent deduplication module for the extraction pipeline.

Extracts dedup logic from ExtractionOrchestrator into standalone functions
so the orchestrator stays thin and dedup can be tested/evolved independently.

The module exposes:
    - ``DedupResult`` — structured output of the dedup process
    - ``run_dedup()`` — async entry point called by WritePipeline
    - Helper functions migrated from ExtractionOrchestrator:
      ``save_dedup_details``, ``analyze_entity_merges``,
      ``analyze_entity_disambiguation``, ``send_dedup_progress_callback``,
      ``parse_dedup_report``
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.core.memory.models.graph_models import (
    EntityEntityEdge,
    ExtractedEntityNode,
    StatementEntityEdge,
)
from app.core.memory.models.message_models import DialogData
from app.core.memory.models.variate_config import ExtractionPipelineConfig
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DedupResult dataclass (Requirement 10.2)
# ---------------------------------------------------------------------------

@dataclass
class DedupResult:
    """Structured output of the two-stage entity deduplication process.

    Attributes:
        entity_nodes: Deduplicated entity node list.
        statement_entity_edges: Deduplicated statement-entity edges.
        entity_entity_edges: Deduplicated entity-entity edges.
        dedup_details: Raw detail dict returned by the first-layer dedup.
        merge_records: Parsed merge records (exact / fuzzy / LLM).
        disamb_records: Parsed disambiguation records.
    """

    entity_nodes: List[ExtractedEntityNode]
    statement_entity_edges: List[StatementEntityEdge]
    entity_entity_edges: List[EntityEntityEdge]
    dedup_details: Dict[str, Any] = field(default_factory=dict)
    merge_records: List[Dict[str, Any]] = field(default_factory=list)
    disamb_records: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def stats(self) -> Dict[str, int]:
        """Summary statistics for the dedup run."""
        return {
            "entity_count": len(self.entity_nodes),
            "merge_count": len(self.merge_records),
            "disamb_count": len(self.disamb_records),
        }


# ---------------------------------------------------------------------------
# Migrated helpers (from ExtractionOrchestrator)  — Requirement 10.4
# ---------------------------------------------------------------------------


def save_dedup_details(
    dedup_details: Dict[str, Any],
    original_entities: List[ExtractedEntityNode],
    final_entities: List[ExtractedEntityNode],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, str]]:
    """Parse raw *dedup_details* into structured merge / disamb records.

    Returns:
        (merge_records, disamb_records, id_redirect_map)
    """
    merge_records: List[Dict[str, Any]] = []
    disamb_records: List[Dict[str, Any]] = []
    id_redirect_map: Dict[str, str] = {}

    try:
        id_redirect_map = dedup_details.get("id_redirect", {})

        # --- exact-match merges ---
        exact_merge_map = dedup_details.get("exact_merge_map", {})
        for _key, info in exact_merge_map.items():
            merged_ids = info.get("merged_ids", set())
            if merged_ids:
                merge_records.append({
                    "type": "精确匹配",
                    "canonical_id": info.get("canonical_id"),
                    "entity_name": info.get("name"),
                    "entity_type": info.get("entity_type"),
                    "merged_count": len(merged_ids),
                    "merged_ids": list(merged_ids),
                })

        # --- fuzzy-match merges ---
        for record in dedup_details.get("fuzzy_merge_records", []):
            try:
                match = re.search(
                    r"规范实体 (\S+) \(([^|]+)\|([^|]+)\|([^)]+)\) <- 合并实体 (\S+)",
                    record,
                )
                if match:
                    merge_records.append({
                        "type": "模糊匹配",
                        "canonical_id": match.group(1),
                        "entity_name": match.group(3),
                        "entity_type": match.group(4),
                        "merged_count": 1,
                        "merged_ids": [match.group(5)],
                    })
            except Exception as e:
                logger.debug("解析模糊匹配记录失败: %s, 错误: %s", record, e)

        # --- LLM-based merges ---
        for record in dedup_details.get("llm_decision_records", []):
            if "[LLM去重]" in str(record):
                try:
                    match = re.search(
                        r"同名类型相似 ([^（]+)（([^）]+)）\|([^（]+)（([^）]+)）",
                        record,
                    )
                    if match:
                        merge_records.append({
                            "type": "LLM去重",
                            "entity_name": match.group(1),
                            "entity_type": f"{match.group(2)}|{match.group(4)}",
                            "merged_count": 1,
                            "merged_ids": [],
                        })
                except Exception as e:
                    logger.debug("解析LLM去重记录失败: %s, 错误: %s", record, e)

        # --- disambiguation records ---
        for record in dedup_details.get("disamb_records", []):
            if "[DISAMB阻断]" in str(record):
                try:
                    content = str(record).replace("[DISAMB阻断]", "").strip()
                    match = re.search(
                        r"([^（]+)（([^）]+)）\|([^（]+)（([^）]+)）", content
                    )
                    if match:
                        entity1_name = match.group(1).strip()
                        entity1_type = match.group(2)
                        entity2_type = match.group(4)

                        conf_match = re.search(r"conf=([0-9.]+)", str(record))
                        confidence = conf_match.group(1) if conf_match else "unknown"

                        reason_match = re.search(r"reason=([^|]+)", str(record))
                        reason = reason_match.group(1).strip() if reason_match else ""

                        disamb_records.append({
                            "entity_name": entity1_name,
                            "disamb_type": f"消歧阻断：{entity1_type} vs {entity2_type}",
                            "confidence": confidence,
                            "reason": (reason[:100] + "...") if len(reason) > 100 else reason,
                        })
                except Exception as e:
                    logger.debug("解析消歧记录失败: %s, 错误: %s", record, e)

        logger.info(
            "保存去重消歧记录：%d 个合并记录，%d 个消歧记录",
            len(merge_records),
            len(disamb_records),
        )
    except Exception as e:
        logger.error("保存去重消歧详情失败: %s", e, exc_info=True)

    return merge_records, disamb_records, id_redirect_map


def analyze_entity_merges(
    merge_records: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return merge info sorted by merged_count (descending)."""
    if not merge_records:
        return []
    sorted_records = sorted(
        merge_records, key=lambda x: x.get("merged_count", 0), reverse=True
    )
    return [
        {
            "main_entity_name": r.get("entity_name", "未知实体"),
            "merged_count": r.get("merged_count", 1),
        }
        for r in sorted_records
    ]


def analyze_entity_disambiguation(
    disamb_records: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return disambiguation records (pass-through)."""
    return disamb_records if disamb_records else []


def parse_dedup_report(
    merge_records: List[Dict[str, Any]],
    disamb_records: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build a summary report dict from parsed records."""
    try:
        dedup_examples: List[Dict[str, Any]] = []
        disamb_examples: List[Dict[str, Any]] = []
        total_merges = 0
        total_disambiguations = 0

        for record in merge_records:
            merge_count = record.get("merged_count", 0)
            total_merges += merge_count
            dedup_examples.append({
                "type": record.get("type", "未知"),
                "entity_name": record.get("entity_name", "未知实体"),
                "entity_type": record.get("entity_type", "未知类型"),
                "merge_count": merge_count,
                "description": f"{record.get('entity_name', '未知实体')}实体去重合并{merge_count}个",
            })

        for record in disamb_records:
            total_disambiguations += 1
            disamb_type = record.get("disamb_type", "")
            entity_name = record.get("entity_name", "未知实体")
            disamb_examples.append({
                "entity1_name": entity_name,
                "entity1_type": (
                    disamb_type.split("vs")[0].replace("消歧阻断：", "").strip()
                    if "vs" in disamb_type
                    else "未知"
                ),
                "entity2_name": entity_name,
                "entity2_type": (
                    disamb_type.split("vs")[1].strip() if "vs" in disamb_type else "未知"
                ),
                "description": f"{entity_name}，消歧区分成功",
            })

        return {
            "dedup_examples": dedup_examples[:5],
            "disamb_examples": disamb_examples[:5],
            "total_merges": total_merges,
            "total_disambiguations": total_disambiguations,
        }
    except Exception as e:
        logger.error("获取去重报告失败: %s", e, exc_info=True)
        return {
            "dedup_examples": [],
            "disamb_examples": [],
            "total_merges": 0,
            "total_disambiguations": 0,
        }


async def send_dedup_progress_callback(
    progress_callback: Callable,
    merge_records: List[Dict[str, Any]],
    disamb_records: List[Dict[str, Any]],
    original_entities: int,
    final_entities: int,
    original_stmt_edges: int,
    final_stmt_edges: int,
    original_ent_edges: int,
    final_ent_edges: int,
) -> None:
    """Send dedup completion progress via *progress_callback*."""
    try:
        dedup_details = parse_dedup_report(merge_records, disamb_records)

        entities_reduced = original_entities - final_entities
        stmt_edges_reduced = original_stmt_edges - final_stmt_edges
        ent_edges_reduced = original_ent_edges - final_ent_edges

        dedup_stats = {
            "entities": {
                "original_count": original_entities,
                "final_count": final_entities,
                "reduced_count": entities_reduced,
                "reduction_rate": (
                    round(entities_reduced / original_entities * 100, 1)
                    if original_entities > 0
                    else 0
                ),
            },
            "statement_entity_edges": {
                "original_count": original_stmt_edges,
                "final_count": final_stmt_edges,
                "reduced_count": stmt_edges_reduced,
            },
            "entity_entity_edges": {
                "original_count": original_ent_edges,
                "final_count": final_ent_edges,
                "reduced_count": ent_edges_reduced,
            },
            "dedup_examples": dedup_details.get("dedup_examples", []),
            "disamb_examples": dedup_details.get("disamb_examples", []),
            "summary": {
                "total_merges": dedup_details.get("total_merges", 0),
                "total_disambiguations": dedup_details.get("total_disambiguations", 0),
            },
        }

        await progress_callback("dedup_disambiguation_complete", "去重消歧完成", dedup_stats)
    except Exception as e:
        logger.error("发送去重消歧进度回调失败: %s", e, exc_info=True)
        try:
            basic_stats = {
                "entities": {
                    "original_count": original_entities,
                    "final_count": final_entities,
                    "reduced_count": original_entities - final_entities,
                },
                "summary": f"实体去重合并{original_entities - final_entities}个",
            }
            await progress_callback("dedup_disambiguation_complete", "去重消歧完成", basic_stats)
        except Exception as e2:
            logger.error("发送基本去重统计失败: %s", e2, exc_info=True)


# ---------------------------------------------------------------------------
# run_dedup — main entry point (Requirements 10.1, 10.3)
# ---------------------------------------------------------------------------


async def run_dedup(
    entity_nodes: List[ExtractedEntityNode],
    statement_entity_edges: List[StatementEntityEdge],
    entity_entity_edges: List[EntityEntityEdge],
    dialog_data_list: List[DialogData],
    pipeline_config: ExtractionPipelineConfig,
    connector: Optional[Neo4jConnector] = None,
    llm_client: Optional[Any] = None,
    is_pilot_run: bool = False,
    progress_callback: Optional[Callable] = None,
) -> DedupResult:
    """Two-stage entity deduplication and disambiguation.

    Full mode:
        Layer 1 — exact / fuzzy / LLM matching
        Layer 2 — Neo4j joint dedup + cross-role alias cleaning

    Pilot-run mode:
        Layer 1 only (skip Neo4j layer 2 and alias cleaning).

    Args:
        entity_nodes: Pre-dedup entity nodes.
        statement_entity_edges: Pre-dedup statement-entity edges.
        entity_entity_edges: Pre-dedup entity-entity edges.
        dialog_data_list: Source dialogue data (used to detect end_user_id).
        pipeline_config: Pipeline configuration (contains DedupConfig).
        connector: Optional Neo4j connector for layer-2 dedup.
        llm_client: Optional LLM client for LLM-based dedup decisions.
        is_pilot_run: When True, only execute layer-1 dedup.
        progress_callback: Optional async callable for progress reporting.

    Returns:
        A ``DedupResult`` with deduplicated nodes, edges, and statistics.
    """
    logger.info("开始两阶段实体去重和消歧")

    if progress_callback:
        await progress_callback("deduplication", "正在去重消歧...")

    logger.info(
        "去重前: %d 个实体节点, %d 条陈述句-实体边, %d 条实体-实体边",
        len(entity_nodes),
        len(statement_entity_edges),
        len(entity_entity_edges),
    )

    original_entity_count = len(entity_nodes)
    original_stmt_edge_count = len(statement_entity_edges)
    original_ent_edge_count = len(entity_entity_edges)

    try:
        if is_pilot_run:
            # --- pilot run: layer 1 only ---
            logger.info("试运行模式：仅执行第一层去重，跳过第二层数据库去重")
            from app.core.memory.storage_services.extraction_engine.deduplication.deduped_and_disamb import (
                deduplicate_entities_and_edges,
            )

            (
                dedup_entity_nodes,
                dedup_stmt_edges,
                dedup_ent_edges,
                raw_details,
            ) = await deduplicate_entities_and_edges(
                entity_nodes,
                statement_entity_edges,
                entity_entity_edges,
                report_stage="第一层去重消歧（试运行）",
                report_append=False,
                dedup_config=pipeline_config.deduplication,
                llm_client=llm_client,
            )

            final_entities = dedup_entity_nodes
            final_stmt_edges = dedup_stmt_edges
            final_ent_edges = dedup_ent_edges
        else:
            # --- full mode: two-stage dedup ---
            from app.core.memory.storage_services.extraction_engine.deduplication.two_stage_dedup import (
                dedup_layers_and_merge_and_return,
            )

            (
                _dialogue_nodes,
                _chunk_nodes,
                _statement_nodes,
                final_entities,
                _statement_chunk_edges,
                final_stmt_edges,
                final_ent_edges,
                raw_details,
            ) = await dedup_layers_and_merge_and_return(
                dialogue_nodes=[],
                chunk_nodes=[],
                statement_nodes=[],
                entity_nodes=entity_nodes,
                statement_chunk_edges=[],
                statement_entity_edges=statement_entity_edges,
                entity_entity_edges=entity_entity_edges,
                dialog_data_list=dialog_data_list,
                pipeline_config=pipeline_config,
                connector=connector,
                llm_client=llm_client,
            )

        # Parse raw details into structured records
        merge_records, disamb_records, _id_redirect = save_dedup_details(
            raw_details, entity_nodes, final_entities
        )

        logger.info(
            "去重后: %d 个实体节点, %d 条陈述句-实体边, %d 条实体-实体边",
            len(final_entities),
            len(final_stmt_edges),
            len(final_ent_edges),
        )
        logger.info(
            "去重效果: 实体减少 %d, 陈述句-实体边减少 %d, 实体-实体边减少 %d",
            original_entity_count - len(final_entities),
            original_stmt_edge_count - len(final_stmt_edges),
            original_ent_edge_count - len(final_ent_edges),
        )

        # --- progress callbacks ---
        if progress_callback:
            merge_info = analyze_entity_merges(merge_records)
            for i, detail in enumerate(merge_info[:5]):
                dedup_result = {
                    "result_type": "entity_merge",
                    "merged_entity_name": detail["main_entity_name"],
                    "merged_count": detail["merged_count"],
                    "merge_progress": f"{i + 1}/{min(len(merge_info), 5)}",
                    "message": (
                        f"{detail['main_entity_name']}合并{detail['merged_count']}个：相似实体已合并"
                    ),
                }
                await progress_callback("dedup_disambiguation_result", "实体去重中", dedup_result)

            disamb_info = analyze_entity_disambiguation(disamb_records)
            for i, detail in enumerate(disamb_info[:5]):
                disamb_result = {
                    "result_type": "entity_disambiguation",
                    "disambiguated_entity_name": detail["entity_name"],
                    "disambiguation_type": detail["disamb_type"],
                    "confidence": detail.get("confidence", "unknown"),
                    "reason": detail.get("reason", ""),
                    "disamb_progress": f"{i + 1}/{min(len(disamb_info), 5)}",
                    "message": f"{detail['entity_name']}消歧完成：{detail['disamb_type']}",
                }
                await progress_callback("dedup_disambiguation_result", "实体消歧中", disamb_result)

            await send_dedup_progress_callback(
                progress_callback,
                merge_records,
                disamb_records,
                original_entity_count,
                len(final_entities),
                original_stmt_edge_count,
                len(final_stmt_edges),
                original_ent_edge_count,
                len(final_ent_edges),
            )

        return DedupResult(
            entity_nodes=final_entities,
            statement_entity_edges=final_stmt_edges,
            entity_entity_edges=final_ent_edges,
            dedup_details=raw_details,
            merge_records=merge_records,
            disamb_records=disamb_records,
        )

    except Exception as e:
        logger.error("两阶段去重失败: %s", e, exc_info=True)
        raise
