"""ServiceBase 外部 FK 锚点（非认领表，仅满足 SQLAlchemy 的 FK 解析要求）。

`model_configs.tenant_id` / `model_channels.tenant_id` 引用 `tenants.id`：该表归老单体，
不在本服务 target_metadata 内。SQLAlchemy 解析 FK 目标时要求同 metadata 存在目标表，
否则 `metadata.sorted_tables` / DDL 编译抛 NoReferencedTableError —— alembic autogenerate
与 baseline 生成都会走到。故此处只放最小锚点（id 单列）：迁移链按 `base.OWNED_TABLES`
白名单过滤，锚点既不参与 autogenerate 对比，也不会被本链创建/删除。
"""
from sqlalchemy import Column, Table
from sqlalchemy.dialects.postgresql import UUID

from .base import ServiceBase

Table(
    "tenants",
    ServiceBase.metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
)
