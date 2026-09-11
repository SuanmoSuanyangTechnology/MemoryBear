"""SceneSummary generation and persistence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Template

from app.core.config import settings
from app.core.memory.models.graph_models import SceneSummaryNode
from app.core.memory.pipelines.base_pipeline import ModelClientMixin
from app.core.memory.storage.enums import MemoryNodeType
from app.core.memory.storage.service import MemoryStorageService
from app.db import get_db_context
from app.repositories.memory_message_repository import MemoryMessageRepository
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.repositories.neo4j.scene_summary_repository import SceneSummaryRepository
from app.schemas.scene_memory_schema import (
    GenerateSceneSummaryTask,
    SceneMessage,
    SceneSummaryContent,
)
from app.services.memory_config_service import MemoryConfigService

_PROMPT_PATH = Path(__file__).parents[1] / "utils/prompt/prompts/scene_summary_detail.jinja2"


class SceneSummaryService:
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
    ) -> tuple[str, list[float]]:
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
        prompt = Template(_PROMPT_PATH.read_text(encoding="utf-8")).render(
            scene_messages=serialized
        )
        llm, embedder = self._build_clients(memory_config)
        structured = llm.with_structured_output(SceneSummaryContent, strict=True)
        result = await structured.ainvoke(prompt)
        parsed = (
            result
            if isinstance(result, SceneSummaryContent)
            else SceneSummaryContent.model_validate(result)
        )
        embedding = await embedder.aembed_query(parsed.content)
        if not embedding:
            raise RuntimeError("SceneSummary embedding is empty")
        return parsed.content, list(embedding)

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
        connector = Neo4jConnector()
        try:
            repo = SceneSummaryRepository(connector)
            existing_ids = await repo.get_source_message_ids(task.scene_start_message_id)
            if existing_ids == source_ids:
                return {"status": "skipped", "reason": "unchanged"}

            content, embedding = await self._generate_content(messages, config)
            now = datetime.now(timezone.utc)
            first, last = messages[0], messages[-1]
            summary = SceneSummaryNode(
                id=task.scene_start_message_id,
                end_user_id=task.end_user_id,
                conversation_id=interval["conversation_id"],
                content=content,
                summary_embedding=embedding,
                source_message_ids=source_ids,
                start_message_id=task.scene_start_message_id,
                end_message_id=last.id,
                started_at=first.created_at.replace(tzinfo=timezone.utc),
                ended_at=last.created_at.replace(tzinfo=timezone.utc),
                turn_count=sum(1 for item in messages if item.role == "user"),
                close_reason=interval["close_reason"],
                config_id=task.config_id,
                created_at=now,
                updated_at=now,
            )
            storage = await MemoryStorageService.create_graph_write_only()
            try:
                result = await storage.save_node(
                    MemoryNodeType.SCENE_SUMMARY,
                    summary.model_dump(),
                )
                if result.affected_count != 1 or result.ids != [summary.id]:
                    raise RuntimeError("SceneSummary storage write returned no row")
                summary_id = result.ids[0]
                return {"status": "success", "summary_id": summary_id}
            finally:
                await storage.close()
        finally:
            await connector.close()
