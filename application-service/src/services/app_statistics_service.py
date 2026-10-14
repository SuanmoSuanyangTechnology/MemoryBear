"""App statistics service (mirrors the monolith behavior).

Window conversion, query semantics, and response shapes match the monolith
``AppStatisticsService`` (app statistics 8-key dict / merged-sorted workspace
statistics). Cross-domain tables (``api_keys`` / ``api_key_logs`` /
``model_usage_records``) are read through the read-only exit
``cross_domain_read`` (same guard / quota semantics); own tables
(``conversations`` / ``end_users``) are queried directly via the ORM.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Date, and_, cast, func
from sqlalchemy.orm import Session

from ..models.conversation_model import Conversation
from ..models.end_user_model import EndUser
from ..repositories import cross_domain_read

# App types (agent, multi_agent, workflow) — host ApiKeyType values; services counted separately
APP_KEY_TYPES = ("agent", "multi_agent", "workflow")
SERVICE_KEY_TYPES = ("service",)


class AppStatisticsService:
    """App statistics service."""

    def __init__(self, db: Session):
        self.db = db

    def get_app_statistics(
        self,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        tenant_id: uuid.UUID,
        start_date: int,
        end_date: int
    ) -> dict[str, Any]:
        """Get app statistics.

        Args:
            app_id: App ID
            workspace_id: Workspace ID
            tenant_id: Tenant ID (token figures are isolated per tenant)
            start_date: Start timestamp (milliseconds)
            end_date: End timestamp (milliseconds)

        Returns:
            Statistics dict
        """
        # Convert millisecond timestamps to datetime
        start_dt = datetime.fromtimestamp(start_date / 1000)
        end_dt = datetime.fromtimestamp(end_date / 1000) + timedelta(days=1)

        # 1. Conversation statistics
        conversations_stats = self._get_conversations_statistics(
            app_id, workspace_id, start_dt, end_dt
        )

        # 2. New-user statistics
        users_stats = self._get_new_users_statistics(app_id, start_dt, end_dt)

        # 3. API-call statistics
        api_stats = self._get_api_calls_statistics(app_id, start_dt, end_dt)

        # 4. Token-usage statistics
        token_stats = self._get_token_statistics(tenant_id, app_id, start_dt, end_dt)

        return {
            "daily_conversations": conversations_stats["daily"],
            "total_conversations": conversations_stats["total"],
            "daily_new_users": users_stats["daily"],
            "total_new_users": users_stats["total"],
            "daily_api_calls": api_stats["daily"],
            "total_api_calls": api_stats["total"],
            "daily_tokens": token_stats["daily"],
            "total_tokens": token_stats["total"]
        }

    def _get_conversations_statistics(
        self,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        start_dt: datetime,
        end_dt: datetime
    ) -> dict[str, Any]:
        """Get conversation statistics."""
        # Daily conversation counts
        daily_query = self.db.query(
            cast(Conversation.created_at, Date).label('date'),
            func.count(Conversation.id).label('count')
        ).filter(
            and_(
                Conversation.app_id == app_id,
                Conversation.workspace_id == workspace_id,
                Conversation.created_at >= start_dt,
                Conversation.created_at < end_dt
            )
        ).group_by(cast(Conversation.created_at, Date)).all()

        daily_data = [{"date": str(row.date), "count": row.count} for row in daily_query]
        total = sum(row["count"] for row in daily_data)

        return {"daily": daily_data, "total": total}

    def _get_new_users_statistics(
        self,
        app_id: uuid.UUID,
        start_dt: datetime,
        end_dt: datetime
    ) -> dict[str, Any]:
        """Get new-user statistics."""
        # Daily new-user counts
        daily_query = self.db.query(
            cast(EndUser.created_at, Date).label('date'),
            func.count(EndUser.id).label('count')
        ).filter(
            and_(
                EndUser.app_id == app_id,
                EndUser.created_at >= start_dt,
                EndUser.created_at < end_dt,
                EndUser.is_active.is_(True),
            )
        ).group_by(cast(EndUser.created_at, Date)).all()

        daily_data = [{"date": str(row.date), "count": row.count} for row in daily_query]
        total = sum(row["count"] for row in daily_data)

        return {"daily": daily_data, "total": total}

    def _get_api_calls_statistics(
        self,
        app_id: uuid.UUID,
        start_dt: datetime,
        end_dt: datetime
    ) -> dict[str, Any]:
        """Get API-call statistics."""
        # Daily API-call counts (cross-domain read: api_key_logs joined with the app's api_keys)
        daily_query = cross_domain_read.get_daily_app_api_calls(self.db, app_id, start_dt, end_dt)

        daily_data = [{"date": str(row.date), "count": row.count} for row in daily_query]
        total = sum(row["count"] for row in daily_data)

        return {"daily": daily_data, "total": total}

    def _get_token_statistics(
        self,
        tenant_id: uuid.UUID,
        app_id: uuid.UUID,
        start_dt: datetime,
        end_dt: datetime
    ) -> dict[str, Any]:
        """Get token-usage statistics.

        Source of truth: model_usage_records; scope = the app's llm calls.
        Covers all app-attributed calls including trial runs and workflows,
        replacing the old scope (reading messages.meta_data); aligns with the
        cost / quota semantics (which live in the external gateway, not here).
        """
        # Daily token usage (read exit: model_usage_records attributed by tenant/app/llm)
        daily_query = cross_domain_read.get_daily_token_usage(
            self.db, tenant_id, app_id, start_dt, end_dt
        )

        daily_data = [{"date": str(row.date), "count": int(row.tokens)} for row in daily_query]
        total = sum(row["count"] for row in daily_data)

        return {"daily": daily_data, "total": total}

    def get_workspace_api_statistics(
        self,
        workspace_id: uuid.UUID,
        start_date: int,
        end_date: int
    ) -> list[Any]:
        """Get workspace API-call statistics.

        Args:
            workspace_id: Workspace ID
            start_date: Start timestamp (milliseconds)
            end_date: End timestamp (milliseconds)

        Returns:
            List of daily statistics
        """
        # Convert millisecond timestamps to datetime
        start_time = datetime.fromtimestamp(start_date / 1000)
        end_time = datetime.fromtimestamp(end_date / 1000)

        # Daily app-type call counts (read exit; window matches the monolith: end_time inclusive)
        daily_app_calls = cross_domain_read.get_daily_workspace_api_calls(
            self.db, workspace_id, APP_KEY_TYPES, start_time, end_time
        )

        # Daily service-type call counts
        daily_service_calls = cross_domain_read.get_daily_workspace_api_calls(
            self.db, workspace_id, SERVICE_KEY_TYPES, start_time, end_time
        )

        # Build the daily data
        app_calls_dict = {str(row.date): row.count for row in daily_app_calls}
        service_calls_dict = {str(row.date): row.count for row in daily_service_calls}

        # Merge all dates
        all_dates = sorted(set(app_calls_dict.keys()) | set(service_calls_dict.keys()))

        daily_data = []
        for date in all_dates:
            app_count = app_calls_dict.get(date, 0)
            service_count = service_calls_dict.get(date, 0)
            daily_data.append({
                "date": date,
                "total_calls": app_count + service_count,
                "app_calls": app_count,
                "service_calls": service_count
            })

        return daily_data


__all__ = ["AppStatisticsService"]
