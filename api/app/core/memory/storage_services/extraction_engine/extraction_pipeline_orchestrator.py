"""Refactored ExtractionOrchestrator using the unified ExtractionStep paradigm.

This module provides ``NewExtractionOrchestrator`` — a slimmed-down orchestrator
(~500 lines vs ~2500) that delegates extraction work to concrete ExtractionStep
instances and uses SidecarStepFactory for hot-pluggable sidecar modules.

The new orchestrator coexists with the legacy ``ExtractionOrchestrator`` until
the team explicitly switches over.

Execution phases:
    1. Statement extraction + concurrent chunk/dialog embedding
    2. Triplet extraction + concurrent after_statement sidecars + statement embedding
    3. Entity embedding + concurrent after_triplet sidecars
    4. Data assignment back to dialog_data_list
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.core.memory.models.message_models import DialogData
from app.core.memory.models.variate_config import ExtractionPipelineConfig

from .steps.base import ExtractionStep, StepContext
from .steps.embedding_step import EmbeddingStep
from .sidecar_factory import SidecarStepFactory, SidecarTiming
from .steps.statement_temporal_step import StatementTemporalExtractionStep
from .steps.triplet_step import TripletExtractionStep
from .steps.schema import (
    EmbeddingStepInput,
    EmbeddingStepOutput,
    EmotionStepInput,
    EmotionStepOutput,
    MessageItem,
    StatementStepInput,
    StatementStepOutput,
    SupportingContext,
    TripletStepInput,
    TripletStepOutput,
)

logger = logging.getLogger(__name__)


class NewExtractionOrchestrator:
    """Slimmed-down extraction orchestrator using the ExtractionStep paradigm.

    Responsibilities:
        * Initialise all steps and sidecar groups via ``SidecarStepFactory``
        * Route data between stages (``_convert_to_*`` helpers)
        * Orchestrate concurrent execution (``_run_with_sidecars``)
        * Assign extracted results back to ``DialogData`` objects

    The orchestrator does **not** own dedup, node/edge creation, or Neo4j writes.
    Those remain in ``WritePipeline`` / ``dedup_step``.
    """

    def __init__(
        self,
        llm_client: Any,
        embedder_client: Any,
        config: Optional[ExtractionPipelineConfig] = None,
        embedding_id: Optional[str] = None,
        ontology_types: Any = None,
        language: str = "zh",
        is_pilot_run: bool = False,
        progress_callback: Optional[
            Callable[[str, str, Optional[Dict[str, Any]]], Awaitable[None]]
        ] = None,
    ) -> None:
        self.config = config or ExtractionPipelineConfig()
        self.is_pilot_run = is_pilot_run
        self.embedding_id = embedding_id
        self.progress_callback = progress_callback

        # Build shared context for all LLM-based steps
        self.context = StepContext(
            llm_client=llm_client,
            language=language,
            config=self.config,
            is_pilot_run=is_pilot_run,
            progress_callback=progress_callback,
        )

        # ── Critical (main-line) steps ──
        self.statement_temporal_step = StatementTemporalExtractionStep(self.context)
        self.triplet_step = TripletExtractionStep(
            self.context, ontology_types=ontology_types
        )

        # ── Embedding step (non-LLM, separate client) ──
        self.embedding_step = EmbeddingStep(
            embedder_client=embedder_client,
            is_pilot_run=is_pilot_run,
        )

        # ── Sidecar steps (auto-discovered via @register decorator) ──
        sidecar_groups = SidecarStepFactory.create_sidecars(self.config, self.context)
        self.after_statement_sidecars: List[ExtractionStep] = sidecar_groups[
            SidecarTiming.AFTER_STATEMENT
        ]
        self.after_triplet_sidecars: List[ExtractionStep] = sidecar_groups[
            SidecarTiming.AFTER_TRIPLET
        ]

        logger.debug(
            "NewExtractionOrchestrator initialised — "
            "after_statement sidecars: %d, after_triplet sidecars: %d",
            len(self.after_statement_sidecars),
            len(self.after_triplet_sidecars),
        )

    # ──────────────────────────────────────────────
    # 1. 并发执行引擎
    #    负责主线路 + 旁路的安全并发调度
    # ──────────────────────────────────────────────

    @staticmethod
    async def _run_sidecar_safe(
        step: ExtractionStep, input_data: Any
    ) -> Any:
        """Run a sidecar step, returning its default output on failure."""
        try:
            return await step.run(input_data)
        except Exception as exc:
            logger.warning(
                "Sidecar '%s' raised during gather — using default output: %s",
                step.name,
                exc,
            )
            return step.get_default_output()

    async def _run_with_sidecars(
        self,
        critical_coro: Any,
        sidecars: List[Tuple[ExtractionStep, Any]],
        extra_coros: Optional[List[Any]] = None,
    ) -> Tuple[Any, List[Any], List[Any]]:
        """Run a critical coroutine concurrently with sidecar steps.

        Args:
            critical_coro: The awaitable for the critical (main-line) step.
            sidecars: List of ``(step, input_data)`` pairs for sidecar steps.
            extra_coros: Additional non-sidecar coroutines to run concurrently
                (e.g. embedding generation).

        Returns:
            A 3-tuple of:
                * The critical step result (exception propagated if it fails).
                * A list of sidecar results (default outputs on failure).
                * A list of extra coroutine results (empty list if none).

        Raises:
            Exception: If the critical coroutine fails, the exception propagates.
        """
        sidecar_coros = [
            self._run_sidecar_safe(step, inp) for step, inp in sidecars
        ]
        extra = extra_coros or []

        # Gather everything concurrently
        all_coros = [critical_coro] + sidecar_coros + extra
        results = await asyncio.gather(*all_coros, return_exceptions=True)

        # Unpack: first result is critical, then sidecars, then extras
        critical_result = results[0]
        n_sidecars = len(sidecar_coros)
        sidecar_results = list(results[1 : 1 + n_sidecars])
        extra_results = list(results[1 + n_sidecars :])

        # Critical step failure → propagate
        if isinstance(critical_result, BaseException):
            raise critical_result

        # Sidecar failures should already be handled by _run_sidecar_safe,
        # but guard against unexpected exceptions from gather
        for i, res in enumerate(sidecar_results):
            if isinstance(res, BaseException):
                step = sidecars[i][0]
                logger.warning(
                    "Sidecar '%s' unexpected exception in gather: %s",
                    step.name,
                    res,
                )
                sidecar_results[i] = step.get_default_output()

        # Extra coroutine failures → log and replace with None
        for i, res in enumerate(extra_results):
            if isinstance(res, BaseException):
                logger.warning("Extra coroutine %d failed: %s", i, res)
                extra_results[i] = None

        return critical_result, sidecar_results, extra_results

    # ──────────────────────────────────────────────
    # 2. 阶段间数据转换
    #    将上一阶段的 StepOutput 转换为下一阶段的 StepInput
    # ──────────────────────────────────────────────

    @staticmethod
    def _build_supporting_context(
        dialog: DialogData,
    ) -> SupportingContext:
        """Build a SupportingContext from a dialog for pronoun resolution.

        Priority:
            1. If ``dialog.metadata["supporting_context"]`` exists (injected by
               the sliding-window write path via ``get_chunked_dialogs``), use
               the structured ``List[MessageItem]`` directly.
            2. Otherwise fall back to wrapping ``dialog.content`` as a single
               ``MessageItem(role="context", ...)`` for backward compatibility
               with non-sliding-window call paths.
        """
        # Prefer structured context injected by the sliding-window write path
        if dialog.metadata and "supporting_context" in dialog.metadata:
            return SupportingContext(msgs=dialog.metadata["supporting_context"])

        # Fallback: legacy string-concatenation path
        msgs: List[MessageItem] = []
        if hasattr(dialog, "content") and dialog.content:
            # dialog.content is the raw conversation string; wrap as single msg
            msgs.append(MessageItem(role="context", msg=dialog.content))
        return SupportingContext(msgs=msgs)
    
    @staticmethod
    def _convert_to_triplet_input(
        stmt_out: StatementStepOutput,
        supporting_context: SupportingContext,
    ) -> TripletStepInput:
        """Convert a StatementStepOutput into a TripletStepInput."""
        return TripletStepInput(
            statement_id=stmt_out.statement_id,
            statement_text=stmt_out.statement_text,
            statement_type=stmt_out.statement_type,
            temporal_type=stmt_out.temporal_type,
            supporting_context=supporting_context,
            speaker=stmt_out.speaker,
            dialog_at=stmt_out.dialog_at or "",
            valid_at=stmt_out.valid_at,
            invalid_at=stmt_out.invalid_at,
            has_unsolved_reference=stmt_out.has_unsolved_reference,
        )

    @staticmethod
    def _convert_to_emotion_input(
        stmt_out: StatementStepOutput,
    ) -> EmotionStepInput:
        """Convert a StatementStepOutput into an EmotionStepInput."""
        return EmotionStepInput(
            statement_id=stmt_out.statement_id,
            statement_text=stmt_out.statement_text,
            speaker=stmt_out.speaker,
        )

    # ──────────────────────────────────────────────
    # 3. 流水线执行入口
    #    公开接口 run() → 分发到 pilot / full 模式
    # ──────────────────────────────────────────────

    async def run(
        self,
        dialog_data_list: List[DialogData],
    ) -> List[DialogData]:
        """Run the full extraction pipeline on *dialog_data_list*.

        Returns the mutated *dialog_data_list* with extracted data assigned
        to each statement (triplets, temporal info, emotions, embeddings).

        The orchestrator does NOT create graph nodes/edges or run dedup —
        those responsibilities remain in WritePipeline.
        """
        mode = "pilot" if self.is_pilot_run else "full"
        logger.info(
            "Starting extraction pipeline (%s mode), %d dialogs",
            mode,
            len(dialog_data_list),
        )

        if self.is_pilot_run:
            return await self._run_pilot(dialog_data_list)
        return await self._run_full(dialog_data_list)

    # ── 3a. 试运行模式：仅 statement + triplet，不生成 embedding 和旁路 ──

    async def _run_pilot(
        self, dialog_data_list: List[DialogData]
    ) -> List[DialogData]:
        """Pilot mode: statement + triplet extraction only, no sidecars or embeddings."""
        # Phase 1: Statement extraction (chunk-level parallel)
        logger.debug("Pilot phase 1/2: Statement extraction")
        all_stmt_results = await self._extract_all_statements(dialog_data_list)

        # Phase 2: Triplet extraction (statement-level parallel)
        logger.debug("Pilot phase 2/2: Triplet extraction")
        all_triplet_results = await self._extract_all_triplets(
            dialog_data_list, all_stmt_results
        )

        # Assign results back to dialog_data_list
        self._assign_results(
            dialog_data_list,
            all_stmt_results,
            all_triplet_results,
            emotion_results={},
            embedding_output=None,
        )

        # Store raw step outputs for snapshot/debugging
        self._last_stage_outputs = {
            "statement_results": all_stmt_results,
            "triplet_results": all_triplet_results,
            "emotion_results": {},
            "embedding_output": None,
        }

        if self.progress_callback:
            statements_count = sum(
                len(stmts)
                for chunk_stmts in all_stmt_results.values()
                for stmts in chunk_stmts.values()
            )
            entities_count = sum(
                len(t_out.entities)
                for stmt_triplets in all_triplet_results.values()
                for t_out in stmt_triplets.values()
            )
            triplets_count = sum(
                len(t_out.triplets)
                for stmt_triplets in all_triplet_results.values()
                for t_out in stmt_triplets.values()
            )
            await self.progress_callback(
                "knowledge_extraction_complete",
                "知识抽取完成",
                {
                    "entities_count": entities_count,
                    "statements_count": statements_count,
                    "temporal_ranges_count": 0,
                    "triplets_count": triplets_count,
                },
            )

        logger.debug("Pilot extraction complete")
        return dialog_data_list

    # ── 3b. 正式模式：四阶段并发执行 ──

    async def _run_full(
        self, dialog_data_list: List[DialogData]
    ) -> List[DialogData]:
        """Full mode: all four phases with concurrent sidecars and embeddings."""

        # ── Phase 1: Statement extraction + chunk/dialog embedding ──
        logger.debug("Phase 1/4: Statement extraction + chunk/dialog embedding")
        chunk_dialog_emb_input = self._build_chunk_dialog_embedding_input(
            dialog_data_list
        )

        stmt_coro = self._extract_all_statements(dialog_data_list)
        emb_coro = self.embedding_step.run(chunk_dialog_emb_input)

        phase1_results = await asyncio.gather(
            stmt_coro, emb_coro, return_exceptions=True
        )

        all_stmt_results: Dict[str, Dict[str, List[StatementStepOutput]]] = (
            phase1_results[0]
            if not isinstance(phase1_results[0], BaseException)
            else {}
        )
        if isinstance(phase1_results[0], BaseException):
            raise phase1_results[0]

        chunk_dialog_emb: Optional[EmbeddingStepOutput] = (
            phase1_results[1]
            if not isinstance(phase1_results[1], BaseException)
            else None
        )
        if isinstance(phase1_results[1], BaseException):
            logger.warning("Chunk/dialog embedding failed: %s", phase1_results[1])

        # ── Phase 2: Triplet extraction + after_statement sidecars + statement embedding ──
        logger.debug(
            "Phase 2/4: Triplet extraction + sidecars + statement embedding"
        )
        stmt_emb_input = self._build_statement_embedding_input(
            dialog_data_list, all_stmt_results
        )

        # Build sidecar inputs for after_statement sidecars (emotion excluded — async Celery)
        sidecar_pairs = self._build_after_statement_sidecar_inputs(
            dialog_data_list, all_stmt_results
        )

        triplet_coro = self._extract_all_triplets(
            dialog_data_list, all_stmt_results
        )
        stmt_emb_coro = self.embedding_step.run(stmt_emb_input)

        triplet_results, sidecar_results, extra_results = (
            await self._run_with_sidecars(
                triplet_coro,
                sidecar_pairs,
                extra_coros=[stmt_emb_coro],
            )
        )
        all_triplet_results = triplet_results
        stmt_emb: Optional[EmbeddingStepOutput] = (
            extra_results[0] if extra_results else None
        )

        # Collect sidecar outputs keyed by step name
        sidecar_steps = [step for step, _inp in sidecar_pairs]
        sidecar_output_map = self._collect_sidecar_outputs(
            sidecar_steps, sidecar_results
        )

        # ── Phase 3: Entity embedding + after_triplet sidecars ──
        logger.debug("Phase 3/4: Entity embedding + after_triplet sidecars")
        entity_emb_input = self._build_entity_embedding_input(all_triplet_results)

        after_triplet_pairs: List[Tuple[ExtractionStep, Any]] = []
        # Future after_triplet sidecars would be wired here

        entity_emb_coro = self.embedding_step.run(entity_emb_input)

        if after_triplet_pairs:
            _, at_sidecar_results, at_extra = await self._run_with_sidecars(
                entity_emb_coro,
                after_triplet_pairs,
            )
            entity_emb = at_extra[0] if at_extra else None
        else:
            # No after_triplet sidecars — just run embedding
            entity_emb_result = await entity_emb_coro
            entity_emb = (
                entity_emb_result
                if not isinstance(entity_emb_result, BaseException)
                else None
            )

        # Merge all embedding outputs
        merged_emb = self._merge_embeddings(chunk_dialog_emb, stmt_emb, entity_emb)

        # ── Phase 4: Data assignment ──
        logger.debug("Phase 4/4: Data assignment")

        self._assign_results(
            dialog_data_list,
            all_stmt_results,
            all_triplet_results,
            emotion_results={},
            embedding_output=merged_emb,
        )

        # ── Fire-and-forget: collect statements for async emotion extraction ──
        self._emotion_statements: List[Dict[str, str]] = []
        if self.config.emotion_enabled:
            self._emotion_statements = self._collect_emotion_statements(all_stmt_results)

        # Store raw step outputs for snapshot/debugging
        self._last_stage_outputs = {
            "statement_results": all_stmt_results,
            "triplet_results": all_triplet_results,
            "emotion_results": {},
            "embedding_output": merged_emb,
        }

        logger.debug("Full extraction pipeline complete")
        return dialog_data_list

    @property
    def last_stage_outputs(self) -> Dict[str, Any]:
        """Return the raw step outputs from the last run for snapshot/debugging."""
        return getattr(self, "_last_stage_outputs", {})

    # ──────────────────────────────────────────────
    # 4. 萃取执行器
    #    chunk 级并行 statement 提取、statement 级并行 triplet 提取
    # ──────────────────────────────────────────────

    async def _extract_all_statements(
        self,
        dialog_data_list: List[DialogData],
    ) -> Dict[str, Dict[str, List[StatementStepOutput]]]:
        """Extract statements from all chunks across all dialogs (chunk-level parallel).

        Returns:
            Nested dict: ``{dialog_id: {chunk_id: [StatementStepOutput, ...]}}``
        """
        # Collect all (chunk, metadata) pairs
        tasks: List[Any] = []
        task_meta: List[Tuple[str, str, str, SupportingContext]] = []

        for dialog in dialog_data_list:
            ctx = self._build_supporting_context(dialog)
            dialogue_content = (
                dialog.content
                if getattr(
                    self.config, "statement_extraction", None
                )
                and getattr(
                    self.config.statement_extraction,
                    "include_dialogue_context",
                    True,
                )
                else None
            )
            for chunk in dialog.chunks:
                # 仅跳过明确标记为 assistant 的 chunk；speaker=None（混合分块）正常处理。
                chunk_speaker = getattr(chunk, "speaker", None)
                if chunk_speaker == "assistant":
                    continue
                inp = StatementStepInput(
                    chunk_id=chunk.id,
                    end_user_id=dialog.end_user_id,
                    target_content=chunk.content,
                    target_message_date=str(
                        getattr(dialog, "created_at", "") or ""
                    ),
                    dialog_at=getattr(chunk, "dialog_at", "") or "",
                    supporting_context=ctx,
                )
                tasks.append(self.statement_temporal_step.run(inp))
                task_meta.append(
                    (dialog.id, chunk.id, chunk_speaker, ctx)
                )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Organise into nested dict
        stmt_map: Dict[str, Dict[str, List[StatementStepOutput]]] = {}
        for i, result in enumerate(results):
            dialog_id, chunk_id, speaker, _ = task_meta[i]
            if dialog_id not in stmt_map:
                stmt_map[dialog_id] = {}

            if isinstance(result, BaseException):
                logger.error("Statement extraction failed for chunk %s: %s", chunk_id, result)
                stmt_map[dialog_id][chunk_id] = []
            else:
                # Override speaker from chunk metadata
                stmts: List[StatementStepOutput] = result if isinstance(result, list) else []
                for s in stmts:
                    s.speaker = speaker
                stmt_map[dialog_id][chunk_id] = stmts
                if self.progress_callback:
                    # Frontend consumes knowledge_extraction_result with data.statement.
                    # Emit one event per statement to keep payload contract simple.
                    for s in stmts:
                        await self.progress_callback(
                            "knowledge_extraction_result",
                            "知识抽取中",
                            {"statement": s.statement_text},
                        )

        return stmt_map

    async def _extract_all_triplets(
        self,
        dialog_data_list: List[DialogData],
        all_stmt_results: Dict[str, Dict[str, List[StatementStepOutput]]],
    ) -> Dict[str, Dict[str, TripletStepOutput]]:
        """Extract triplets for every statement (statement-level parallel).

        Returns:
            Nested dict: ``{dialog_id: {statement_id: TripletStepOutput}}``
        """
        tasks: List[Any] = []
        task_meta: List[Tuple[str, str]] = []  # (dialog_id, statement_id)

        for dialog in dialog_data_list:
            ctx = self._build_supporting_context(dialog)
            chunk_stmts = all_stmt_results.get(dialog.id, {})
            for _chunk_id, stmts in chunk_stmts.items():
                for stmt in stmts:
                    # 防御性过滤：跳过明确标记为 assistant 的 statement。
                    # speaker=None（混合分块）正常处理。
                    if getattr(stmt, "speaker", None) == "assistant":
                        continue
                    inp = self._convert_to_triplet_input(stmt, ctx)
                    tasks.append(self.triplet_step.run(inp))
                    task_meta.append((dialog.id, stmt.statement_id))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        triplet_map: Dict[str, Dict[str, TripletStepOutput]] = {}
        for i, result in enumerate(results):
            dialog_id, stmt_id = task_meta[i]
            if dialog_id not in triplet_map:
                triplet_map[dialog_id] = {}

            if isinstance(result, BaseException):
                logger.error(
                    "Triplet extraction failed for statement %s: %s",
                    stmt_id,
                    result,
                )
                triplet_map[dialog_id][stmt_id] = self.triplet_step.get_default_output()
            else:
                triplet_map[dialog_id][stmt_id] = result
                if self.progress_callback:
                    await self.progress_callback(
                        "extract_triplet_result",
                        f"statement {stmt_id} 提取完成",
                        {
                            "statement_id": stmt_id,
                            "triplet_count": len(result.triplets),
                            "entity_count": len(result.entities),
                            "triplets": [
                                {
                                    "subject_name": t.subject_name,
                                    "predicate": t.predicate,
                                    "object_name": t.object_name,
                                }
                                for t in result.triplets[:5]
                            ],
                        },
                    )

        return triplet_map

    # ──────────────────────────────────────────────
    # 5. Embedding 输入构建器
    #    为不同阶段构建 EmbeddingStepInput（chunk/statement/entity）
    # ──────────────────────────────────────────────

    @staticmethod
    def _build_chunk_dialog_embedding_input(
        dialog_data_list: List[DialogData],
    ) -> EmbeddingStepInput:
        """Build embedding input for chunks and dialogs (phase 1)."""
        chunk_texts: Dict[str, str] = {}
        dialog_texts: List[str] = []

        for dialog in dialog_data_list:
            if hasattr(dialog, "content") and dialog.content:
                dialog_texts.append(dialog.content)
            for chunk in dialog.chunks:
                chunk_texts[chunk.id] = chunk.content

        return EmbeddingStepInput(
            chunk_texts=chunk_texts,
            dialog_texts=dialog_texts,
        )

    @staticmethod
    def _build_statement_embedding_input(
        dialog_data_list: List[DialogData],
        all_stmt_results: Dict[str, Dict[str, List[StatementStepOutput]]],
    ) -> EmbeddingStepInput:
        """Build embedding input for statements (phase 2)."""
        stmt_texts: Dict[str, str] = {}
        for _dialog_id, chunk_stmts in all_stmt_results.items():
            for _chunk_id, stmts in chunk_stmts.items():
                for s in stmts:
                    stmt_texts[s.statement_id] = s.statement_text
        return EmbeddingStepInput(statement_texts=stmt_texts)

    @staticmethod
    def _build_entity_embedding_input(
        all_triplet_results: Dict[str, Dict[str, TripletStepOutput]],
    ) -> EmbeddingStepInput:
        """Build embedding input for entities (phase 3)."""
        entity_names: Dict[str, str] = {}
        entity_descs: Dict[str, str] = {}
        seen: set = set()

        for _dialog_id, stmt_triplets in all_triplet_results.items():
            for _stmt_id, triplet_out in stmt_triplets.items():
                for ent in triplet_out.entities:
                    key = f"{ent.entity_idx}_{ent.name}"
                    if key not in seen:
                        seen.add(key)
                        entity_names[key] = ent.name
                        entity_descs[key] = ent.description

        return EmbeddingStepInput(
            entity_names=entity_names,
            entity_descriptions=entity_descs,
        )

    # ──────────────────────────────────────────────
    # 6. 旁路输入构建与结果收集
    #    为 after_statement / after_triplet 旁路构建输入，合并 embedding 输出
    # ──────────────────────────────────────────────

    def _build_after_statement_sidecar_inputs(
        self,
        dialog_data_list: List[DialogData],
        all_stmt_results: Dict[str, Dict[str, List[StatementStepOutput]]],
    ) -> List[Tuple[ExtractionStep, Any]]:
        """Build (step, input) pairs for after_statement sidecars.

        Emotion extraction is excluded here — it runs asynchronously via Celery.
        """
        if not self.after_statement_sidecars:
            return []

        # Collect all user statements for sidecar processing
        all_user_stmts: List[StatementStepOutput] = []
        for _dialog_id, chunk_stmts in all_stmt_results.items():
            for _chunk_id, stmts in chunk_stmts.items():
                for s in stmts:
                    if s.speaker == "user":
                        all_user_stmts.append(s)

        pairs: List[Tuple[ExtractionStep, Any]] = []
        for sidecar in self.after_statement_sidecars:
            if sidecar.name == "emotion_extraction":
                # Skip — emotion is dispatched as async Celery task after Phase 4
                continue
            # Generic sidecar: pass first statement as representative input
            if all_user_stmts:
                inp = self._convert_to_emotion_input(all_user_stmts[0])
                pairs.append((sidecar, inp))

        return pairs

    @staticmethod
    def _collect_sidecar_outputs(
        sidecars: List[ExtractionStep],
        results: List[Any],
    ) -> Dict[str, Any]:
        """Map sidecar results by step name."""
        output: Dict[str, Any] = {}
        for i, sidecar in enumerate(sidecars):
            if i < len(results):
                output[sidecar.name] = results[i]
        return output

    @staticmethod
    def _merge_embeddings(
        chunk_dialog: Optional[EmbeddingStepOutput],
        statement: Optional[EmbeddingStepOutput],
        entity: Optional[Any],
    ) -> Optional[EmbeddingStepOutput]:
        """Merge partial embedding outputs into a single EmbeddingStepOutput."""
        merged = EmbeddingStepOutput()
        if chunk_dialog:
            merged.chunk_embeddings = chunk_dialog.chunk_embeddings
            merged.dialog_embeddings = chunk_dialog.dialog_embeddings
        if statement:
            merged.statement_embeddings = statement.statement_embeddings
        if entity and isinstance(entity, EmbeddingStepOutput):
            merged.entity_embeddings = entity.entity_embeddings
        return merged

    # ──────────────────────────────────────────────
    # 6.5 异步情绪提取调度
    #     收集 user statement，fire-and-forget 发送 Celery task
    # ──────────────────────────────────────────────

    def _collect_emotion_statements(
        self,
        all_stmt_results: Dict[str, Dict[str, List[StatementStepOutput]]],
    ) -> List[Dict[str, str]]:
        """Collect user statements for async emotion extraction.

        Only includes statements where has_emotional_state=True,
        indicating the statement reflects user's emotional state.

        Returns a list of dicts ready to be sent as Celery task payload.
        """
        statements_payload: List[Dict[str, str]] = []
        for _dialog_id, chunk_stmts in all_stmt_results.items():
            for _chunk_id, stmts in chunk_stmts.items():
                for s in stmts:
                    if s.speaker == "user" and s.has_emotional_state:
                        statements_payload.append({
                            "statement_id": s.statement_id,
                            "statement_text": s.statement_text,
                            "speaker": s.speaker,
                        })
        return statements_payload

    @property
    def emotion_statements(self) -> List[Dict[str, str]]:
        """Statements collected for async emotion extraction after last run."""
        return getattr(self, "_emotion_statements", [])

    # ──────────────────────────────────────────────
    # 7. 数据赋值
    #    将各阶段 StepOutput 组装为 Statement 对象，替换 chunk.statements
    # ──────────────────────────────────────────────
    # TODO 乐力齐 函数内容密集较长，需要优化
    def _assign_results(
        self,
        dialog_data_list: List[DialogData],
        all_stmt_results: Dict[str, Dict[str, List[StatementStepOutput]]],
        all_triplet_results: Dict[str, Dict[str, TripletStepOutput]],
        emotion_results: Dict[str, EmotionStepOutput],
        embedding_output: Optional[EmbeddingStepOutput],
    ) -> None:
        """Assign extraction results back to dialog_data_list in-place.

        Replaces chunk.statements with new Statement objects built from step
        outputs, because the new orchestrator generates its own statement IDs
        that don't match the original chunk statement IDs.
        """
        from app.core.memory.models.message_models import (
            Statement,
            TemporalValidityRange,
        )
        from app.core.memory.models.triplet_models import (
            TripletExtractionResponse,
            Entity as TripletEntity,
            Triplet as TripletRelation,
        )
        from app.core.memory.utils.data.ontology import (
            RelevenceInfo,
            StatementType,
            TemporalInfo,
        )

        # Map string values to enums
        _STMT_TYPE_MAP = {
            "FACT": StatementType.FACT,
            "OPINION": StatementType.OPINION,
            "PREDICTION": StatementType.PREDICTION,
            "SUGGESTION": StatementType.SUGGESTION,
        }
        _TEMPORAL_MAP = {
            "STATIC": TemporalInfo.STATIC,
            "DYNAMIC": TemporalInfo.DYNAMIC,
            "ATEMPORAL": TemporalInfo.ATEMPORAL,
        }

        total_stmts = 0
        assigned_triplets = 0
        assigned_emotions = 0
        assigned_stmt_emb = 0
        assigned_chunk_emb = 0
        assigned_dialog_emb = 0

        for dialog in dialog_data_list:
            dialog_stmts = all_stmt_results.get(dialog.id, {})
            dialog_triplets = all_triplet_results.get(dialog.id, {})

            # Assign dialog embedding
            if embedding_output and embedding_output.dialog_embeddings:
                idx = dialog_data_list.index(dialog)
                if idx < len(embedding_output.dialog_embeddings):
                    dialog.dialog_embedding = embedding_output.dialog_embeddings[idx]
                    assigned_dialog_emb += 1

            for chunk in dialog.chunks:
                # Assign chunk embedding
                if embedding_output and chunk.id in embedding_output.chunk_embeddings:
                    chunk.chunk_embedding = embedding_output.chunk_embeddings[chunk.id]
                    assigned_chunk_emb += 1

                # Build new Statement objects from step outputs
                chunk_stmt_outputs = dialog_stmts.get(chunk.id, [])
                new_statements = []

                for stmt_out in chunk_stmt_outputs:
                    total_stmts += 1

                    # Temporal validity
                    valid_at = stmt_out.valid_at if stmt_out.valid_at != "NULL" else None
                    invalid_at = stmt_out.invalid_at if stmt_out.invalid_at != "NULL" else None

                    # Triplet info
                    triplet_info = None
                    triplet_out = dialog_triplets.get(stmt_out.statement_id)
                    if triplet_out and (triplet_out.entities or triplet_out.triplets):
                        entities = [
                            TripletEntity(
                                entity_idx=e.entity_idx,
                                name=e.name,
                                type=e.type,
                                type_id=e.type_id,
                                type_description=getattr(e, "type_description", ""),
                                description=e.description,
                                is_explicit_memory=e.is_explicit_memory,
                            )
                            for e in triplet_out.entities
                        ]
                        triplets = [
                            TripletRelation(
                                subject_name=t.subject_name,
                                subject_id=t.subject_id,
                                predicate=t.predicate,
                                predicate_id=t.predicate_id,
                                predicate_surface=t.predicate_surface,
                                predicate_description=getattr(t, "predicate_description", ""),
                                object_name=t.object_name,
                                object_id=t.object_id,
                            )
                            for t in triplet_out.triplets
                        ]
                        triplet_info = TripletExtractionResponse(
                            entities=entities, triplets=triplets,
                        )
                        assigned_triplets += 1

                    # Emotion info
                    emo = emotion_results.get(stmt_out.statement_id)
                    emotion_kwargs = {}
                    if emo:
                        emotion_kwargs = {
                            "emotion_type": emo.emotion_type,
                            "emotion_intensity": emo.emotion_intensity,
                            "emotion_keywords": emo.emotion_keywords,
                        }
                        assigned_emotions += 1

                    # Statement embedding
                    stmt_embedding = None
                    if (
                        embedding_output
                        and stmt_out.statement_id in embedding_output.statement_embeddings
                    ):
                        stmt_embedding = embedding_output.statement_embeddings[stmt_out.statement_id]
                        assigned_stmt_emb += 1

                    # Build the Statement object that _create_nodes_and_edges expects
                    stmt = Statement(
                        id=stmt_out.statement_id,
                        chunk_id=chunk.id,
                        end_user_id=dialog.end_user_id,
                        statement=stmt_out.statement_text,
                        speaker=stmt_out.speaker,
                        stmt_type=_STMT_TYPE_MAP.get(stmt_out.statement_type, StatementType.FACT),
                        temporal_info=_TEMPORAL_MAP.get(stmt_out.temporal_type, TemporalInfo.ATEMPORAL),
                        # relevence_info=RelevenceInfo.RELEVANT if stmt_out.relevance == "RELEVANT" else RelevenceInfo.IRRELEVANT,
                        temporal_validity=TemporalValidityRange(valid_at=valid_at, invalid_at=invalid_at),
                        has_unsolved_reference=stmt_out.has_unsolved_reference,
                        has_emotional_state=stmt_out.has_emotional_state,
                        triplet_extraction_info=triplet_info,
                        statement_embedding=stmt_embedding,
                        dialog_at=getattr(chunk, "dialog_at", None),
                        **emotion_kwargs,
                    )
                    new_statements.append(stmt)

                # Replace chunk.statements with newly built objects
                chunk.statements = new_statements

        logger.info(
            "Data assignment complete — statements: %d, triplets: %d, "
            "emotions: %d, stmt_emb: %d, chunk_emb: %d, dialog_emb: %d",
            total_stmts,
            assigned_triplets,
            assigned_emotions,
            assigned_stmt_emb,
            assigned_chunk_emb,
            assigned_dialog_emb,
        )
