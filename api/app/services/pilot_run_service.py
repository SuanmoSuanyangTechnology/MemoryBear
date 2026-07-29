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
- SSE 剪枝与分块各自拥有完整三联事件：pruning_extract/pruning_result/pruning_complete
  与 chunking_extract/chunking_result/chunking_complete
- SSE 新增 perceptual / extract_statement / extract_triplet 三联事件
- result.extracted_result 新增 perceptual 字段 / 移除 disambiguation 字段
"""

import asyncio
import json
import os
import time
from collections import Counter
from typing import Awaitable, Callable, List, Optional

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
from app.core.utils.datetime_utils import ensure_dialog_at, utcnow_naive
from app.schemas.app_schema import FileInput, TransferMethod
from app.schemas.memory_agent_schema import WriteMessageItem
from app.schemas.memory_config_schema import MemoryConfig
from app.services.memory_perceptual_service import _PerceptualSnapshot
from app.services.multimodal_service import MultimodalService

logger = get_memory_logger(__name__)

# 类型别名，提高可读性
ProgressCallback = Callable[[str, str, Optional[dict]], Awaitable[None]]
PerceptualEntry = tuple[int, _PerceptualSnapshot, str | None, str | None]

# 中英 role 映射（downstream chunker/pipeline 要求 "用户"/"AI"）
_ROLE_TO_CN = {"user": "用户", "assistant": "AI"}


# ════════════════════════════════════════════════════════════════════
# Helper: 进度回调空实现（避免调用方到处 if callback）
# ════════════════════════════════════════════════════════════════════

async def _noop_progress(stage: str, message: str, data: Optional[dict] = None) -> None:
    """空回调，用于 progress_callback 为 None 时的兜底。"""


# ════════════════════════════════════════════════════════════════════
# Helper: 三元组 / 实体文本报告
# ════════════════════════════════════════════════════════════════════

def _save_triplets_from_dialogs(dialog_data_list: list[DialogData], output_path: str) -> None:
    """Write triplet/entity text report compatible with pipeline_help parsers."""
    all_triplets: list = []
    all_entities: list = []

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


# ════════════════════════════════════════════════════════════════════
# Helper: 感知记忆
# ════════════════════════════════════════════════════════════════════

def _collect_perceptual_nodes_data(snapshots: list[PerceptualEntry]) -> list[dict]:
    """构造 perceptual_result 事件 data.perceptual_nodes。无快照时返回 []。"""
    result: list[dict] = []
    for _, snap, file_type, url in snapshots:
        content = (snap.meta_data or {}).get("content", {}) or {}
        result.append({
            "file_type": file_type,
            "file_name": snap.file_name,
            "url": url,
            "summary": snap.summary,
            "topic": content.get("topic"),
            "keywords": content.get("keywords", []),
            "domain": content.get("domain"),
        })
    return result


def _build_perceptual_summary(snapshots: list[PerceptualEntry]) -> dict:
    """构造 result.extracted_result.perceptual 字段（无快照时返回零统计 + 空数组）。"""
    file_types = [ft for _, _, ft, _ in snapshots if ft]
    type_counter = Counter(file_types)
    return {
        "count": len(snapshots),
        "file_type_distribution": [
            {"file_type": t, "count": c} for t, c in type_counter.items()
        ],
        "samples": _collect_perceptual_nodes_data(snapshots),
    }


async def _resolve_file_url(
    file: FileInput,
    mm_service: Optional[MultimodalService] = None,
) -> Optional[str]:
    """解析 FileInput 为可访问 URL。

    - remote_url：直接返回 `file.url`，不需要 DB。
    - local_file：需要走 `MultimodalService.get_file_url()`。调用方应传入共享的
      `mm_service`（复用同一 read session）；未传时按旧路径临时开一次 session。
    """
    if file.transfer_method == TransferMethod.REMOTE_URL:
        return file.url

    try:
        if mm_service is not None:
            return await mm_service.get_file_url(file)

        # 兼容：无共享 service 时临时开一次 session
        from app.db import get_db_read

        with get_db_read() as db:
            return await MultimodalService(db, api_config=None).get_file_url(file)
    except Exception as e:
        logger.warning(f"[PILOT_RUN] 文件 URL 解析失败: file_id={file.upload_file_id}, err={e}")
        return None


async def _generate_perceptual_snapshots(
    memory_config: MemoryConfig,
    messages: List[WriteMessageItem],
) -> list[PerceptualEntry]:
    """遍历 messages 中的 files，生成感知记忆内存快照（persist=False）。

    **涵盖范围**：仅处理 `role == "user"` 的消息。assistant 挂的 files 不生成感知记忆、
    不参与 SSE 事件、不注入到任何 content——与本文件 pilot 阶段的“仅对 user 消息
    执行完整萃取”语义一致（assistant 消息仅作为上下文）。

    优化点：
    1. **共享 session 批解析 URL**：所有 `local_file` 的 URL 解析集中在一个
       `get_db_read()` session 内完成；`remote_url` 无需 DB。
    2. **LLM 调用并发化**：每个 file 的感知记忆生成通过 `asyncio.gather` 并行执行，
       单文件失败不影响其他文件。

    返回 (message_index, snapshot, file_type, resolved_url) 元组列表。
    """
    from app.db import get_db_read
    from app.services.memory_perceptual_service import MemoryPerceptualService

    # ── Phase 1: 批量解析 URL（一次 session 内完成，remote_url 无需 DB）──
    # 注意：仅遍历 user 消息，assistant 消息的 files 直接忽略。
    resolved_files: list[tuple[int, WriteMessageItem, FileInput, str]] = []
    needs_db = any(
        f.transfer_method != TransferMethod.REMOTE_URL
        for msg in messages
        if msg.role == "user"
        for f in (msg.files or [])
    )

    async def _collect_urls(mm_service: Optional[MultimodalService]) -> None:
        for msg_idx, msg in enumerate(messages):
            if msg.role != "user":
                continue  # 仅 user 消息参与感知记忆；assistant files 不处理
            if not msg.files:
                continue
            for file in msg.files:
                url = await _resolve_file_url(file, mm_service)
                if not url:
                    logger.warning(
                        f"[PILOT_RUN] 文件 URL 解析为空，跳过: msg_idx={msg_idx}, file={file}"
                    )
                    continue
                resolved_files.append((msg_idx, msg, file, url))

    if needs_db:
        with get_db_read() as db:
            await _collect_urls(MultimodalService(db, api_config=None))
    else:
        await _collect_urls(None)

    if not resolved_files:
        return []

    # ── Phase 2: 并发生成感知记忆（每个协程独占一次 read session）──
    async def _snapshot_one(
        msg_idx: int,
        msg: WriteMessageItem,
        file: FileInput,
        url: str,
    ) -> Optional[PerceptualEntry]:
        try:
            file_for_perceptual = file.model_copy(update={"url": url})
            with get_db_read() as db:
                snapshot = await MemoryPerceptualService(db).generate_perceptual_memory(
                    end_user_id=str(memory_config.workspace_id),
                    memory_config=memory_config,
                    file=file_for_perceptual,
                    content=msg.content,
                    persist=False,
                )
        except Exception as e:
            logger.warning(
                f"[PILOT_RUN] 文件处理失败，跳过: msg_idx={msg_idx}, err={e}",
                exc_info=True,
            )
            return None
        if snapshot is None:
            logger.warning(
                f"[PILOT_RUN] 感知记忆生成返回 None，跳过: msg_idx={msg_idx}, file={file}"
            )
            return None
        return (msg_idx, snapshot, file.file_type, url)

    results = await asyncio.gather(
        *(_snapshot_one(*args) for args in resolved_files),
        return_exceptions=False,  # 单任务已 try/except，不会外抛
    )
    # 按原 message 顺序（同 msg 下按 files 顺序）返回
    return [r for r in results if r is not None]


# ════════════════════════════════════════════════════════════════════
# Helper: 结果文件 patch
# ════════════════════════════════════════════════════════════════════

def _patch_extracted_result_json(perceptual_summary: dict) -> None:
    """注入 perceptual 字段并移除 disambiguation 字段"""
    result_path = settings.get_memory_output_path("extracted_result.json")
    if not os.path.isfile(result_path):
        logger.warning(f"[PILOT_RUN] extracted_result.json 不存在，跳过 patch: {result_path}")
        return
    try:
        with open(result_path, "r", encoding="utf-8") as rf:
            data = json.load(rf)
        data["perceptual"] = perceptual_summary
        data.pop("disambiguation", None)
        with open(result_path, "w", encoding="utf-8") as wf:
            json.dump(data, wf, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"[PILOT_RUN] patch extracted_result.json 失败: {e}", exc_info=True)


# ════════════════════════════════════════════════════════════════════
# Helper: 语义剪枝子流程
# ════════════════════════════════════════════════════════════════════

async def _run_pruning(
    dialog: DialogData,
    memory_config: MemoryConfig,
    llm_client,
    emit: ProgressCallback,
) -> tuple[list[DialogData], dict]:
    """执行语义剪枝，返回 (pruned_dialogs, pruning_stats)。

    stats.status 取值：
    - `success`      剪枝成功，已产出 diff
    - `empty_fallback` 剪枝后为空，使用原始对话
    - `failed`       剪枝执行异常，使用原始对话
    - `disabled`     未启用（当前函数不会产生此值，由上游控制）

    enabled 始终反映配置开关（与 status 解耦），避免“启用但失败”被误读成“未启用”。
    """
    from app.core.memory.models.config_models import PruningConfig
    from app.core.memory.storage_services.extraction_engine.data_preprocessing.data_pruning import (
        SemanticPruner,
    )

    await emit("pruning_extract", "开始语义剪枝", None)

    config = PruningConfig(
        pruning_switch=memory_config.pruning_enabled,
        pruning_scene=memory_config.pruning_scene,
        pruning_threshold=memory_config.pruning_threshold,
        scene_id=str(memory_config.scene_id) if memory_config.scene_id else None,
        ontology_class_infos=memory_config.ontology_class_infos,
    )

    # 记录原始 user 消息用于 diff
    original_user_msgs: dict[int, str] = {
        i: m.msg for i, m in enumerate(dialog.context.msgs) if m.role == "user"
    }

    try:
        pruned_dialogs = await SemanticPruner(config=config, llm_client=llm_client).prune_dataset([dialog])

        if not (pruned_dialogs and pruned_dialogs[0].context):
            logger.warning("[PILOT_RUN] 剪枝后对话为空，使用原始对话")
            fallback_stats = {
                "enabled": True,
                "status": "empty_fallback",
                "scene": config.pruning_scene,
                "threshold": config.pruning_threshold,
                "changed_count": 0,
            }
            await emit(
                "pruning_result", "语义剪枝结果",
                {"user_message_changes": [], "fallback": "剪枝后为空，使用原始对话"},
            )
            await emit("pruning_complete", "语义剪枝完成", fallback_stats)
            return [dialog], fallback_stats

        # 构建 user 消息 diff
        pruned_user_msgs = [m for m in pruned_dialogs[0].context.msgs if m.role == "user"]
        pruned_user_map: dict[int, str] = {}
        for (orig_idx, _), pmsg in zip(original_user_msgs.items(), pruned_user_msgs):
            pruned_user_map[orig_idx] = pmsg.msg

        user_message_changes = [
            {"index": idx, "original": original_user_msgs[idx], "pruned": pruned_user_map.get(idx, "")}
            for idx in original_user_msgs
            if pruned_user_map.get(idx, "") != original_user_msgs[idx]
        ]

        pruning_stats = {
            "enabled": True,
            "status": "success",
            "scene": config.pruning_scene,
            "threshold": config.pruning_threshold,
            "changed_count": len(user_message_changes),
        }
        logger.info(
            f"[PILOT_RUN] 语义剪枝完成: "
            f"原始 user 消息 {len(original_user_msgs)} 条，变化 {len(user_message_changes)} 条"
        )
        await emit("pruning_result", "语义剪枝结果", {"user_message_changes": user_message_changes})
        await emit("pruning_complete", "语义剪枝完成", pruning_stats)
        return pruned_dialogs, pruning_stats

    except Exception as e:
        logger.error(f"[PILOT_RUN] 语义剪枝失败，使用原始对话: {e}", exc_info=True)
        failed_stats = {
            "enabled": True,
            "status": "failed",
            "scene": config.pruning_scene,
            "threshold": config.pruning_threshold,
            "changed_count": 0,
            "error": str(e),
        }
        await emit("pruning_result", "语义剪枝失败", {"error": str(e), "fallback": "使用原始对话"})
        await emit("pruning_complete", "语义剪枝完成", failed_stats)
        return [dialog], failed_stats


# ═════════════════════════════════════════════════════════════════
# Helper: user-only 拆分（user 为抽取目标，assistant 为上下文）
# ═════════════════════════════════════════════════════════════════

def _split_user_and_context(dialog: DialogData) -> DialogData:
    """将对话拆分为“user 抽取目标” + “全对话上下文”。

    拆分语义对齐 write_pipeline：user 消息参与完整萃取链路（分块 -> 陈述句 ->
    三元组），assistant 消息仅作为 supporting_context 供陈述句抽取时的代词/背景解析。

    具体处理：
    - `dialog.context.msgs` 仅保留 `role == "user"` 的消息，且 role 就地改为中文 "用户"
      （对齐 chunker 内部 `Chunk(content=f"{role}: ...", speaker=role)` 的中文 prompt 约束）。
    - `dialog.metadata["supporting_context"] = {"before_msgs": [全对话中文化 MessageItem], "after_msgs": []}`，
      全部放到 before_msgs（与 orchestrator fallback “全对话已经发生”的语义一致）。
    - `dialog.content` 保持不变（上游未赋值，且 orchestrator 优先读 metadata）。

    前置条件：`dialog.context.msgs` 内容为英文 role（"user"/"assistant"），与前置
    剪枝阶段的 SemanticPruner 配对预期一致。

    Raises:
        ValueError: 拆分后 user 消息为空（无抽取目标）。
    """
    from app.core.memory.storage_services.extraction_engine.steps.schema.extraction_step_schema import (
        MessageItem,
    )

    if not (dialog.context and dialog.context.msgs):
        raise ValueError("pilot run 拆分失败：dialog.context.msgs 为空")

    original_msgs = list(dialog.context.msgs)

    # 构建“全对话上下文”（保序，user+assistant 交错，中文 role）
    before_msgs = [
        MessageItem(
            role=_ROLE_TO_CN.get(m.role, m.role),
            msg=m.msg,
        )
        for m in original_msgs
        if (m.msg or "").strip()
    ]

    # 提取 user 消息，就地改中文 role
    user_msgs = [m for m in original_msgs if m.role == "user"]
    if not user_msgs:
        raise ValueError("pilot run 拆分失败：对话中不存在 user 消息，无可抽取目标")
    for m in user_msgs:
        m.role = _ROLE_TO_CN.get(m.role, m.role)  # "user" -> "用户"

    dialog.context.msgs = user_msgs
    if dialog.metadata is None:
        dialog.metadata = {}
    dialog.metadata["supporting_context"] = {
        "before_msgs": before_msgs,
        "after_msgs": [],
    }
    logger.info(
        f"[PILOT_RUN] user-only 拆分完成：user={len(user_msgs)}, "
        f"context_msgs={len(before_msgs)}"
    )
    return dialog


# ════════════════════════════════════════════════════════════════════
# Helper: 语义分块子流程
# ════════════════════════════════════════════════════════════════════

async def _run_chunking(
    pruned_dialogs: list[DialogData],
    memory_config: MemoryConfig,
    llm_client,
    emit: ProgressCallback,
) -> list[DialogData]:
    """对剪枝后的对话列表执行语义分块，返回 chunked_dialogs。"""
    from app.core.memory.storage_services.extraction_engine.knowledge_extraction.chunk_extraction import (
        DialogueChunker,
    )

    await emit("chunking_extract", "开始分块", {"chunker_strategy": memory_config.chunker_strategy})

    chunker = DialogueChunker(memory_config.chunker_strategy, llm_client=llm_client)
    chunked_dialogs: list[DialogData] = []
    for dlg in pruned_dialogs:
        dlg.chunks = await chunker.process_dialogue(dlg)
        chunked_dialogs.append(dlg)

    # 逐块汇报进度
    for dlg in chunked_dialogs:
        for i, chunk in enumerate(dlg.chunks or []):
            await emit(
                "chunking_result", f"分块 {i + 1} 处理完成",
                {
                    "chunk_index": i + 1,
                    "content": chunk.content[:200] + "..." if len(chunk.content) > 200 else chunk.content,
                    "full_length": len(chunk.content),
                    "dialog_id": dlg.id,
                    "chunker_strategy": memory_config.chunker_strategy,
                },
            )

    await emit(
        "chunking_complete", "分块完成",
        {
            "total_chunks": sum(len(dlg.chunks or []) for dlg in chunked_dialogs),
            "total_dialogs": len(chunked_dialogs),
            "chunker_strategy": memory_config.chunker_strategy,
        },
    )
    return chunked_dialogs


# ════════════════════════════════════════════════════════════════════
# 主入口
# ════════════════════════════════════════════════════════════════════

async def run_pilot_extraction(
    memory_config: MemoryConfig,
    messages: List[WriteMessageItem],
    llm_client,
    progress_callback: Optional[ProgressCallback] = None,
    language: str = "zh",
) -> None:
    """执行试运行模式的知识提取流水线（v0.3.13 QA 消息格式）。

    流程：
    1. 文件 URL 解析 + 感知记忆内存快照（persist=False）
    2. 构建 DialogData → 语义剪枝 → 语义分块
    3. 调用 PilotWritePipeline 执行萃取链路
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

    # 统一使用 emit，避免到处判断 callback 是否为 None
    emit: ProgressCallback = progress_callback or _noop_progress
    pipeline_start = time.time()

    try:
        # ── 步骤 1: 文件 URL 解析 + 感知记忆生成（persist=False） ─────────
        step_start = time.time()
        await emit("perceptual_extract", "开始提取感知记忆", None)

        perceptual_snapshots = await _generate_perceptual_snapshots(memory_config, messages)

        await emit(
            "perceptual_result", "感知记忆结果",
            {"perceptual_nodes": _collect_perceptual_nodes_data(perceptual_snapshots)},
        )
        await emit("perceptual_complete", "感知记忆提取完成", {"count": len(perceptual_snapshots)})
        log_time("Perceptual Snapshot Generation", time.time() - step_start, log_file)

        # 将 file_summary 注入回对应 message.content（对齐 WritePipeline._preprocess_files）
        message_content_map: dict[int, str] = {}
        for msg_idx, snap, _ft, _url in perceptual_snapshots:
            original = messages[msg_idx].content or ""
            tag = f"<input-file-summary>{snap.summary}</input-file-summary>"
            if snap.summary and tag not in original:
                message_content_map[msg_idx] = original + tag
            else:
                message_content_map.setdefault(msg_idx, original)

        # ── 步骤 2: 构建 DialogData（保留英文 role 以兼容 SemanticPruner 配对逻辑）──
        step_start = time.time()
        conv_msgs: list[ConversationMessage] = []
        for idx, m in enumerate(messages):
            content = message_content_map.get(idx, m.content or "").strip()
            if not content:
                continue
            conv_msgs.append(
                ConversationMessage(
                    role=m.role,
                    msg=content,
                    dialog_at=ensure_dialog_at(m.dialog_at),
                )
            )

        if not conv_msgs:
            raise ValueError("messages 为空或所有消息内容为空")

        # 前置校验：pilot 阶段仅对 user 消息执行完整萃取，必须存在至少一条 user 消息。
        if not any(m.role == "user" for m in conv_msgs):
            raise ValueError("messages 中不存在 user 消息，pilot run 无可萃取目标")

        dialog = DialogData(
            context=ConversationContext(msgs=conv_msgs),
            ref_id="pilot_dialog_1",
            end_user_id=str(memory_config.workspace_id),
            user_id=str(memory_config.tenant_id),
            apply_id=str(memory_config.config_id),
            metadata={"source": "pilot_run", "input_type": "qa_messages"},
        )

        # ── 步骤 2.1: 语义剪枝 ────────────────────────────────────────────
        if memory_config.pruning_enabled:
            pruned_dialogs, _ = await _run_pruning(dialog, memory_config, llm_client, emit)
        else:
            pruned_dialogs = [dialog]

        # ── 步骤 2.1.5: user-only 拆分（user=抽取目标 / 全对话=上下文）────────
        # 语义对齐 write_pipeline：仅 user 参与完整萃取链路；assistant 进 supporting_context。
        # 剪枝完后可能把 user 删空（剪枝失败 fallback 回退到原 dialog，empty_fallback 也走原 dialog），
        # 在 helper 内部也会再次校验且抛 ValueError。
        for pruned in pruned_dialogs:
            _split_user_and_context(pruned)

        # ── 步骤 2.2: 语义分块 ────────────────────────────────────────────
        chunked_dialogs = await _run_chunking(pruned_dialogs, memory_config, llm_client, emit)
        log_time("Data Loading & Chunking", time.time() - step_start, log_file)

        # ── 步骤 3: 萃取 ──────────────────────────────────────────────────
        step_start = time.time()
        logger.info("Running pilot extraction pipeline...")

        from app.core.memory.pipelines.pilot_write_pipeline import PilotWritePipeline

        pilot_result = await PilotWritePipeline(
            memory_config=memory_config,
            end_user_id=str(memory_config.workspace_id),
            language=language,
            progress_callback=emit,
        ).run(chunked_dialogs)

        log_time("Extraction Pipeline", time.time() - step_start, log_file)

        # ── 步骤 4: 输出结果文件 ──────────────────────────────────────────
        await emit("generating_results", "正在生成结果...", None)

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
