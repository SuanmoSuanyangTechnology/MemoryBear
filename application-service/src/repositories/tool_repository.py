"""Tool table access (frozen port of the monolith ``tool_repository``
management-plane reads; the host static methods become instance repositories,
matching the port's repository convention)."""

from __future__ import annotations

import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.tool_model import (
    BuiltinToolConfig,
    CustomToolConfig,
    MCPToolConfig,
    ToolConfig,
    ToolStatus,
    ToolType,
    WorkflowToolConfig,
)


class ToolRepository:
    """Tool repository."""

    def __init__(self, db: Session):
        self.db = db

    def find_by_tenant(
        self,
        tenant_id: uuid.UUID,
        name: str | None = None,
        tool_type: ToolType | None = None,
        status: ToolStatus | None = None,
        is_enabled: bool | None = None,
    ) -> list[ToolConfig]:
        """Find tools by tenant (only non-deleted records)."""
        query = self.db.query(ToolConfig).filter(
            ToolConfig.tenant_id == tenant_id,
            ToolConfig.is_active.is_(True)
        )

        if name:
            query = query.filter(ToolConfig.name.ilike(f"%{name}%"))
        if tool_type:
            query = query.filter(ToolConfig.tool_type == tool_type.value)
        if status:
            query = query.filter(ToolConfig.status == status.value)
        if is_enabled is not None:
            query = query.filter(ToolConfig.is_enabled == is_enabled)
        query = query.order_by(ToolConfig.created_at.desc())
        return query.all()

    def find_by_id_and_tenant(self, tool_id: uuid.UUID, tenant_id: uuid.UUID) -> ToolConfig | None:
        """Find a tool by ID and tenant (only non-deleted records)."""
        return self.db.query(ToolConfig).filter(
            ToolConfig.id == tool_id,
            ToolConfig.tenant_id == tenant_id,
            ToolConfig.is_active.is_(True)
        ).first()

    def find_by_id_and_tenant_all(
        self, tool_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> ToolConfig | None:
        """Find a tool by ID and tenant (returns all records, including deleted)."""
        return self.db.query(ToolConfig).filter(
            ToolConfig.id == tool_id,
            ToolConfig.tenant_id == tenant_id
        ).first()

    def count_by_tenant(self, tenant_id: uuid.UUID) -> int:
        """Count a tenant's tools (only non-deleted records)."""
        return self.db.query(ToolConfig).filter(
            ToolConfig.tenant_id == tenant_id,
            ToolConfig.is_active.is_(True)
        ).count()

    def get_status_statistics(self, tenant_id: uuid.UUID) -> list[tuple]:
        """Get status statistics."""
        return self.db.query(ToolConfig.status, func.count(ToolConfig.id).label('count')).filter(
            ToolConfig.tenant_id == tenant_id,
            ToolConfig.is_active.is_(True)
        ).group_by(ToolConfig.status).all()

    def get_type_statistics(self, tenant_id: uuid.UUID) -> list[tuple]:
        """Get type statistics."""
        return self.db.query(ToolConfig.tool_type, func.count(ToolConfig.id).label('count')).filter(
            ToolConfig.tenant_id == tenant_id,
            ToolConfig.is_active.is_(True)
        ).group_by(ToolConfig.tool_type).all()

    def count_enabled_by_tenant(self, tenant_id: uuid.UUID) -> int:
        """Count a tenant's enabled tools."""
        # "Enabled" maps to the available status; ToolConfig has no is_enabled
        # column (that switch only exists on builtin tools).
        return self.db.query(ToolConfig).filter(
            ToolConfig.tenant_id == tenant_id,
            ToolConfig.is_active.is_(True),
            ToolConfig.status == ToolStatus.AVAILABLE.value
        ).count()

    def exists_builtin_for_tenant(self, tenant_id: uuid.UUID) -> bool:
        """Check whether the tenant already has builtin tools."""
        return self.db.query(ToolConfig).filter(
            ToolConfig.tenant_id == tenant_id,
            ToolConfig.tool_type == ToolType.BUILTIN.value,
            ToolConfig.is_active.is_(True)
        ).count() > 0


class BuiltinToolRepository:
    """Builtin tool repository."""

    def __init__(self, db: Session):
        self.db = db

    def find_by_tool_id(self, tool_id: uuid.UUID) -> BuiltinToolConfig | None:
        """Find a builtin tool config by tool ID."""
        return self.db.query(BuiltinToolConfig).filter(
            BuiltinToolConfig.id == tool_id
        ).first()

    def get_existing_tool_classes(self, tenant_id: uuid.UUID) -> set:
        """Get the set of builtin tool classes the tenant already has."""
        rows = self.db.query(BuiltinToolConfig.tool_class).join(
            ToolConfig, BuiltinToolConfig.id == ToolConfig.id
        ).filter(
            ToolConfig.tenant_id == tenant_id,
            ToolConfig.tool_type == ToolType.BUILTIN.value
        ).all()
        return {row[0] for row in rows}


class CustomToolRepository:
    """Custom tool repository."""

    def __init__(self, db: Session):
        self.db = db

    def find_by_tool_id(self, tool_id: uuid.UUID) -> CustomToolConfig | None:
        """Find a custom tool config by tool ID."""
        return self.db.query(CustomToolConfig).filter(
            CustomToolConfig.id == tool_id
        ).first()


class MCPToolRepository:
    """MCP tool repository."""

    def __init__(self, db: Session):
        self.db = db

    def find_by_tool_id(self, tool_id: uuid.UUID) -> MCPToolConfig | None:
        """Find an MCP tool config by tool ID."""
        return self.db.query(MCPToolConfig).filter(
            MCPToolConfig.id == tool_id
        ).first()


class WorkflowToolRepository:
    """Workflow tool repository."""

    def __init__(self, db: Session):
        self.db = db

    def find_by_tool_id(self, tool_id: uuid.UUID) -> WorkflowToolConfig | None:
        """Find a workflow tool config by tool ID."""
        return self.db.query(WorkflowToolConfig).filter(
            WorkflowToolConfig.id == tool_id
        ).first()
