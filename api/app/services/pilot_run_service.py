"""
Pilot Run Service - 试运行服务

用于执行记忆系统的试运行流程，不保存到 Neo4j。

职责边界：
- 文本解析、语义剪枝、语义分块（预处理）
- 调用 PilotWritePipeline 执行萃取链路
- 输出结果文件
"""

import os
import re
import time
from datetime import datetime
from typing import Awaitable, Callable, Optional

from app.core.config import settings
from app.core.logging_config import get_memory_logger, log_time
from app.core.memory.models.message_models import (
    ConversationContext,
    ConversationMessage,
    DialogData,
)
from app.core.memory.storage_services.extraction_engine.pipeline_help import (
    _write_extracted_result_summary,
    export_test_input_doc,
)
from app.schemas.memory_config_schema import MemoryConfig
from sqlalchemy.orm import Session

logger = get_memory_logger(__name__)


def _save_triplets_from_dialogs(dialog_data_list: list[DialogData], output_path: str) -> None:
    """Write triplet/entity text report compatible with pipeline_help parsers."""
    all_triplets = []
    all_entities = []

    for dialog in dialog_data_list:
        for chunk in getattr(dialog, "chunks", []) or []:
            for statement in getattr(chunk, "statements", []) or []:
                triplet_info = getattr(statement, "triplet_extraction_info", None)
                if not triplet_info:
                    continue
                all_triplets.extend(getattr(triplet_info, "triplets", []) or [])
                all_entities.extend(getattr(triplet_info, "entities", []) or [])

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"=== EXTRACTED TRIPLETS ({len(all_triplets)} total) ===\n\n")
        for i, triplet in enumerate(all_triplets, 1):
            f.write(f"Triplet {i}:\n")
            f.write(f"  Subject: {triplet.subject_name} (ID: {triplet.subject_id})\n")
            f.write(f"  Predicate: {triplet.predicate}\n")
            f.write(f"  Object: {triplet.object_name} (ID: {triplet.object_id})\n")
            value = getattr(triplet, "value", None)
            if value:
                f.write(f"  Value: {value}\n")
            f.write("\n")

        f.write(f"\n=== EXTRACTED ENTITIES ({len(all_entities)} total) ===\n\n")
        for i, entity in enumerate(all_entities, 1):
            f.write(f"Entity {i}:\n")
            f.write(f"  ID: {entity.entity_idx}\n")
            f.write(f"  Name: {entity.name}\n")
            f.write(f"  Type: {entity.type}\n")
            f.write(f"  Description: {entity.description}\n")
            f.write("\n")


