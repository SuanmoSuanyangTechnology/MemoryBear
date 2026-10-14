"""External FK anchors for ServiceBase (non-owned tables; only satisfy SQLAlchemy FK resolution).

The app-domain FK columns reference five tables owned by other domains: `tenants`,
`users` and `workspaces` (identity), `model_configs` (model) and `memory_config`
(memory). SQLAlchemy requires every FK target table in the same metadata when
resolving FKs; otherwise `metadata.sorted_tables` / DDL compilation raises
NoReferencedTableError. Hence only minimal anchors (the referenced key column) are
placed here — this service never reads or writes these tables (cross-domain data is
reached through service interfaces, never through ORM joins), and no migration chain
manages this metadata (the DDL lives in the monolith chain).
"""
from sqlalchemy import Column, Table
from sqlalchemy.dialects.postgresql import UUID

from .base import ServiceBase

Table(
    "tenants",
    ServiceBase.metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
)

Table(
    "users",
    ServiceBase.metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
)

Table(
    "workspaces",
    ServiceBase.metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
)

Table(
    "model_configs",
    ServiceBase.metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
)

# The memory-domain config table uses `config_id` as its primary key column name.
Table(
    "memory_config",
    ServiceBase.metadata,
    Column("config_id", UUID(as_uuid=True), primary_key=True),
)
