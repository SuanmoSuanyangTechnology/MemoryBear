"""Generate conversation details with short UoW read/write scopes."""

from src.application.ports.llm import ChatModelFactory
from src.application.ports.prompts import PromptCatalog
from src.application.ports.task_cache import TaskCache
from src.application.ports.uow import UnitOfWork
from src.application.shared.generation import model_for_context, structured_data
from src.application.working_memory.dto import (
    ConversationDetailResult,
    GenerateConversationDetailCommand,
)
from src.application.working_memory.schemas import ConversationDetailData
from src.domain.working_memory.errors import DetailFailure
from src.domain.working_memory.policies import stable


class GenerateConversationDetailUseCase:
    def __init__(
        self, uow: UnitOfWork, models: ChatModelFactory, prompts: PromptCatalog, cache: TaskCache
    ):
        self.uow, self.models, self.prompts, self.cache = uow, models, prompts, cache

    def execute(self, command: GenerateConversationDetailCommand) -> ConversationDetailResult:
        payload = {**command.scope.payload(), "conversation_id": str(command.conversation_id)}
        key = (
            f"memory_jobs:conversation_detail:{command.scope.workspace_id}:"
            f"{command.conversation_id}"
        )
        try:
            with self.cache.lock(key):
                return ConversationDetailResult(self._generate(payload))
        except DetailFailure as exc:
            return ConversationDetailResult({"error": {"kind": exc.kind, "message": str(exc)}})

    def _generate(self, payload):
        with self.uow.read() as repos:
            source = repos.conversation_details.read(payload)
            if source["detail"] is not None and stable(source["updated_at"]):
                return ConversationDetailData.model_validate(source["detail"]).model_dump(
                    mode="json"
                )
            context = repos.conversation_details.generation_context(payload)
        if not context["messages"]:
            return ConversationDetailData(
                theme="", question=[], summary="", takeaways=[], info_score=0
            ).model_dump(mode="json")
        model = model_for_context(self.models, context)
        try:
            result = model.call_structured(
                [
                    {"role": "system", "content": self.prompts.conversation_summary_system()},
                    {
                        "role": "user",
                        "content": self.prompts.conversation_summary_user(
                            language="zh", conversation=str(context["messages"])
                        ),
                    },
                ],
                ConversationDetailData,
            )
            data = structured_data(result, ConversationDetailData)
        except Exception as exc:
            raise DetailFailure("llm_error", "Failed to parse LLM response") from exc
        if not stable(context["updated_at"]):
            with self.uow.transaction() as repos:
                repos.conversation_details.save(payload, context, data)
        return data
