"""model-service baseline：四表冻结快照（D-M7-8）

Revision ID: 0001_model_baseline
Revises:
Create Date: 2026-09-21

四表（model_bases / model_usage_records / model_channels / model_configs）在老单体链
已经存在，本文件是**冻结快照**而非"从零建库"脚本：

- 既有库：inspect 守卫逐表跳过，唯一实际动作是 alembic 自建 alembic_version_model
  并盖章 0001 —— 与老单体链（alembic_version）同库共存互不覆盖
- 新库：按快照建表；注意 model_bases/model_configs/model_channels 对 `tenants` 有 FK，
  而 tenants 归老单体，故必须先有 tenants 表（真实部署恒成立）

冻结语义：后续列/索引变更一律新增 revision，**不要回改本文件** —— 改它会让新库
baseline 与既有库漂移，也破坏"快照 = 盖章时的 ORM 形状"这一对应关系。

与既有库的两处已知差异（仅影响新库路径，因既有库走守卫跳过）：
- model_bases.created_at：老库带 `DEFAULT now()`（老迁移加列时带），ORM 未声明
  server_default → 新库无 DB 默认，由应用侧 default=utcnow_naive 填充
- model_bases.capability / is_omni：老库无 DB 默认，ORM 声明 `'{}'` / `false`

downgrade 是**有意的 no-op**：四表在老库归老单体链，服务链无权 drop；`alembic
downgrade` 只回退版本号，不删表。确需删表由运维按现场判断手工执行。
"""
import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision = "0001_model_baseline"
down_revision = None
branch_labels = None
depends_on = None

_TABLES = (
    "model_bases",
    "model_usage_records",
    "model_channels",
    "model_configs",
)


def _missing_tables() -> set[str]:
    """既存库跳过：只对缺失表建 DDL（在任一 DDL 执行前一次性探测）。

    离线（--sql）模式无连接可探，按"全新库"输出完整 DDL。
    """
    if context.is_offline_mode():
        return set(_TABLES)
    inspector = sa.inspect(op.get_bind())
    return {name for name in _TABLES if not inspector.has_table(name)}


