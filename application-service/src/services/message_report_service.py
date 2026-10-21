"""Message report service (frozen port of the monolith ``ReportService``).

Covers the user-facing report submission (with selected-text ranges) and the
operations-side review queue: filtered list, review decisions with their
side effects, and grouped statistics.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from ..errors import BizCode, BusinessException
from ..models.conversation_model import Message
from ..models.message_report_model import MessageReport
from ..utils.datetime_utils import utcnow_naive

logger = logging.getLogger(__name__)


class ReportService:
    """Message report service."""

    def __init__(self, db: Session):
        self.db = db

    def submit_report(
        self,
        message_id: uuid.UUID,
        conversation_id: uuid.UUID,
        workspace_id: uuid.UUID,
        reported_by: uuid.UUID,
        report_type: str,
        report_reason: str | None = None,
        text_start_offset: int | None = None,
        text_end_offset: int | None = None,
        selected_text: str | None = None,
    ) -> dict:
        """Submit a report / selected-text feedback; returns the report id."""

        report = MessageReport(
            message_id=message_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            reported_by=reported_by,
            report_type=report_type,
            report_reason=report_reason,
            text_start_offset=text_start_offset,
            text_end_offset=text_end_offset,
            selected_text=selected_text,
        )
        self.db.add(report)

        message = self.db.get(Message, message_id)
        if message:
            message.report_count += 1

        self.db.commit()
        self.db.refresh(report)

        logger.info(
            "Report submitted",
            extra={
                "report_id": str(report.id),
                "message_id": str(message_id),
                "report_type": report_type,
                "reported_by": str(reported_by),
            },
        )

        self._trigger_auto_audit(report.id)

        return {
            "report_id": str(report.id),
            "status": "pending",
        }

    def _trigger_auto_audit(self, report_id: uuid.UUID):
        """Trigger automatic content audit (not wired to a vendor yet)."""

        logger.debug("Auto audit triggered: %s", report_id)

    def get_reports_for_review(
        self,
        workspace_id: uuid.UUID,
        status: str | None = None,
        severity: str | None = None,
        page: int = 1,
        pagesize: int = 20,
    ) -> tuple[list[MessageReport], int]:
        """Filtered + paginated review queue (newest first); returns (rows, total)."""

        query = self.db.query(MessageReport).filter(
            MessageReport.workspace_id == workspace_id,
        )

        if status:
            query = query.filter(MessageReport.status == status)
        if severity:
            query = query.filter(MessageReport.severity == severity)

        total = query.count()
        reports = query.order_by(
            MessageReport.created_at.desc()
        ).offset((page - 1) * pagesize).limit(pagesize).all()

        return reports, total

    def review_report(
        self,
        report_id: uuid.UUID,
        reviewer_id: uuid.UUID,
        severity: str,
        action_taken: str,
        review_note: str | None = None,
    ) -> dict:
        """Review one report; side effects follow the chosen action."""

        report = self.db.get(MessageReport, report_id)
        if not report:
            raise BusinessException("举报记录不存在", BizCode.NOT_FOUND)

        report.status = "reviewed"
        report.severity = severity
        report.action_taken = action_taken
        report.reviewer_id = reviewer_id
        report.review_note = review_note
        report.reviewed_at = utcnow_naive()

        if action_taken == "content_removed":
            message = self.db.get(Message, report.message_id)
            if message:
                message.is_deleted = True
        elif action_taken == "user_banned":
            # User banning is not wired yet.
            pass

        self.db.commit()

        logger.info(
            "Report reviewed",
            extra={
                "report_id": str(report_id),
                "reviewer_id": str(reviewer_id),
                "severity": severity,
                "action_taken": action_taken,
            },
        )

        return {
            "report_id": str(report_id),
            "status": "reviewed",
            "action_taken": action_taken,
        }

    def get_reports_statistics(
        self,
        workspace_id: uuid.UUID,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> dict:
        """Grouped report statistics with an optional time range."""

        query = self.db.query(MessageReport).filter(
            MessageReport.workspace_id == workspace_id,
        )

        if start_date:
            query = query.filter(MessageReport.created_at >= start_date)
        if end_date:
            query = query.filter(MessageReport.created_at <= end_date)

        reports = query.all()

        type_stats = {}
        severity_stats = {}
        status_stats = {}
        for r in reports:
            type_stats[r.report_type] = type_stats.get(r.report_type, 0) + 1
            if r.severity:
                severity_stats[r.severity] = severity_stats.get(r.severity, 0) + 1
            status_stats[r.status] = status_stats.get(r.status, 0) + 1

        return {
            "total_count": len(reports),
            "by_type": type_stats,
            "by_severity": severity_stats,
            "by_status": status_stats,
            "pending_count": sum(1 for r in reports if r.status == "pending"),
        }


__all__ = ["ReportService"]