async def run_pilot_extraction(
    memory_config: MemoryConfig,
    dialogue_text: str,
    db: Session,
    progress_callback: Optional[Callable[[str, str, Optional[dict]], Awaitable[None]]] = None,
    language: str = "zh",
) -> None:
    """执行试运行模式的知识提取流水线。

    职责：
    1. 文本解析 → 语义剪枝 → 语义分块（预处理，需要 llm_client）
    2. 调用 PilotWritePipeline 执行萃取链路（Pipeline 自行管理客户端）
    3. 将萃取结果写入输出文件

    Args:
        memory_config: 从数据库加载的内存配置对象
        dialogue_text: 输入的对话文本
        db: 数据库会话（用于初始化预处理所需的 LLM 客户端）
        progress_callback: 可选的进度回调 (stage, message, data)
        language: 语言类型 ("zh" | "en")
    """
    log_file = "logs/time.log"
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"\n=== Pilot Run Started: {timestamp} ===\n")

    pipeline_start = time.time()

    try:
        # ── 步骤 1: 初始化预处理所需的 LLM 客户端 ──────────────────────────
        # 只用于语义剪枝和分块，PilotWritePipeline 内部会自行初始化萃取客户端
        step_start = time.time()
        from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
        factory = MemoryClientFactory(db)
        llm_client = factory.get_llm_client(str(memory_config.llm_model_id))
        log_time("Client Initialization", time.time() - step_start, log_file)

        # ── 步骤 2: 文本解析 ────────────────────────────────────────────────
        step_start = time.time()
        pattern = r"(用户|AI)[：:]\s*([^\n]+(?:\n(?!(?:用户|AI)[：:])[^\n]*)*?)"
        matches = re.findall(pattern, dialogue_text, re.MULTILINE | re.DOTALL)
        messages = [
            ConversationMessage(role=r, msg=c.strip())
            for r, c in matches
            if c.strip()
        ]
        if not messages:
            messages = [ConversationMessage(role="用户", msg=dialogue_text.strip())]

        dialog = DialogData(
            context=ConversationContext(msgs=messages),
            ref_id="pilot_dialog_1",
            end_user_id=str(memory_config.workspace_id),
            user_id=str(memory_config.tenant_id),
            apply_id=str(memory_config.config_id),
            metadata={"source": "pilot_run", "input_type": "frontend_text"},
        )

        if progress_callback:
            await progress_callback("text_preprocessing", "开始预处理文本（语义剪枝 + 语义分块）...")

        # ── 步骤 2.1: 语义剪枝 ─────────────────────────────────────────────
        pruned_dialogs = [dialog]
        pruning_stats: dict = {"enabled": False}

        if memory_config.pruning_enabled:
            try:
                from app.core.memory.storage_services.extraction_engine.data_preprocessing import (
                    SemanticPruner,
                )
                from app.core.memory.models.config_models import PruningConfig

                config = PruningConfig(
                    pruning_switch=memory_config.pruning_enabled,
                    pruning_scene=memory_config.pruning_scene,
                    pruning_threshold=memory_config.pruning_threshold,
                    scene_id=str(memory_config.scene_id) if memory_config.scene_id else None,
                    ontology_class_infos=memory_config.ontology_class_infos,
                )
                original_msgs = [{"role": m.role, "content": m.msg} for m in dialog.context.msgs]
                pruned_dialogs = await SemanticPruner(config=config, llm_client=llm_client).prune_dataset([dialog])

                if pruned_dialogs and pruned_dialogs[0].context:
                    remaining = [{"role": m.role, "content": m.msg} for m in pruned_dialogs[0].context.msgs]
                    # 找出被删除的消息（顺序匹配）
                    kept_indices: list[int] = []
                    ri = 0
                    for oi, om in enumerate(original_msgs):
                        if ri < len(remaining) and om == remaining[ri]:
                            kept_indices.append(oi)
                            ri += 1
                    deleted_messages = [
                        {"index": i, "role": m["role"], "content": m["content"]}
                        for i, m in enumerate(original_msgs)
                        if i not in kept_indices
                    ]
                    pruning_stats = {
                        "enabled": True,
                        "scene": config.pruning_scene,
                        "threshold": config.pruning_threshold,
                        "deleted_count": len(deleted_messages),
                    }
                    logger.info(
                        f"[PILOT_RUN] 语义剪枝完成: {len(original_msgs)} → {len(remaining)} 条"
                        f"（删除 {len(deleted_messages)} 条）"
                    )
                    if progress_callback:
                        await progress_callback(
                            "text_preprocessing_result", "语义剪枝完成",
                            {"type": "pruning", "deleted_messages": deleted_messages},
                        )
                else:
                    logger.warning("[PILOT_RUN] 剪枝后对话为空，使用原始对话")
                    pruned_dialogs = [dialog]

            except Exception as e:
                logger.error(f"[PILOT_RUN] 语义剪枝失败，使用原始对话: {e}", exc_info=True)
                pruned_dialogs = [dialog]
                if progress_callback:
                    await progress_callback(
                        "text_preprocessing_result", "语义剪枝失败",
                        {"type": "pruning", "error": str(e), "fallback": "使用原始对话"},
                    )

        # ── 步骤 2.2: 语义分块 ─────────────────────────────────────────────
        from app.core.memory.storage_services.extraction_engine.knowledge_extraction.chunk_extraction import (
            DialogueChunker,
        )
        chunked_dialogs = []
        for dlg in pruned_dialogs:
            dlg.chunks = await DialogueChunker(memory_config.chunker_strategy, llm_client=llm_client).process_dialogue(dlg)
            chunked_dialogs.append(dlg)

        if progress_callback:
            for dlg in chunked_dialogs:
                for i, chunk in enumerate(dlg.chunks or []):
                    await progress_callback(
                        "text_preprocessing_result", f"分块 {i + 1} 处理完成",
                        {
                            "type": "chunking",
                            "chunk_index": i + 1,
                            "content": chunk.content[:200] + "..." if len(chunk.content) > 200 else chunk.content,
                            "full_length": len(chunk.content),
                            "dialog_id": dlg.id,
                            "chunker_strategy": memory_config.chunker_strategy,
                        },
                    )
            await progress_callback(
                "text_preprocessing_complete", "预处理文本完成（剪枝 + 分块）",
                {
                    "total_chunks": sum(len(dlg.chunks or []) for dlg in chunked_dialogs),
                    "total_dialogs": len(chunked_dialogs),
                    "chunker_strategy": memory_config.chunker_strategy,
                    "pruning": pruning_stats,
                },
            )

        log_time("Data Loading & Chunking", time.time() - step_start, log_file)

        # ── 步骤 3: 萃取（PilotWritePipeline 自行管理客户端和本体加载）──────
        step_start = time.time()
        logger.info("Running pilot extraction pipeline...")

        if progress_callback:
            await progress_callback("knowledge_extraction", "正在知识抽取...")

        from app.core.memory.pipelines.pilot_write_pipeline import PilotWritePipeline

        pilot_result = await PilotWritePipeline(
            memory_config=memory_config,
            end_user_id=str(memory_config.workspace_id),
            language=language,
            progress_callback=progress_callback,
        ).run(chunked_dialogs)

        log_time("Extraction Pipeline", time.time() - step_start, log_file)

        # ── 步骤 4: 输出结果文件 ────────────────────────────────────────────
        if progress_callback:
            await progress_callback("generating_results", "正在生成结果...")

        graph = pilot_result.graph
        settings.ensure_memory_output_dir()
        export_test_input_doc(
            entity_nodes=graph.entity_nodes,
            statement_entity_edges=graph.stmt_entity_edges,
            entity_entity_edges=graph.entity_entity_edges,
        )
        _save_triplets_from_dialogs(
            dialog_data_list=pilot_result.dialog_data_list,
            output_path=settings.get_memory_output_path("extracted_triplets.txt"),
        )
        _write_extracted_result_summary(
            chunk_nodes=graph.chunk_nodes,
            pipeline_output_dir=settings.get_memory_output_path(),
        )
        logger.info("Pilot run completed: stop after layer-1 dedup (no Neo4j write)")

    except Exception as e:
        logger.error(f"Pilot run failed: {e}", exc_info=True)
        raise

    total_time = time.time() - pipeline_start
    log_time("TOTAL PILOT RUN TIME", total_time, log_file)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"=== Pilot Run Completed: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n\n")
    logger.info(f"Pilot run complete. Total time: {total_time:.2f}s")