def upgrade() -> None:
    missing = _missing_tables()

    if "model_bases" in missing:
        op.create_table(
            "model_bases",
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("logo", sa.String(length=255), nullable=True, comment="模型logo图片URL"),
            sa.Column("name", sa.String(), nullable=False, comment="模型唯一标识（如gpt-3.5-turbo）"),
            sa.Column("type", sa.String(), nullable=False, comment="模型类型"),
            sa.Column("provider", sa.String(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True, comment="模型描述"),
            sa.Column("is_deprecated", sa.Boolean(), nullable=False, comment="是否弃用"),
            sa.Column("is_official", sa.Boolean(), nullable=True, comment="是否供应商官方模型（区分自定义）"),
            sa.Column("tags", postgresql.ARRAY(sa.String()), nullable=False, comment="模型标签（如['聊天', '创作']）"),
            sa.Column("add_count", sa.Integer(), nullable=False, comment="模型被用户添加的次数"),
            sa.Column("created_at", sa.DateTime(), nullable=True, comment="创建时间"),
            sa.Column("capability", postgresql.ARRAY(sa.String()), server_default=sa.text("'{}'::varchar[]"), nullable=False, comment="模型能力列表（如['vision', 'audio', 'video']）"),
            sa.Column("is_omni", sa.Boolean(), server_default="false", nullable=False, comment="是否为Omni模型（使用特殊API调用）"),
            sa.Column("input_modalities", postgresql.ARRAY(sa.String()), server_default=sa.text("'{}'::varchar[]"), nullable=False, comment="输入模态（如['text','image','audio','video']）"),
            sa.Column("output_modalities", postgresql.ARRAY(sa.String()), server_default=sa.text("'{}'::varchar[]"), nullable=False, comment="输出模态（如['text','image','audio']）"),
            sa.Column("features", postgresql.ARRAY(sa.String()), server_default=sa.text("'{}'::varchar[]"), nullable=False, comment="能力特征（如['thinking','json_output','function_call']）"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name", "provider", name="uk_model_name_provider"),
        )
        op.create_index(op.f("ix_model_bases_id"), "model_bases", ["id"], unique=False)
        op.create_index(op.f("ix_model_bases_provider"), "model_bases", ["provider"], unique=False)
        op.create_index(op.f("ix_model_bases_type"), "model_bases", ["type"], unique=False)

    if "model_usage_records" in missing:
        op.create_table(
            "model_usage_records",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("event_id", sa.UUID(), nullable=False),
            sa.Column("source_service", sa.String(length=32), nullable=False),
            sa.Column("tenant_id", sa.UUID(), nullable=False),
            sa.Column("config_id", sa.UUID(), nullable=False),
            sa.Column("channel_id", sa.UUID(), nullable=True),
            sa.Column("resource_type", sa.String(length=32), nullable=True),
            sa.Column("resource_id", sa.UUID(), nullable=True),
            sa.Column("provider", sa.String(length=50), nullable=False),
            sa.Column("model_name", sa.String(length=255), nullable=False),
            sa.Column("capability", sa.String(length=32), nullable=False),
            sa.Column("stream", sa.Boolean(), nullable=False),
            sa.Column("input_tokens", sa.BigInteger(), nullable=False),
            sa.Column("output_tokens", sa.BigInteger(), nullable=False),
            sa.Column("images_count", sa.Integer(), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("error_type", sa.String(length=64), nullable=True),
            sa.Column("attempts", sa.SmallInteger(), nullable=False),
            sa.Column("request_id", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("event_id", name="uq_model_usage_records_event_id"),
        )
        op.create_index("ix_usage_channel_time", "model_usage_records", ["channel_id", sa.literal_column("created_at DESC")], unique=False)
        op.create_index("ix_usage_model_time", "model_usage_records", ["provider", "model_name", sa.literal_column("created_at DESC")], unique=False)
        op.create_index("ix_usage_resource_time", "model_usage_records", ["tenant_id", "resource_type", "resource_id", sa.literal_column("created_at DESC")], unique=False)
        op.create_index("ix_usage_tenant_time", "model_usage_records", ["tenant_id", sa.literal_column("created_at DESC")], unique=False)

    if "model_channels" in missing:
        op.create_table(
            "model_channels",
            sa.Column("tenant_id", sa.UUID(), nullable=False, comment="凭据归属租户"),
            sa.Column("provider", sa.String(length=50), nullable=False, comment="供应商（不允许 composite）"),
            sa.Column("model_names", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False, comment="覆盖模型集；[]=provider 级默认渠道"),
            sa.Column("api_base", sa.String(length=512), nullable=True, comment="执行端点；空=provider 默认 base_url（不参与覆盖匹配）"),
            sa.Column("credential_encrypted", sa.Text(), nullable=False, comment="信封 v{ver}:iv:tag:ct"),
            sa.Column("credential_sha256", sa.String(length=64), nullable=False, comment="凭据指纹（幂等合并键）"),
            sa.Column("credential_masked", sa.String(length=255), nullable=False, comment="展示用掩码 sk-****abcd"),
            sa.Column("priority", sa.Integer(), server_default="0", nullable=False, comment="同精确度主备权重"),
            sa.Column("cooldown_until_ms", sa.BigInteger(), nullable=True, comment="熔断预留（阶段一恒空）"),
            sa.Column("source", sa.String(length=20), server_default="manual", nullable=False, comment="manual|platform"),
            sa.Column("extra", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False, comment="speedbear 等企业语义"),
            sa.Column("remark", sa.Text(), nullable=True),
            sa.Column("created_by", sa.UUID(), nullable=True, comment="登记人弱引用（不建 FK）"),
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True, comment="创建时间"),
            sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间"),
            sa.Column("is_active", sa.Boolean(), nullable=False, comment="是否激活"),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("provider", "tenant_id", "api_base", "credential_sha256", name="uq_channel_credential", postgresql_nulls_not_distinct=True),
        )
        op.create_index("ix_channel_tenant_provider", "model_channels", ["tenant_id", "provider"], unique=False)
        op.create_index(op.f("ix_model_channels_id"), "model_channels", ["id"], unique=False)

    if "model_configs" in missing:
        op.create_table(
            "model_configs",
            sa.Column("model_id", sa.UUID(), nullable=True, comment="基础模型ID"),
            sa.Column("tenant_id", sa.UUID(), nullable=False, comment="租户ID"),
            sa.Column("logo", sa.String(length=255), nullable=True, comment="模型logo图片URL"),
            sa.Column("name", sa.String(), nullable=False, comment="模型显示名称"),
            sa.Column("provider", sa.String(), server_default="composite", nullable=False, comment="供应商"),
            sa.Column("type", sa.String(), nullable=False, comment="模型类型"),
            sa.Column("is_composite", sa.Boolean(), server_default="false", nullable=False, comment="是否为组合模型"),
            sa.Column("description", sa.String(), nullable=True, comment="模型描述"),
            sa.Column("capability", postgresql.ARRAY(sa.String()), server_default=sa.text("'{}'::varchar[]"), nullable=False, comment="模型能力列表（如['vision', 'audio', 'video', 'thinking']）"),
            sa.Column("is_omni", sa.Boolean(), server_default="false", nullable=False, comment="是否为Omni模型（使用特殊API调用）"),
            sa.Column("input_modalities", postgresql.ARRAY(sa.String()), server_default=sa.text("'{}'::varchar[]"), nullable=False, comment="输入模态（如['text','image','audio','video']）"),
            sa.Column("output_modalities", postgresql.ARRAY(sa.String()), server_default=sa.text("'{}'::varchar[]"), nullable=False, comment="输出模态（如['text','image','audio']）"),
            sa.Column("features", postgresql.ARRAY(sa.String()), server_default=sa.text("'{}'::varchar[]"), nullable=False, comment="能力特征（如['thinking','json_output','function_call']）"),
            sa.Column("config", postgresql.JSON(astext_type=sa.Text()), nullable=True, comment="模型配置参数"),
            sa.Column("is_public", sa.Boolean(), nullable=False, comment="是否公开"),
            sa.Column("load_balance_strategy", sa.String(), server_default="none", nullable=True, comment="负载均衡策略"),
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True, comment="创建时间"),
            sa.Column("updated_at", sa.DateTime(), nullable=True, comment="更新时间"),
            sa.Column("is_active", sa.Boolean(), nullable=False, comment="是否激活"),
            sa.ForeignKeyConstraint(["model_id"], ["model_bases.id"]),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_model_configs_id"), "model_configs", ["id"], unique=False)
        op.create_index(op.f("ix_model_configs_model_id"), "model_configs", ["model_id"], unique=False)
        op.create_index(op.f("ix_model_configs_tenant_id"), "model_configs", ["tenant_id"], unique=False)
        op.create_index(op.f("ix_model_configs_type"), "model_configs", ["type"], unique=False)


def downgrade() -> None:
    # 有意 no-op：四表归老单体链，服务链不 drop（详见模块 docstring）
    pass
