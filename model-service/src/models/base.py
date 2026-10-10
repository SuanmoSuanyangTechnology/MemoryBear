"""model-service local model bases.

- ServiceBase: tables mapped by this service. The four model tables
  (model_configs / model_bases / model_channels / model_usage_records) are
  written by this service at runtime, but their DDL is managed by the host
  (enterprise) migration chain again — see HOST_OWNED_TABLES, the denylist the
  service chain's include_object excludes. The service chain only ever manages
  tables born in it. The `tenants` anchor from `fk_anchors.py` shares this
  metadata for FK resolution only and is excluded the same way.
- ReadOnlyBase: read-only mappings of tables owned by other services / the
  monolith (apps / app_releases / workspaces / workspace_default_model_presets /
  tenant_subscriptions). Their schema is managed by the owning side and no
  migrations are generated here. The mappings declare no FK and no
  relationships; keep them in sync when the owning side renames or drops
  columns.
"""
from datetime import UTC, datetime

from sqlalchemy.orm import DeclarativeBase


class ServiceBase(DeclarativeBase):
    pass


class ReadOnlyBase(DeclarativeBase):
    pass


# Tables this service writes at runtime — the only DML targets allowed by
# scripts/check_no_host_writes.py. Their DDL is managed by the host
# (enterprise) migration chain (ownership moved back there).
SERVICE_WRITE_TABLES = frozenset(
    {
        "model_configs",
        "model_bases",
        "model_channels",
        "model_usage_records",
    }
)

# Denylist for the service migration chain's include_object: tables present in
# ServiceBase.metadata whose DDL is managed by the host chain — the service
# chain neither compares nor creates/drops them.
# - the four model tables above: born in the monolith chain, host-managed again
# - model_api_keys / model_config_api_key_association: frozen transitional
#   entities (retired with the old write path), host-managed
# - tenants: minimal FK anchor, monolith table
HOST_OWNED_TABLES = SERVICE_WRITE_TABLES | frozenset(
    {
        "model_api_keys",
        "model_config_api_key_association",
        "tenants",
    }
)


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
