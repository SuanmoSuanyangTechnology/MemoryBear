"""Declarative base for cross-domain tables read during the coexistence period.

The app-domain set (`ServiceBase`) is exactly the 39 tables this service owns.
The tables mapped here belong to other services (`users` / `workspaces` /
`model_*` / `documents` / `knowledge_metadatas`): this service reads them for
request-time resolution and never owns their DDL, so they get a separate
registry and metadata. Relationship attributes that would cross into the
app-domain registry are removed in the model copies (see `monolith_map`).
"""

from sqlalchemy import Column, Table
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase


class CrossDomainBase(DeclarativeBase):
    pass


# FK anchor for `tenants` (identity domain): FK columns on the cross-domain
# tables reference it and no full mapping exists here.
Table(
    "tenants",
    CrossDomainBase.metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
)
