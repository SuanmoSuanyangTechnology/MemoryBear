"""SceneSummary generation and persistence."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from jinja2 import Template

from app.core.config import settings
from app.core.memory.models.graph_models import SceneSummaryNode
from app.core.memory.pipelines.base_pipeline import ModelClientMixin
from app.core.memory.scene.scene_community_resources import (
    load_scene_community_prompt,
)
from app.core.memory.storage.custom.scene_storage import (
    SceneStorage,
)
from app.db import get_db_context
from app.repositories.memory_message_repository import MemoryMessageRepository
from app.schemas.scene_memory_schema import (
    GenerateSceneSummaryTask,
    SceneMessage,
)
from app.schemas.scene_community_schema import SceneValueSummaryOutput
from app.services.memory_config_service import MemoryConfigService


logger = logging.getLogger(__name__)
_P1V_PROMPT = "p1v_scene_value_summary_v1.jinja2"


class SceneSummaryService:
    def __init__(self, *, writer: SceneStorage) -> None:
        self.writer = writer

    @staticmethod
    def _build_clients(memory_config):
        with get_db_context() as db:
            llm = ModelClientMixin.get_llm_client(
                db,
                memory_config.llm_model_id,
                memory_config.tenant_id,
            )
            embedder = ModelClientMixin.get_embedding_client(
                db,
                memory_config.embedding_model_id,
                memory_config.tenant_id,
            )
        return llm, embedder

    async def _generate_content(
        self,
        messages: list[SceneMessage],
        memory_config,
    ) -> tuple[SceneValueSummaryOutput, list[float]]:
        serialized = json.dumps(
            [
                {
                    "role": item.role,
                    "content": item.content,
                    "created_at": item.created_at.isoformat(),
                }
                for item in messages
            ],
            ensure_ascii=False,
        )
        prompt = Template(load_scene_community_prompt(_P1V_PROMPT)).render(
            scene_messages=serialized
        )
        llm, embedder = self._build_clients(memory_config)
        structured = llm.with_structured_output(SceneValueSummaryOutput, strict=True)
        result = await structured.ainvoke(prompt)
        parsed = (
            result
            if isinstance(result, SceneValueSummaryOutput)
            else SceneValueSummaryOutput.model_validate(result)
        )
        embeddings = await embedder.aembed_documents([parsed.summary])
        if not embeddings or not embeddings[0]:
            raise RuntimeError("SceneSummary embedding is empty")
        return parsed, list(embeddings[0])

    async def generate(self, task: GenerateSceneSummaryTask) -> dict:
        with get_db_context() as db:
            config = MemoryConfigService(db).load_memory_config(task.config_id)
            interval = MemoryMessageRepository(db).load_scene_interval(
                end_user_id=task.end_user_id,
                scene_start_message_id=task.scene_start_message_id,
                close_before_message_id=task.close_before_message_id,
                idle_high_watermark_message_id=task.idle_high_watermark_message_id,
                idle_timeout_seconds=config.scene_idle_timeout_seconds,
                max_message_chars=settings.MEMORY_MESSAGE_MAX_CONTENT_CHARS,
                close_reason=task.close_reason,
            )
        if interval is None:
            return {"status": "skipped", "reason": "unstable_or_invalid_interval"}
        messages = [SceneMessage(**row) for row in interval["messages"]]
        total_chars = sum(len(message.content.strip()) for message in messages)
        if total_chars < config.scene_min_chars_to_summary:
            return {"status": "skipped", "reason": "below_min_chars"}

        source_ids = [message.id for message in messages]
        existing_ids = await self.writer.get_scene_summary_source_ids(
            task.scene_start_message_id,
            task.end_user_id,
        )
        if existing_ids is not None:
            if existing_ids != source_ids:
                logger.warning(
                    "[SceneSummary] immutable summary source changed: "
                    "user=%s scene=%s existing_source_count=%s current_source_count=%s",
                    task.end_user_id,
                    task.scene_start_message_id,
                    len(existing_ids),
                    len(source_ids),
                )
            await self.writer.republish_scene_summary(
                task.scene_start_message_id,
                task.end_user_id,
            )
            inactive_count = await self.writer.count_inactive(task.end_user_id)
            return {
                "status": "skipped",
                "reason": "unchanged",
                "summary_id": task.scene_start_message_id,
                "inactive_count": inactive_count,
                "community_dispatch_required": (
                    inactive_count >= int(config.batch_trigger_count)
                ),
            }

        p1v, embedding = await self._generate_content(messages, config)
        now = datetime.now(timezone.utc)
        first, last = messages[0], messages[-1]
        summary = SceneSummaryNode(
            id=task.scene_start_message_id,
            end_user_id=task.end_user_id,
            conversation_id=interval["conversation_id"],
            content=p1v.summary,
            topic_scope=p1v.topic_scope,
            summary_embedding=embedding,
            source_message_ids=source_ids,
            start_message_id=task.scene_start_message_id,
            end_message_id=last.id,
            started_at=first.created_at.replace(tzinfo=timezone.utc),
            ended_at=last.created_at.replace(tzinfo=timezone.utc),
            turn_count=sum(1 for item in messages if item.role == "user"),
            close_reason=interval["close_reason"],
            config_id=task.config_id,
            community_eligibility=p1v.decision,
            community_status=(
                "INACTIVE" if p1v.decision == "ELIGIBLE" else None
            ),
            created_at=now,
            updated_at=now,
        )
        await self.writer.create_scene_summary_if_absent(
            summary.model_dump(exclude_none=True)
        )

        # 长期固化展示事件：摘要成功落库后 best effort 落 PG。
        # 使用内存中已有的 summary 组装，不回查 Neo4j；写入失败只记日志，
        # 不能把已成功的 SceneSummary 改判为失败，也不触发摘要重新生成。
        try:
            from app.services.memory_engine_display_service import (
                MemoryEngineDisplayService,
            )
            await MemoryEngineDisplayService.save_scene_summary_event(
                end_user_id=task.end_user_id,
                summary=summary,
            )
        except Exception as e:
            logger.warning(
                f"[EngineDisplay] 长期固化展示写入异常（不影响主流程）: {e}",
                exc_info=True,
            )

        inactive_count = await self.writer.count_inactive(task.end_user_id)
        logger.info(
            "[SceneSummary] P1V scene=%s decision=%s reason=%s confidence=%s",
            summary.id,
            p1v.decision,
            p1v.reason_code,
            p1v.confidence,
        )
        return {
            "status": "success",
            "summary_id": summary.id,
            "community_eligibility": p1v.decision,
            "inactive_count": inactive_count,
            "community_dispatch_required": (
                inactive_count >= int(config.batch_trigger_count)
            ),
        }
