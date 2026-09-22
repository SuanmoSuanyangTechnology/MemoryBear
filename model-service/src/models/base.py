"""model-service 本地模型基类。

- ServiceBase：本服务认领表（model_configs / model_bases / model_channels /
  model_usage_records），挂 alembic target_metadata —— M7 后四表 DDL 只走服务链
  （老单体链不再生成模型表迁移）；`fk_anchors.py` 中的 tenants 锚点注册在同一
  metadata，仅供 FK 解析，由迁移链 include_object 排除
- ReadOnlyBase：只读映射其他服务/老单体表（apps / app_releases / workspaces /
  workspace_default_model_presets / tenant_subscriptions），表结构归其归属方管理，
  不生成迁移；映射不声明 FK、不建 relationship，对侧改列名/删列时须同步
"""
from datetime import UTC, datetime

from sqlalchemy.orm import DeclarativeBase


class ServiceBase(DeclarativeBase):
    pass


class ReadOnlyBase(DeclarativeBase):
    pass


# 迁移链 include_object 的白名单：M7 后这四张表的 DDL 只走服务链。
# 同一 metadata 内的 model_api_keys / model_config_api_key_association（过渡期冻结实体，
# 随 M6 退役）与 tenants 锚点不属认领范围，服务链既不对比也不创建/删除。
OWNED_TABLES = frozenset(
    {
        "model_configs",
        "model_bases",
        "model_channels",
        "model_usage_records",
    }
)


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
