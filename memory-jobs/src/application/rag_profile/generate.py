"""Generate a RAG profile; only short writes use the Unit of Work."""

import logging
from collections import Counter

from src.application.ports.chat_model import ChatModel
from src.application.ports.llm import ChatModelFactory
from src.application.ports.prompts import PromptCatalog
from src.application.ports.task_cache import TaskCache
from src.application.ports.uow import UnitOfWork
from src.application.rag_profile.dto import GenerateRAGProfileCommand, RAGProfileResult
from src.application.rag_profile.ports import RAGProfileSource
from src.application.rag_profile.schemas import (
    RAGDomain,
    RAGPersonas,
    RAGProfile,
    RAGSummary,
    RAGTags,
)
from src.application.shared.generation import model_for_context, structured_data
from src.domain.rag_profile.policies import insight_sections

logger = logging.getLogger(__name__)


class GenerateRAGProfileUseCase:
    def __init__(
        self,
        uow: UnitOfWork,
        source: RAGProfileSource,
        models: ChatModelFactory,
        prompts: PromptCatalog,
        cache: TaskCache,
    ) -> None:
        self.uow, self.sources, self.models, self.prompts, self.cache = (
            uow,
            source,
            models,
            prompts,
            cache,
        )

    def execute(self, command: GenerateRAGProfileCommand) -> RAGProfileResult:
        with self.uow.read() as repos:
            context = repos.task_context.context(command.scope)
        if context["storage_type"] != "rag":
            raise ValueError("RAG画像生成仅支持 RAG 工作空间")
        key = (
            f"memory_jobs:operation:rag_profile:{context['workspace_id']}:{context['end_user_id']}"
        )
        with self.cache.lock(key):
            result = self._generate(context, command.limit, command.max_tags, command.language)
            self.cache.invalidate(context["end_user_id"], context["workspace_id"])
            return result

    def _generate(self, context, limit: int, maximum: int, language: str) -> RAGProfileResult:
        chunks = self.sources.rag_messages(context["end_user_id"], limit)
        if not chunks:
            raise ValueError("暂无chunk内容，无法生成画像")
        try:
            model = model_for_context(self.models, context)
        except Exception:
            logger.warning("RAG profile model unavailable", exc_info=True)
            profile = RAGProfile.model_validate(
                {
                    "summary": "摘要生成失败",
                    "tags": [],
                    "personas": [],
                    **insight_sections("", language),
                }
            )
        else:
            profile = self._model_profile(model, chunks, maximum, language)
        data = profile.model_dump(mode="json")
        result = {
            "end_user_id": context["end_user_id"],
            "summary_length": len(profile.summary),
            "tags_count": len(profile.tags),
            "personas_count": len(profile.personas),
            "insight_generated": bool(profile.memory_insight),
        }
        with self.uow.transaction() as repos:
            repos.rag_profiles.save(context, data)
        return RAGProfileResult(result)

    def _summarize(self, model: ChatModel, chunks: list[str], language: str) -> str:
        chunk_texts = "\n\n".join(f"片段{i + 1}: {chunk}" for i, chunk in enumerate(chunks))
        prompt = self.prompts.rag_profile_summary(
            chunk_texts=chunk_texts,
            json_schema=RAGSummary.model_json_schema(),
            max_words=200,
            language=language,
        )
        messages = [{"role": "user", "content": prompt}]
        try:
            extracted = structured_data(model.call_structured(messages, RAGSummary), RAGSummary)
        except Exception:
            logger.warning("RAG structured summary failed; trying plain text", exc_info=True)
            try:
                raw = model.invoke(messages)
                return raw.text.strip() if raw is not None else "暂无内容"
            except Exception:
                logger.warning("RAG plain-text summary failed", exc_info=True)
                return "摘要生成失败"
        if extracted["summary"]:
            return extracted["summary"].strip()
        if extracted["statements"]:
            return "；".join(row["statement"] for row in extracted["statements"])
        return "暂无内容"

    def _model_profile(
        self, model: ChatModel, chunks: list[str], maximum: int, language: str
    ) -> RAGProfile:
        summary = self._summarize(model, chunks, language)
        tags: Counter[str] = Counter()
        domains: Counter[str] = Counter()
        for chunk in chunks:
            tag_prompt = self.prompts.rag_profile_tags(chunk=chunk, language=language)
            try:
                tag_data = structured_data(
                    model.call_structured([{"role": "user", "content": tag_prompt}], RAGTags),
                    RAGTags,
                )
                tags.update(tag_data["tags"])
            except Exception:
                logger.warning("RAG chunk tag extraction failed", exc_info=True)

            domain_prompt = self.prompts.rag_profile_domain(chunk=chunk[:500])
            try:
                domain = structured_data(
                    model.call_structured([{"role": "user", "content": domain_prompt}], RAGDomain),
                    RAGDomain,
                )["domain"]
            except Exception:
                logger.warning("RAG chunk domain classification failed", exc_info=True)
                domain = "其他"
            domains[domain] += 1

        persona_prompt = self.prompts.rag_profile_personas(chunks=chunks, language=language)
        try:
            personas = structured_data(
                model.call_structured([{"role": "user", "content": persona_prompt}], RAGPersonas),
                RAGPersonas,
            )["personas"]
        except Exception:
            logger.warning("RAG persona extraction failed", exc_info=True)
            personas = []

        distribution = ", ".join(
            f"{name}({count / len(chunks):.0%})" for name, count in domains.most_common(3)
        )
        insight_prompt = self.prompts.rag_profile_insight(
            domain_distribution=distribution,
            active_periods=None,
            social_connections=None,
            language=language,
        )
        try:
            raw = model.invoke([{"role": "user", "content": insight_prompt}]).text.strip()
        except Exception:
            logger.warning("RAG insight generation failed", exc_info=True)
            raw = ""
        return RAGProfile.model_validate(
            {
                "summary": summary,
                "tags": [
                    {"tag": tag, "frequency": count} for tag, count in tags.most_common(maximum)
                ],
                "personas": personas[:5],
                **insight_sections(raw, language),
            }
        )
