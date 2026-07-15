"""
Pilot Run Service - 试运行服务

用于执行记忆系统的试运行流程，不保存到 Neo4j。

职责边界：
- QA 消息格式解析、文件 URL 解析、感知记忆生成（persist=False）
- 语义剪枝、语义分块（预处理）
- 调用 PilotWritePipeline 执行萃取链路
- 输出结果文件（含试运行专属的 extracted_result.perceptual 字段注入）

v0.3.13 变更：
- 入参从 dialogue_text: str 改为 messages: List[WriteMessageItem]（与 /write 接口对齐）
- 新增：文件 URL 解析 + 感知记忆内存快照（persist=False）
- SSE `text_preprocessing_result` 的 pruning 子事件字段：deleted_messages → user_message_changes（仅 user 角色）
- SSE 新增 `perceptual_result` 事件（感知记忆汇总，无 files 时也会发出，perceptual_nodes 为空数组）
- `result.extracted_result` 新增 `perceptual` 字段 / 移除 `disambiguation` 字段
"""

import json
import os
import time
from typing import Any, Awaitable, Callable, List, Optional

from app.core.utils.datetime_utils import utcnow_naive
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
from app.schemas.app_schema import FileInput, TransferMethod
from app.schemas.memory_agent_schema import WriteMessageItem
from app.schemas.memory_config_schema import MemoryConfig
from app.services.memory_perceptual_service import _PerceptualSnapshot


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


# ────────────────────────────────────────────────────────────────
# 感知记忆 helper（试运行专用，来自 development.md §4.1.6）
# ────────────────────────────────────────────────────────────────
def _collect_perceptual_nodes_data(
    snapshots: list[tuple[int, "_PerceptualSnapshot"]],
) -> list[dict]:
    """perceptual_result 事件 data.perceptual_nodes 构造。无快照时返回 []。"""
    result: list[dict] = []
    for _, snap in snapshots:
        content = (snap.meta_data or {}).get("content", {}) or {}
        result.append({
            "file_type": snap.perceptual_type.value if snap.perceptual_type else None,
            "file_name": snap.file_name,
            "summary": snap.summary,
            "topic": content.get("topic"),
            "keywords": content.get("keywords", []),
            "domain": content.get("domain"),
        })
    return result


def _build_perceptual_summary(
    snapshots: list[tuple[int, "_PerceptualSnapshot"]],
) -> dict:
    """result.extracted_result.perceptual 字段构造（无快照时返回零统计 + 空数组）。"""
    from collections import Counter
    file_types = [
        snap.perceptual_type.value
        for _, snap in snapshots
        if snap.perceptual_type
    ]
    type_counter = Counter(file_types)
    samples = _collect_perceptual_nodes_data(snapshots)
    return {
        "count": len(snapshots),
        "file_type_distribution": [
            {"file_type": t, "count": c} for t, c in type_counter.items()
        ],
        "samples": samples,   # 字段与 perceptual_result 事件一致
    }


async def _resolve_file_url(file: FileInput) -> Optional[str]:
    """解析 FileInput 为可访问 URL（remote_url 直接返回，local_file 走 MultimodalService.get_file_url）。"""
    if file.transfer_method == TransferMethod.REMOTE_URL:
        return file.url
    # local_file：需要短 session 查 file_metadata 并签名 URL
    from app.db import get_db_read
    from app.services.multimodal_service import MultimodalService
    try:
        with get_db_read() as db:
            mm_service = MultimodalService(db, api_config=None)
            return await mm_service.get_file_url(file)
    except Exception as e:
        logger.warning(f"[PILOT_RUN] 文件 URL 解析失败: file_id={file.upload_file_id}, err={e}")
        return None


async def _generate_perceptual_snapshots(
    memory_config: MemoryConfig,
    messages: List[WriteMessageItem],
) -> list[tuple[int, _PerceptualSnapshot]]:
    """遍历 messages 中的 files，生成感知记忆内存快照（persist=False）。

    返回 (message_index, snapshot) 元组列表，用于后续把 summary 注入回对应 message。
    单条 file 失败不阻断整体流程，仅记录 warning 日志。
    """
    from app.db import get_db_read
    from app.services.memory_perceptual_service import MemoryPerceptualService

    snapshots: list[tuple[int, _PerceptualSnapshot]] = []
    for msg_idx, msg in enumerate(messages):
        if not msg.files:
            continue
        for file in msg.files:
            try:
                resolved_url = await _resolve_file_url(file)
                if not resolved_url:
                    logger.warning(f"[PILOT_RUN] 文件 URL 解析为空，跳过: msg_idx={msg_idx}, file={file}")
                    continue
                file_for_perceptual = file.model_copy(update={"url": resolved_url})

                with get_db_read() as db:
                    perceptual_service = MemoryPerceptualService(db)
                    snapshot = await perceptual_service.generate_perceptual_memory(
                        end_user_id=str(memory_config.workspace_id),
                        memory_config=memory_config,
                        file=file_for_perceptual,
                        content=msg.content,
                        persist=False,
                    )
                if snapshot is None:
                    logger.warning(
                        f"[PILOT_RUN] 感知记忆生成返回 None，跳过: msg_idx={msg_idx}, file={file}"
                    )
                    continue
                snapshots.append((msg_idx, snapshot))
            except Exception as e:
                logger.warning(
                    f"[PILOT_RUN] 文件处理失败，跳过: msg_idx={msg_idx}, err={e}",
                    exc_info=True,
                )
    return snapshots


