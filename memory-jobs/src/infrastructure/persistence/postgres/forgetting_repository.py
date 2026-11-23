"""Session-bound domain persistence; transaction ownership stays in the use case."""

from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from src.infrastructure.persistence.postgres.task_context_repository import (
    SqlAlchemyTaskContextRepository,
)
from src.models import EndUser
from src.models.forgetting_model import ForgetAudit, ForgetHistory


class SqlAlchemyForgettingRepository:
    def __init__(self, session) -> None:
        self.session = session

    def finish_forgetting(self, context, report, pairs, job_id) -> None:
        when = datetime.fromisoformat(report["start_time"].replace("Z", "+00:00")).replace(
            tzinfo=None
        )
        session = self.session
        SqlAlchemyTaskContextRepository.guard(session, context, require_config=False)
        for pair in pairs:
            self._pair_audit(session, context, pair, job_id, when)
        session.execute(
            insert(ForgetHistory)
            .values(
                id=uuid5(NAMESPACE_URL, f"forget-history:{job_id}"),
                end_user_id=context["end_user_id"],
                execution_time=when,
                merged_count=report["merged_count"],
                failed_count=report["failed_count"],
                total_nodes=report["nodes_after"],
                low_activation_nodes=context["low_activation_nodes"],
                average_activation_value=context["average_activation"],
                duration_seconds=report["duration_seconds"],
                trigger_type="manual",
            )
            .on_conflict_do_nothing(index_elements=[ForgetHistory.id])
        )
        # Count the core quota pool, not auxiliary MemorySummary/Dialogue nodes.
        session.execute(
            update(EndUser)
            .where(EndUser.id == UUID(context["end_user_id"]))
            .values(
                memory_count=context["active_count_after"],
            )
        )

    @staticmethod
    def _pair_audit(session, context, pair, job_id, when):
        for kind, key, content in (
            ("Statement", "statement_id", "statement_text"),
            ("ExtractedEntity", "entity_id", "entity_name"),
        ):
            audit_id = uuid5(NAMESPACE_URL, f"forget:{job_id}:{kind}:{pair[key]}")
            session.execute(
                insert(ForgetAudit)
                .values(
                    id=audit_id,
                    end_user_id=UUID(context["end_user_id"]),
                    node_id=pair[key],
                    node_type=kind,
                    content=pair.get(content) or "",
                    trigger=1,
                    reason="manual forgetting pair fusion",
                    recoverable=False,
                    operator=UUID(context["actor_id"]) if context.get("actor_id") else None,
                    delete_at=when,
                    is_recovered=False,
                )
                .on_conflict_do_nothing(index_elements=[ForgetAudit.id])
            )

    def record_pair(self, context, pair, job_id, when):
        timestamp = datetime.fromisoformat(when.replace("Z", "+00:00")).replace(tzinfo=None)
        session = self.session
        SqlAlchemyTaskContextRepository.guard(session, context, require_config=False)
        self._pair_audit(session, context, pair, job_id, timestamp)
