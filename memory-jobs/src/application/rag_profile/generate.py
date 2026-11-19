"""Generate a RAG profile; only short writes use the Unit of Work."""

from collections import Counter

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
        model = model_for_context(self.models, context)
        chunk_texts = "\n\n".join(f"片段{i + 1}: {chunk}" for i, chunk in enumerate(chunks))
        prompt = self.prompts.rag_profile_summary(
            chunk_texts=chunk_texts,
            json_schema=RAGSummary.model_json_schema(),
            max_words=200,
            language=language,
        )
        extracted = structured_data(
            model.call_structured([{"role": "user", "content": prompt}], RAGSummary), RAGSummary
        )
        summary = (extracted.get("summary") or "").strip() or "\n".join(
            row["statement"] for row in extracted["statements"]
        )
        tags, domains = Counter(), Counter()
        for chunk in chunks:
            tag_data = structured_data(
                model.call_structured(
                    [
                        {
                            "role": "user",
                            "content": self.prompts.rag_profile_tags(
                                chunk=chunk, language=language
                            ),
                        }
                    ],
                    RAGTags,
                ),
                RAGTags,
            )
            tags.update(tag_data["tags"])
            domain = structured_data(
                model.call_structured(
                    [
                        {
                            "role": "user",
                            "content": self.prompts.rag_profile_domain(chunk=chunk[:500]),
                        }
                    ],
                    RAGDomain,
                ),
                RAGDomain,
            )
            domains[domain["domain"]] += 1
        personas = structured_data(
            model.call_structured(
                [
                    {
                        "role": "user",
                        "content": self.prompts.rag_profile_personas(
                            chunks=chunks, language=language
                        ),
                    }
                ],
                RAGPersonas,
            ),
            RAGPersonas,
        )
        distribution = ", ".join(
            f"{name}({count / len(chunks):.0%})" for name, count in domains.most_common(3)
        )
        prompt = self.prompts.rag_profile_insight(
            domain_distribution=distribution,
            active_periods=None,
            social_connections=None,
            language=language,
        )
        raw = model.invoke([{"role": "user", "content": prompt}]).text.strip()
        sections = insight_sections(raw, language)
        profile = RAGProfile.model_validate(
            {
                "summary": summary,
                "tags": [
                    {"tag": tag, "frequency": count} for tag, count in tags.most_common(maximum)
                ],
                "personas": personas["personas"][:5],
                **sections,
            }
        ).model_dump(mode="json")
        result = {
            "end_user_id": context["end_user_id"],
            "summary_length": len(profile["summary"]),
            "tags_count": len(profile["tags"]),
            "personas_count": len(profile["personas"]),
            "insight_generated": bool(profile["memory_insight"]),
        }
        with self.uow.transaction() as repos:
            repos.rag_profiles.save(context, profile)
        return RAGProfileResult(result)
