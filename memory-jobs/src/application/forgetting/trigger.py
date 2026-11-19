"""Manual forgetting: graph fusion and explicit short PG transactions."""

import logging
from datetime import UTC, datetime

from src.application.forgetting.dto import ForgettingResult, TriggerForgettingCommand
from src.application.forgetting.ports import ForgettingSource
from src.application.forgetting.schemas import FusionSummary
from src.application.ports.llm import ChatModelFactory
from src.application.ports.prompts import PromptCatalog
from src.application.ports.task_cache import TaskCache
from src.application.ports.uow import UnitOfWork
from src.application.shared.generation import model_for_context, structured_data
from src.domain.forgetting.policies import unique_pairs

logger = logging.getLogger(__name__)


class TriggerForgettingUseCase:
    def __init__(
        self,
        uow: UnitOfWork,
        source: ForgettingSource,
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

    def execute(self, command: TriggerForgettingCommand) -> ForgettingResult:
        with self.uow.read() as repos:
            context = repos.task_context.context(command.scope)
        if context["storage_type"] != "neo4j":
            raise ValueError("该任务仅支持 Neo4j 工作空间")
        key = f"memory_jobs:operation:forgetting:{context['workspace_id']}:{context['end_user_id']}"
        with self.cache.lock(key):
            try:
                return self._forget(context, command)
            finally:
                try:
                    self.cache.invalidate(context["end_user_id"], context["workspace_id"])
                except Exception:
                    logger.warning("forgetting cache invalidation failed", exc_info=True)

    def _forget(self, context, command: TriggerForgettingCommand) -> ForgettingResult:
        maximum = command.max_merge_batch_size
        days = command.min_days_since_access
        if not 1 <= maximum <= 1000 or not 1 <= days <= 365:
            raise ValueError("invalid forgetting limits")
        uid = context["end_user_id"]
        start_time = datetime.now(UTC)
        nodes_before = self.sources.knowledge_count(uid)
        pairs = self.sources.forgetting_pairs(uid, context["forgetting_threshold"], days, maximum)
        unique = unique_pairs(pairs, maximum)
        model = None
        successful = []
        failed = 0
        for pair in unique:
            text = f"{pair['entity_name']}: {pair.get('statement_text') or ''}"
            summary = {"content": text, "title": "未命名", "episodic_type": "conversation"}
            if context["enable_llm_summary"]:
                if model is None:
                    model = model_for_context(self.models, context)
                prompt = self.prompts.forgetting_summary(pair=pair)
                summary = structured_data(
                    model.call_structured([{"role": "user", "content": prompt}], FusionSummary),
                    FusionSummary,
                )
            # Infrastructure/commit uncertainty must fail the task, not become a
            # successful report that would hide reconciliation work.
            with self.uow.read() as repos:
                repos.task_context.check(context)
            if self.sources.merge_pair(context, pair, summary, command.attempt_token):
                with self.uow.transaction() as repos:
                    repos.forgetting.record_pair(
                        context, pair, command.attempt_token, start_time.isoformat()
                    )
                successful.append(pair)
            else:
                failed += 1
        after = self.sources.knowledge_count(uid)
        now = datetime.now(UTC)
        before = nodes_before
        count = len(successful)
        report = {
            "merged_count": count,
            "nodes_before": before,
            "nodes_after": after,
            "reduction_rate": (before - after) / before if before else 0.0,
            "duration_seconds": (now - start_time).total_seconds(),
            "start_time": start_time.isoformat().replace("+00:00", "Z"),
            "end_time": now.isoformat().replace("+00:00", "Z"),
            "failed_count": failed,
            "success_rate": count / (count + failed) if count + failed else 1.0,
        }
        context["active_count_after"] = self.sources.active_count(uid)
        context.update(self.sources.activation_stats(uid, context["forgetting_threshold"]))
        with self.uow.transaction() as repos:
            repos.forgetting.finish_forgetting(context, report, successful, command.attempt_token)
        return ForgettingResult(report)
