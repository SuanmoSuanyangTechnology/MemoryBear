"""identity 本地模型基类。

- ServiceBase：本服务自有表（acl_rules/audit_logs），挂 alembic target_metadata
- SharedBase：管理面可写的 core 共享表，不生成 DDL、不声明外键
- ReadOnlyBase：只读映射 core 表（apps/api_key_logs），不生成迁移

SharedBase/ReadOnlyBase 都是受控复制，schema 真源在老单体迁移链；core 改列名/
删列时须同步本包下的镜像模型
"""
from sqlalchemy.orm import DeclarativeBase

from src.core.datetime_utils import utcnow_naive  # noqa: F401  (re-exported for models)


class ServiceBase(DeclarativeBase):
    pass


class SharedBase(DeclarativeBase):
    """Host-owned tables the management plane writes (no DDL, no FKs)."""


class ReadOnlyBase(DeclarativeBase):
    pass
