"""Read-only access to host-owned tables required by guards, quota checks, and
application statistics.

These mirrors live in a dedicated ``MetaData`` — never ``ServiceBase.metadata``:
the service ships no DDL and holds no write access to them; the monolith
migration chain owns the schema. Each mirror carries only the column subset the
monolith's own read paths consume, so results stay wire-compatible during the
coexistence period. If the premium quota tables are absent (community
deployment) the queries raise ``ProgrammingError`` with pgcode 42P01; the
quota layer turns that into the free-plan fallback.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    Row,
    String,
    Table,
    cast,
    exists,
    func,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Session

from ..schemas.model_enums import LLM_FAMILY_TYPES

cross_domain_metadata = MetaData()

workspaces = Table(
    "workspaces",
    cross_domain_metadata,
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("name", String),
    Column("icon", String),
    Column("iconType", String),
    Column("description", String),
    Column("tenant_id", PgUUID(as_uuid=True)),
    Column("is_active", Boolean),
)

users = Table(
    "users",
    cross_domain_metadata,
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("username", String),
    Column("email", String),
    Column("tenant_id", PgUUID(as_uuid=True)),
    Column("is_superuser", Boolean),
    Column("is_active", Boolean),
)

workspace_members = Table(
    "workspace_members",
    cross_domain_metadata,
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("user_id", PgUUID(as_uuid=True)),
    Column("workspace_id", PgUUID(as_uuid=True)),
    Column("is_active", Boolean),
)

tenant_subscriptions = Table(
    "tenant_subscriptions",
    cross_domain_metadata,
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("tenant_id", PgUUID(as_uuid=True)),
    Column("package_plan_id", PgUUID(as_uuid=True)),
    Column("package_version", String),
    Column("status", String),
)

package_plans = Table(
    "package_plans",
    cross_domain_metadata,
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("tier_level", Integer),
    Column("quotas", JSONB),
)

package_plan_versions = Table(
    "package_plan_versions",
    cross_domain_metadata,
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("package_plan_id", PgUUID(as_uuid=True)),
    Column("version", String),
    Column("version_snapshot", JSONB),
)

resource_pack_versions = Table(
    "resource_pack_versions",
    cross_domain_metadata,
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("version_snapshot", JSONB),
)

tenant_resource_packs = Table(
    "tenant_resource_packs",
    cross_domain_metadata,
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("tenant_id", PgUUID(as_uuid=True)),
    Column("resource_pack_version_id", PgUUID(as_uuid=True)),
    Column("tier_id", PgUUID(as_uuid=True)),
    Column("quantity", Integer),
    Column("status", String),
    Column("expired_at", DateTime),
)

api_keys = Table(
    "api_keys",
    cross_domain_metadata,
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("api_key", String),
    Column("workspace_id", PgUUID(as_uuid=True)),
    Column("resource_id", PgUUID(as_uuid=True)),
    Column("scopes", JSONB),
    # Statistics reads narrow workspace calls by key type.
    Column("type", String),
)

api_key_logs = Table(
    "api_key_logs",
    cross_domain_metadata,
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("api_key_id", PgUUID(as_uuid=True)),
    Column("created_at", DateTime),
)

model_usage_records = Table(
    "model_usage_records",
    cross_domain_metadata,
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("tenant_id", PgUUID(as_uuid=True)),
    Column("resource_type", String),
    Column("resource_id", PgUUID(as_uuid=True)),
    Column("capability", String),
    Column("input_tokens", BigInteger),
    Column("output_tokens", BigInteger),
    Column("created_at", DateTime),
)

knowledges = Table(
    "knowledges",
    cross_domain_metadata,
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("workspace_id", PgUUID(as_uuid=True)),
    Column("name", String),
    Column("status", Integer),
)

knowledge_shares = Table(
    "knowledge_shares",
    cross_domain_metadata,
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("source_kb_id", PgUUID(as_uuid=True)),
    Column("target_kb_id", PgUUID(as_uuid=True)),
    Column("target_workspace_id", PgUUID(as_uuid=True)),
)

# Table name is singular in the monolith model (`memory_config`).
memory_config = Table(
    "memory_config",
    cross_domain_metadata,
    Column("config_id", PgUUID(as_uuid=True), primary_key=True),
    Column("config_id_old", Integer),
    Column("workspace_id", PgUUID(as_uuid=True)),
    Column("is_default", Boolean),
    Column("state", Boolean),
    Column("created_at", DateTime),
)

model_configs = Table(
    "model_configs",
    cross_domain_metadata,
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("tenant_id", PgUUID(as_uuid=True)),
    Column("model_id", PgUUID(as_uuid=True)),
    Column("name", String),
    Column("provider", String),
    Column("type", String),
    Column("is_active", Boolean),
    Column("is_public", Boolean),
    Column("created_at", DateTime),
)

model_bases = Table(
    "model_bases",
    cross_domain_metadata,
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("is_deprecated", Boolean),
)

tool_configs = Table(
    "tool_configs",
    cross_domain_metadata,
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("tenant_id", PgUUID(as_uuid=True)),
    Column("name", String),
    Column("tool_type", String),
    Column("is_active", Boolean),
)

skills = Table(
    "skills",
    cross_domain_metadata,
    Column("id", PgUUID(as_uuid=True), primary_key=True),
    Column("tenant_id", PgUUID(as_uuid=True)),
    Column("name", String),
)


def get_workspace(db: Session, workspace_id: UUID) -> Row | None:
    """Workspace row by id (no is_active filter, matching the host lookup)."""

    return db.execute(
        select(workspaces).where(workspaces.c.id == workspace_id)
    ).first()


def get_workspace_ids_by_tenant(db: Session, tenant_id: UUID) -> list[UUID]:
    """Workspace ids of a tenant (stand-in for the host workspace join in
    tenant-scoped usage counts)."""

    return list(
        db.execute(select(workspaces.c.id).where(workspaces.c.tenant_id == tenant_id))
        .scalars()
        .all()
    )


def get_user(db: Session, user_id: UUID) -> Row | None:
    return db.execute(select(users).where(users.c.id == user_id)).first()


def is_workspace_member(db: Session, user_id: UUID, workspace_id: UUID) -> bool:
    row = db.execute(
        select(workspace_members.c.id)
        .where(
            workspace_members.c.user_id == user_id,
            workspace_members.c.workspace_id == workspace_id,
            workspace_members.c.is_active.is_(True),
        )
        .limit(1)
    ).first()
    return row is not None


def get_active_subscription(db: Session, tenant_id: UUID) -> Row | None:
    """Current subscription: status = active, highest tier_level first."""

    return db.execute(
        select(tenant_subscriptions)
        .join(
            package_plans,
            tenant_subscriptions.c.package_plan_id == package_plans.c.id,
        )
        .where(
            tenant_subscriptions.c.tenant_id == tenant_id,
            tenant_subscriptions.c.status == "active",
        )
        .order_by(package_plans.c.tier_level.desc())
        .limit(1)
    ).first()


def get_plan_version_snapshot(
    db: Session, package_plan_id: UUID, version: str
) -> Any | None:
    return db.execute(
        select(package_plan_versions.c.version_snapshot)
        .where(
            package_plan_versions.c.package_plan_id == package_plan_id,
            package_plan_versions.c.version == version,
        )
        .limit(1)
    ).scalars().first()


def get_plan_quotas(db: Session, package_plan_id: UUID) -> dict[str, Any] | None:
    return db.execute(
        select(package_plans.c.quotas)
        .where(package_plans.c.id == package_plan_id)
        .limit(1)
    ).scalars().first()


def get_resource_pack_rows(
    db: Session, tenant_id: UUID, now: datetime
) -> list[Row]:
    """Active resource-pack grants with their unexpired quantities."""

    return (
        db.execute(
            select(
                resource_pack_versions.c.version_snapshot,
                tenant_resource_packs.c.tier_id,
                tenant_resource_packs.c.quantity,
            )
            .join(
                tenant_resource_packs,
                tenant_resource_packs.c.resource_pack_version_id
                == resource_pack_versions.c.id,
            )
            .where(
                tenant_resource_packs.c.tenant_id == tenant_id,
                tenant_resource_packs.c.status == "active",
                or_(
                    tenant_resource_packs.c.expired_at.is_(None),
                    tenant_resource_packs.c.expired_at > now,
                ),
            )
        )
        .all()
    )


def get_api_key_resource_id(
    db: Session, workspace_id: UUID, api_key: str
) -> UUID | None:
    """Exact API-key match: the host `list_apps` search filter; a key without a
    bound resource never matches."""

    return db.execute(
        select(api_keys.c.resource_id).where(
            api_keys.c.workspace_id == workspace_id,
            api_keys.c.api_key == api_key,
            api_keys.c.resource_id.isnot(None),
        )
    ).scalar_one_or_none()


def get_api_key_binding(
    db: Session, api_key_id: UUID, workspace_id: UUID
) -> Row | None:
    """Key-row binding used by the ``/v1`` faces: resolve the key's resource,
    type and scopes from the credential id the gateway injected. Scoped by the
    principal's workspace (claims authority) as defense in depth; a missing row
    (deleted key) or foreign workspace returns None."""

    return db.execute(
        select(
            api_keys.c.resource_id, api_keys.c.type, api_keys.c.scopes
        ).where(
            api_keys.c.id == api_key_id,
            api_keys.c.workspace_id == workspace_id,
        )
    ).first()


def get_workspaces_by_ids(
    db: Session, workspace_ids: set[UUID]
) -> dict[UUID, Row]:
    """Batch workspace rows for share serialization (host lazy-loads per row)."""

    if not workspace_ids:
        return {}
    rows = db.execute(
        select(workspaces).where(workspaces.c.id.in_(workspace_ids))
    ).all()
    return {row.id: row for row in rows}


def get_usernames_by_ids(db: Session, user_ids: set[UUID]) -> dict[UUID, str]:
    """Batch usernames for share serialization (host lazy-loads per row)."""

    if not user_ids:
        return {}
    rows = db.execute(
        select(users.c.id, users.c.username).where(users.c.id.in_(user_ids))
    ).all()
    return {row.id: row.username for row in rows}


def get_user_display_names_by_ids(db: Session, user_ids: set[UUID]) -> dict[UUID, str | None]:
    """Batch publisher display names (host reads ``User.username or User.email``
    per row through the lazy relationship; the caller applies the "未知用户"
    fallback for missing rows)."""

    if not user_ids:
        return {}
    rows = db.execute(
        select(users.c.id, users.c.username, users.c.email).where(users.c.id.in_(user_ids))
    ).all()
    return {row.id: row.username or row.email for row in rows}


def get_workspace_tenant_id(db: Session, workspace_id: UUID) -> UUID | None:
    """Workspace -> tenant mapping (host ``ToolRepository.get_tenant_id_by_workspace_id``)."""

    return db.execute(
        select(workspaces.c.tenant_id).where(workspaces.c.id == workspace_id)
    ).scalar_one_or_none()


def get_model_config_row(
    db: Session, model_id: UUID, tenant_id: UUID | None = None
) -> Row | None:
    """Model config by id with the base-model deprecation flag (host
    ``ModelConfigRepository.get_by_id``: the tenant filter admits the tenant's
    own configs plus public ones)."""

    stmt = (
        select(model_configs, model_bases.c.is_deprecated)
        .select_from(
            model_configs.outerjoin(
                model_bases, model_bases.c.id == model_configs.c.model_id
            )
        )
        .where(model_configs.c.id == model_id)
    )
    if tenant_id:
        stmt = stmt.where(
            or_(model_configs.c.tenant_id == tenant_id, model_configs.c.is_public)
        )
    return db.execute(stmt).first()


def get_publishable_ref_rows(db: Session, config_ids: list[UUID]) -> list[Row]:
    """One batched row per referenced model config with its base-model
    deprecation flag (host ``ModelConfigService.assert_refs_publishable``;
    missing rows simply do not appear)."""

    if not config_ids:
        return []
    stmt = (
        select(
            model_configs.c.id,
            model_configs.c.name,
            model_configs.c.is_active,
            model_bases.c.is_deprecated,
        )
        .select_from(
            model_configs.outerjoin(model_bases, model_bases.c.id == model_configs.c.model_id)
        )
        .where(model_configs.c.id.in_(config_ids))
    )
    return db.execute(stmt).all()


def get_available_model_ids(
    db: Session, model_config_ids: set[UUID], tenant_id: UUID | None
) -> set[UUID]:
    """Model configs of the given ids that belong to the target tenant."""

    if not model_config_ids or not tenant_id:
        return set()
    return set(
        db.execute(
            select(model_configs.c.id).where(
                model_configs.c.id.in_(model_config_ids),
                model_configs.c.tenant_id == tenant_id,
            )
        )
        .scalars()
        .all()
    )


def get_available_kb_ids(
    db: Session, kb_uuids: set[UUID], workspace_id: UUID
) -> set[UUID]:
    """Knowledge bases reachable from the target workspace: owned + shared in."""

    available: set[UUID] = set()
    if not kb_uuids:
        return available
    available.update(
        db.execute(
            select(knowledges.c.id).where(
                knowledges.c.id.in_(kb_uuids),
                knowledges.c.workspace_id == workspace_id,
            )
        )
        .scalars()
        .all()
    )
    remaining = kb_uuids - available
    if remaining:
        available.update(
            db.execute(
                select(knowledge_shares.c.source_kb_id).where(
                    knowledge_shares.c.source_kb_id.in_(remaining),
                    knowledge_shares.c.target_workspace_id == workspace_id,
                )
            )
            .scalars()
            .all()
        )
    return available


def memory_config_exists_by_uuid(
    db: Session, config_id: UUID, workspace_id: UUID
) -> bool:
    """Memory config (uuid form) exists in the target workspace."""

    return bool(
        db.execute(
            select(
                exists().where(
                    memory_config.c.config_id == config_id,
                    memory_config.c.workspace_id == workspace_id,
                )
            )
        ).scalar()
    )


def memory_config_exists_by_legacy_id(
    db: Session, config_id_old: int, workspace_id: UUID
) -> bool:
    """Memory config (legacy integer id form) exists in the target workspace."""

    return bool(
        db.execute(
            select(
                exists().where(
                    memory_config.c.config_id_old == config_id_old,
                    memory_config.c.workspace_id == workspace_id,
                )
            )
        ).scalar()
    )


def get_memory_config_by_id(db: Session, config_id: UUID) -> Row | None:
    """Memory config by uuid, no workspace filter (host
    ``MemoryConfigRepository.get_by_id_async``)."""

    return db.execute(
        select(memory_config).where(memory_config.c.config_id == config_id)
    ).first()


def get_workspace_default_memory_config(db: Session, workspace_id: UUID) -> Row | None:
    """Workspace default memory config (host
    ``MemoryConfigRepository.get_workspace_default_async``: flagged default
    first, else the earliest active config by created_at)."""

    row = db.execute(
        select(memory_config)
        .where(
            memory_config.c.workspace_id == workspace_id,
            memory_config.c.is_default.is_(True),
            memory_config.c.state.is_(True),
        )
        .limit(1)
    ).first()
    if row is not None:
        return row
    return db.execute(
        select(memory_config)
        .where(
            memory_config.c.workspace_id == workspace_id,
            memory_config.c.state.is_(True),
        )
        .order_by(memory_config.c.created_at.asc())
        .limit(1)
    ).first()


def get_memory_config_with_fallback(
    db: Session, config_id: UUID | None, workspace_id: UUID
) -> Row | None:
    """Memory config with workspace-default fallback (host
    ``MemoryConfigRepository.get_with_fallback_async``: a missing id falls back
    to the default instead of erroring)."""

    if not config_id:
        return get_workspace_default_memory_config(db, workspace_id)
    row = get_memory_config_by_id(db, config_id)
    if row is not None:
        return row
    return get_workspace_default_memory_config(db, workspace_id)


def get_tenant_user_ids(db: Session, tenant_id: UUID) -> list[UUID]:
    """User ids of a tenant (host quota trial-user exclusion: end users whose
    ``other_id`` matches a tenant user are not counted)."""

    return list(
        db.execute(select(users.c.id).where(users.c.tenant_id == tenant_id))
        .scalars()
        .all()
    )


def get_knowledge_row(db: Session, kb_id: UUID) -> Row | None:
    """Knowledge row by id (host ``AppDslService._kb_ref``: no status filter)."""

    return db.execute(select(knowledges).where(knowledges.c.id == kb_id)).first()


def get_knowledge_share_row(
    db: Session, workspace_id: UUID, source_kb_id: UUID
) -> Row | None:
    """Incoming share of a knowledge base to the workspace (host
    ``AppDslService._resolve_kb`` first probe)."""

    return db.execute(
        select(knowledge_shares).where(
            knowledge_shares.c.target_workspace_id == workspace_id,
            knowledge_shares.c.source_kb_id == source_kb_id,
        )
    ).first()


def get_workspace_published_knowledge_row(
    db: Session, workspace_id: UUID, kb_id: UUID
) -> Row | None:
    """Workspace-owned published knowledge base (host ``AppDslService._resolve_kb``
    second probe: workspace + id + status == 1)."""

    return db.execute(
        select(knowledges).where(
            knowledges.c.workspace_id == workspace_id,
            knowledges.c.id == kb_id,
            knowledges.c.status == 1,
        )
    ).first()


def get_tool_config_row(db: Session, tool_id: UUID) -> Row | None:
    """Tool config row by id (host ``AppDslService._tool_ref``)."""

    return db.execute(
        select(tool_configs).where(tool_configs.c.id == tool_id)
    ).first()


def get_skill_row(db: Session, skill_id: UUID) -> Row | None:
    """Skill row by id (host ``AppDslService._skill_ref``)."""

    return db.execute(select(skills).where(skills.c.id == skill_id)).first()


def get_importable_model_id(
    db: Session,
    model_id: UUID,
    tenant_id: UUID,
    allowed_types: Collection[str] | None = None,
) -> UUID | None:
    """Active model config by exact id admitted to the tenant (host
    ``AppDslService._resolve_model`` id probe: the tenant's own configs plus
    public ones, optionally narrowed to the allowed types)."""

    filters = [
        model_configs.c.id == model_id,
        model_configs.c.is_active.is_(True),
        or_(model_configs.c.tenant_id == tenant_id, model_configs.c.is_public.is_(True)),
    ]
    if allowed_types:
        filters.append(model_configs.c.type.in_(allowed_types))
    return db.execute(select(model_configs.c.id).where(*filters)).scalar_one_or_none()


def find_importable_model_id_by_name(
    db: Session,
    tenant_id: UUID,
    name: str,
    provider: str | None = None,
    ref_type: str | None = None,
    allowed_types: Collection[str] | None = None,
) -> UUID | None:
    """Name match for imported model refs (host ``AppDslService._resolve_model``
    name probe): newest active match first, tenant-owned rows preferred over
    public ones. A legacy ``chat`` ref type is resolved as the llm family."""

    filters = [
        or_(model_configs.c.tenant_id == tenant_id, model_configs.c.is_public.is_(True)),
        model_configs.c.name == name,
        model_configs.c.is_active.is_(True),
    ]
    if allowed_types:
        filters.append(model_configs.c.type.in_(allowed_types))
    if provider:
        filters.append(model_configs.c.provider == provider)
    if isinstance(ref_type, str) and ref_type.lower() in LLM_FAMILY_TYPES:
        filters.append(model_configs.c.type.in_(LLM_FAMILY_TYPES))
    elif ref_type:
        filters.append(model_configs.c.type == ref_type)

    order = (
        model_configs.c.is_active.desc(),
        model_configs.c.created_at.desc().nulls_last(),
    )
    row = db.execute(
        select(model_configs.c.id)
        .where(*filters, model_configs.c.tenant_id == tenant_id)
        .order_by(*order)
    ).first()
    if row is None:
        row = db.execute(
            select(model_configs.c.id)
            .where(*filters, model_configs.c.is_public.is_(True))
            .order_by(*order)
        ).first()
    return row.id if row else None


def get_importable_tool_id(db: Session, tool_id: UUID, tenant_id: UUID) -> UUID | None:
    """Tenant-owned active tool config by id (host ``AppDslService._resolve_tool``
    id probe)."""

    return db.execute(
        select(tool_configs.c.id).where(
            tool_configs.c.id == tool_id,
            tool_configs.c.tenant_id == tenant_id,
            tool_configs.c.is_active.is_(True),
        )
    ).scalar_one_or_none()


def find_tool_config_id_by_name(
    db: Session, tenant_id: UUID, name: str, tool_type: str | None = None
) -> UUID | None:
    """Name match for imported tool refs (host ``AppDslService._resolve_tool``
    name probe: no ``is_active`` filter)."""

    stmt = select(tool_configs.c.id).where(
        tool_configs.c.tenant_id == tenant_id,
        tool_configs.c.name == name,
    )
    if tool_type:
        stmt = stmt.where(tool_configs.c.tool_type == tool_type)
    return db.execute(stmt).scalars().first()


def get_importable_skill_id(db: Session, skill_id: UUID, tenant_id: UUID) -> UUID | None:
    """Tenant-owned skill by id (host ``AppDslService._resolve_skill`` id probe)."""

    return db.execute(
        select(skills.c.id).where(
            skills.c.id == skill_id,
            skills.c.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()


def find_skill_id_by_name(db: Session, tenant_id: UUID, name: str) -> UUID | None:
    """Name match for imported skill refs (host ``AppDslService._resolve_skill``
    name probe)."""

    return (
        db.execute(
            select(skills.c.id).where(
                skills.c.name == name,
                skills.c.tenant_id == tenant_id,
            )
        )
        .scalars()
        .first()
    )


def get_daily_app_api_calls(
    db: Session, app_id: UUID, start_dt: datetime, end_dt: datetime
) -> list[Row]:
    """Daily API-call counts for one app (host
    ``AppStatisticsService._get_api_calls_statistics``: log rows joined to the
    keys bound to the app, half-open window)."""

    return db.execute(
        select(
            cast(api_key_logs.c.created_at, Date).label("date"),
            func.count(api_key_logs.c.id).label("count"),
        )
        .select_from(
            api_key_logs.join(api_keys, api_key_logs.c.api_key_id == api_keys.c.id)
        )
        .where(
            api_keys.c.resource_id == app_id,
            api_key_logs.c.created_at >= start_dt,
            api_key_logs.c.created_at < end_dt,
        )
        .group_by(cast(api_key_logs.c.created_at, Date))
    ).all()


def get_daily_workspace_api_calls(
    db: Session,
    workspace_id: UUID,
    key_types: Collection[str],
    start_dt: datetime,
    end_dt: datetime,
) -> list[Row]:
    """Daily API-call counts per workspace key type (host
    ``AppStatisticsService.get_workspace_api_statistics``: log rows joined to
    the workspace's keys, inclusive window; the caller issues one query for
    app-type keys and one for service keys)."""

    return db.execute(
        select(
            cast(api_key_logs.c.created_at, Date).label("date"),
            func.count(api_key_logs.c.id).label("count"),
        )
        .select_from(
            api_key_logs.join(api_keys, api_key_logs.c.api_key_id == api_keys.c.id)
        )
        .where(
            api_keys.c.workspace_id == workspace_id,
            api_keys.c.type.in_(key_types),
            api_key_logs.c.created_at >= start_dt,
            api_key_logs.c.created_at <= end_dt,
        )
        .group_by(cast(api_key_logs.c.created_at, Date))
    ).all()


def get_daily_token_usage(
    db: Session, tenant_id: UUID, app_id: UUID, start_dt: datetime, end_dt: datetime
) -> list[Row]:
    """Daily token sums for one app's llm calls (host
    ``AppStatisticsService._get_token_statistics``: usage records own-resource
    rows only, zero-sum days dropped, ascending by date)."""

    token_sum = func.sum(
        model_usage_records.c.input_tokens + model_usage_records.c.output_tokens
    )
    return db.execute(
        select(
            cast(model_usage_records.c.created_at, Date).label("date"),
            token_sum.label("tokens"),
        )
        .where(
            model_usage_records.c.tenant_id == tenant_id,
            model_usage_records.c.resource_type == "app",
            model_usage_records.c.resource_id == app_id,
            model_usage_records.c.capability == "llm",
            model_usage_records.c.created_at >= start_dt,
            model_usage_records.c.created_at < end_dt,
        )
        .group_by(cast(model_usage_records.c.created_at, Date))
        .having(token_sum > 0)
        .order_by(cast(model_usage_records.c.created_at, Date))
    ).all()


__all__ = [
    "cross_domain_metadata",
    "find_importable_model_id_by_name",
    "find_skill_id_by_name",
    "find_tool_config_id_by_name",
    "get_active_subscription",
    "get_api_key_binding",
    "get_api_key_resource_id",
    "get_available_kb_ids",
    "get_available_model_ids",
    "get_daily_app_api_calls",
    "get_daily_token_usage",
    "get_daily_workspace_api_calls",
    "get_importable_model_id",
    "get_importable_skill_id",
    "get_importable_tool_id",
    "get_knowledge_row",
    "get_knowledge_share_row",
    "get_memory_config_by_id",
    "get_memory_config_with_fallback",
    "get_model_config_row",
    "get_plan_quotas",
    "get_plan_version_snapshot",
    "get_publishable_ref_rows",
    "get_resource_pack_rows",
    "get_skill_row",
    "get_tenant_user_ids",
    "get_tool_config_row",
    "get_user",
    "get_user_display_names_by_ids",
    "get_usernames_by_ids",
    "get_workspace",
    "get_workspace_default_memory_config",
    "get_workspace_ids_by_tenant",
    "get_workspace_tenant_id",
    "get_workspace_published_knowledge_row",
    "get_workspaces_by_ids",
    "is_workspace_member",
    "memory_config_exists_by_legacy_id",
    "memory_config_exists_by_uuid",
]
