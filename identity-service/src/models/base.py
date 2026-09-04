"""identity 本地模型基类。

- ServiceBase：本服务自有表（acl_rules/audit_logs），挂 alembic target_metadata
- ReadOnlyBase：只读映射 core 表（users/tenants/workspaces/workspace_members/api_keys），
  表结构归 core 管理，不生成迁移；core 改列名/删列时须同步本文件
"""
from datetime import datetime, timezone

from sqlalchemy.orm import DeclarativeBase


class ServiceBase(DeclarativeBase):
    pass


class ReadOnlyBase(DeclarativeBase):
    pass


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