def _patch_extracted_result_json(perceptual_summary: dict) -> None:
    """在 _write_extracted_result_summary 生成 extracted_result.json 之后，
    注入 perceptual 字段并移除 disambiguation 字段（v0.3.13 契约）。
    """
    result_path = settings.get_memory_output_path("extracted_result.json")
    if not os.path.isfile(result_path):
        logger.warning(f"[PILOT_RUN] extracted_result.json 不存在，跳过 patch: {result_path}")
        return
    try:
        with open(result_path, "r", encoding="utf-8") as rf:
            data = json.load(rf)
        data["perceptual"] = perceptual_summary
        data.pop("disambiguation", None)   # v0.3.13: 消歧下线，安全移除
        with open(result_path, "w", encoding="utf-8") as wf:
            json.dump(data, wf, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"[PILOT_RUN] patch extracted_result.json 失败: {e}", exc_info=True)


async def run_pilot_extraction(
    memory_config: MemoryConfig,
    messages: List[WriteMessageItem],
    llm_client,
    progress_callback: Optional[Callable[[str, str, Optional[dict]], Awaitable[None]]] = None,
    language: str = "zh",
) -> None:
    """执行试运行模式的知识提取流水线（v0.3.13 QA 消息格式）。

    职责：
    1. 文件 URL 解析 + 感知记忆内存快照（persist=False）
    2. 构建 DialogData → 语义剪枝 → 语义分块（预处理，需要 llm_client）
    3. 调用 PilotWritePipeline 执行萃取链路（Pipeline 自行管理客户端）
    4. 将萃取结果写入输出文件，并注入 perceptual 字段 / 移除 disambiguation

    Args:
        memory_config: 从数据库加载的内存配置对象
        messages: QA 格式消息列表（role: user/assistant，files 可选）
        llm_client: 预先初始化的 LLM 客户端（调用方负责在 db session 关闭前完成初始化）
        progress_callback: 可选的进度回调 (stage, message, data)
        language: 语言类型 ("zh" | "en")
    """
    log_file = "logs/time.log"
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    timestamp = utcnow_naive().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"\n=== Pilot Run Started: {timestamp} ===\n")

    pipeline_start = time.time()

    # ── 包装 progress_callback：在 creating_nodes_edges_complete 之后插入 perceptual_result ──
    perceptual_snapshots: list[tuple[int, _PerceptualSnapshot]] = []
    perceptual_emitted = {"done": False}

    async def _wrapped_progress_callback(stage: str, message: str, data: Optional[dict] = None) -> None:
        # 先透传原事件
        if progress_callback:
            await progress_callback(stage, message, data)
        # 在 creating_nodes_edges_complete 之后、dedup 事件之前插入 perceptual_result（幂等）
        if stage == "creating_nodes_edges_complete" and not perceptual_emitted["done"]:
            perceptual_emitted["done"] = True
            perceptual_nodes = _collect_perceptual_nodes_data(perceptual_snapshots)
            if progress_callback:
                await progress_callback(
                    "perceptual_result", "感知记忆提取完成",
                    {"perceptual_nodes": perceptual_nodes},
                )

    try:
        # ── 步骤 1: llm_client 已由调用方在 db session 关闭前初始化传入 ──────
        step_start = time.time()
        log_time("Client Initialization", time.time() - step_start, log_file)

        # ── 步骤 1.5: 文件 URL 解析 + 感知记忆生成（persist=False） ─────────
        step_start = time.time()
        perceptual_snapshots.extend(
            await _generate_perceptual_snapshots(memory_config, messages)
        )
        log_time("Perceptual Snapshot Generation", time.time() - step_start, log_file)

        # 将 file_summary 注入回对应 message.content（对齐 WritePipeline._preprocess_files）
        message_content_map: dict[int, str] = {}
        for msg_idx, snap in perceptual_snapshots:
            original = messages[msg_idx].content or ""
            if snap.summary:
                tag = f"<input-file-summary>{snap.summary}</input-file-summary>"
                if tag not in original:
                    message_content_map[msg_idx] = original + tag
                    continue
            message_content_map.setdefault(msg_idx, original)

        # ── 步骤 2: 构建 DialogData ──────────────────────────────────────────
        step_start = time.time()
        role_map = {"user": "用户", "assistant": "AI"}
        conv_msgs: list[ConversationMessage] = []
        for idx, m in enumerate(messages):
            content = message_content_map.get(idx, m.content or "")
            content = content.strip()
            if not content:
                continue
            conv_msgs.append(
                ConversationMessage(role=role_map.get(m.role, m.role), msg=content)
            )

        if not conv_msgs:
            raise ValueError("messages 为空或所有消息内容为空")

        dialog = DialogData(
            context=ConversationContext(msgs=conv_msgs),
            ref_id="pilot_dialog_1",
            end_user_id=str(memory_config.workspace_id),
            user_id=str(memory_config.tenant_id),
            apply_id=str(memory_config.config_id),
            metadata={"source": "pilot_run", "input_type": "qa_messages"},
        )

        if progress_callback:
            await _wrapped_progress_callback(
                "text_preprocessing", "开始预处理文本（语义剪枝 + 语义分块）...", None
            )

        # ── 步骤 2.1: 语义剪枝 ─────────────────────────────────────────────
        pruned_dialogs = [dialog]
        pruning_stats: dict = {"enabled": False}

        if memory_config.pruning_enabled:
            try:
                from app.core.memory.storage_services.extraction_engine.data_preprocessing.data_pruning import (
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
                # v0.3.13: 只关注 user 角色的剪枝变化（assistant 修改对前端无展示价值）
                original_user_msgs: dict[int, str] = {
                    i: m.msg for i, m in enumerate(dialog.context.msgs) if m.role in ("用户", "user")
                }
                pruned_dialogs = await SemanticPruner(config=config, llm_client=llm_client).prune_dataset([dialog])

                if pruned_dialogs and pruned_dialogs[0].context:
                    pruned_user_map: dict[int, str] = {}
                    pruned_user_msgs = [
                        m for m in pruned_dialogs[0].context.msgs
                        if m.role in ("用户", "user")
                    ]
                    # 按顺序匹配：剩余 user 消息按原次序对应原 user 消息（内容可能被修改）
                    for (orig_idx, _orig_text), pmsg in zip(original_user_msgs.items(), pruned_user_msgs):
                        pruned_user_map[orig_idx] = pmsg.msg

                    user_message_changes = [
                        {
                            "index": idx,
                            "original": original_user_msgs[idx],
                            "pruned": pruned_user_map.get(idx, ""),   # 未保留 → ""
                        }
                        for idx in original_user_msgs
                        if pruned_user_map.get(idx, "") != original_user_msgs[idx]   # 仅保留真变动
                    ]

                    pruning_stats = {
                        "enabled": True,
                        "scene": config.pruning_scene,
                        "threshold": config.pruning_threshold,
                        "changed_count": len(user_message_changes),
                    }
                    logger.info(
                        f"[PILOT_RUN] 语义剪枝完成: "
                        f"原始 user 消息 {len(original_user_msgs)} 条，变化 {len(user_message_changes)} 条"
                    )
                    if progress_callback:
                        await _wrapped_progress_callback(
                            "text_preprocessing_result", "语义剪枝完成",
                            {"type": "pruning", "user_message_changes": user_message_changes},
                        )
                else:
                    logger.warning("[PILOT_RUN] 剪枝后对话为空，使用原始对话")
                    pruned_dialogs = [dialog]

            except Exception as e:
                logger.error(f"[PILOT_RUN] 语义剪枝失败，使用原始对话: {e}", exc_info=True)
                pruned_dialogs = [dialog]
                if progress_callback:
                    await _wrapped_progress_callback(
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
                    await _wrapped_progress_callback(
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
            await _wrapped_progress_callback(
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
            await _wrapped_progress_callback("knowledge_extraction", "正在知识抽取...", None)

        from app.core.memory.pipelines.pilot_write_pipeline import PilotWritePipeline

        pilot_result = await PilotWritePipeline(
            memory_config=memory_config,
            end_user_id=str(memory_config.workspace_id),
            language=language,
            progress_callback=_wrapped_progress_callback,
        ).run(chunked_dialogs)

        log_time("Extraction Pipeline", time.time() - step_start, log_file)

        # ── 步骤 4: 输出结果文件 ────────────────────────────────────────────
        if progress_callback:
            await _wrapped_progress_callback("generating_results", "正在生成结果...", None)

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

        # v0.3.13: 注入 perceptual 字段 / 移除 disambiguation 字段（试运行专属）
        _patch_extracted_result_json(_build_perceptual_summary(perceptual_snapshots))

        logger.info("Pilot run completed: stop after layer-1 dedup (no Neo4j write)")

    except Exception as e:
        logger.error(f"Pilot run failed: {e}", exc_info=True)
        raise

    total_time = time.time() - pipeline_start
    log_time("TOTAL PILOT RUN TIME", total_time, log_file)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"=== Pilot Run Completed: {utcnow_naive().strftime('%Y-%m-%d %H:%M:%S')} ===\n\n")
    logger.info(f"Pilot run complete. Total time: {total_time:.2f}s")
