"""Request body for updating workspace temporary-memory retention."""

from pydantic import BaseModel, Field


class WorkspaceRetentionPolicyUpdate(BaseModel):
    retention_days: int | None = Field(
        default=None,
        ge=1,
        le=3650,
        description="临时身份保留天数，null 表示永不过期",
    )
