"""External FK anchors for ServiceBase (non-owned tables; only satisfy SQLAlchemy FK resolution).

`model_configs.tenant_id` / `model_channels.tenant_id` reference `tenants.id`, a monolith
table absent from this service's target_metadata. SQLAlchemy requires the target table in
the same metadata when resolving FKs; otherwise `metadata.sorted_tables` / DDL compilation
raises NoReferencedTableError — hit by both alembic autogenerate and baseline generation.
Hence only a minimal anchor is placed here (single id column): the migration chain excludes
it via base.HOST_OWNED_TABLES, so it takes part in neither autogenerate comparison nor
create/drop by this chain.
"""
from sqlalchemy import Column, Table
from sqlalchemy.dialects.postgresql import UUID

from .base import ServiceBase

Table(
    "tenants",
    ServiceBase.metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
)
