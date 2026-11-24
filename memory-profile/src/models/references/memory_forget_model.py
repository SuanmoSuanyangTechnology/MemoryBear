"""Read-only mapping for the API-owned ``forgetting_cycle_history`` table.

The table records one row per forgetting cycle (manual today, scheduled from the
API task) and backs the stats trend/window. It is never a migration target and
declares no foreign keys or relationships, matching the other reference models.
"""

from sqlalchemy import Column, DateTime, Float, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from src.models.base import ReadOnlyBase


class ForgettingCycleHistory(ReadOnlyBase):
    __tablename__ = "forgetting_cycle_history"

    id = Column(UUID(as_uuid=True), primary_key=True)
    end_user_id = Column(String(255), nullable=False)
    execution_time = Column(DateTime, nullable=False)
    merged_count = Column(Integer, nullable=False)
    failed_count = Column(Integer, nullable=False)
    average_activation_value = Column(Float, nullable=True)
    total_nodes = Column(Integer, nullable=False)
    low_activation_nodes = Column(Integer, nullable=False)
    duration_seconds = Column(Float, nullable=True)
    trigger_type = Column(String(50), nullable=False)