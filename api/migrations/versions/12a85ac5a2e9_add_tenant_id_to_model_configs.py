"""add_tenant_id_to_model_configs
Revision ID: 12a85ac5a2e9
Revises: 20a742ef1d93
Create Date: 2025-12-06 19:56:24.947732
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
# revision identifiers, used by Alembic.
revision: str = '12a85ac5a2e9'
down_revision: Union[str, None] = '20a742ef1d93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
def table_exists(table_name: str) -> bool:
    """检查表是否存在"""
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in inspector.get_table_names()
def column_exists(table_name: str, column_name: str) -> bool:
    """检查列是否存在"""
    bind = op.get_bind()
    inspector = inspect(bind)
    if not table_exists(table_name):
        return False
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns
def index_exists(table_name: str, index_name: str) -> bool:
    """检查索引是否存在"""
    bind = op.get_bind()
    inspector = inspect(bind)
    if not table_exists(table_name):
        return False
    indexes = [idx['name'] for idx in inspector.get_indexes(table_name)]
    return index_name in indexes
def constraint_exists(table_name: str, constraint_name: str) -> bool:
    """检查约束是否存在（外键、唯一约束等）"""
    bind = op.get_bind()
    inspector = inspect(bind)
    if not table_exists(table_name):
        return False
    
    # 检查外键约束
    foreign_keys = [fk['name'] for fk in inspector.get_foreign_keys(table_name) if fk['name']]
    if constraint_name in foreign_keys:
        return True
    
    # 检查唯一约束
    unique_constraints = [uc['name'] for uc in inspector.get_unique_constraints(table_name) if uc['name']]
    if constraint_name in unique_constraints:
        return True
    
    # 检查检查约束
    check_constraints = [cc['name'] for cc in inspector.get_check_constraints(table_name) if cc['name']]
    if constraint_name in check_constraints:
        return True
    
    return False
def trigger_exists(trigger_name: str) -> bool:
    """检查触发器是否存在（PostgreSQL）"""
    bind = op.get_bind()
    result = bind.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = :trigger_name)"
    ), {"trigger_name": trigger_name})
    return result.scalar()
def sequence_exists(sequence_name: str) -> bool:
    """检查序列是否存在（PostgreSQL）"""
    bind = op.get_bind()
    result = bind.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM pg_class WHERE relkind = 'S' AND relname = :sequence_name)"
    ), {"sequence_name": sequence_name})
    return result.scalar()
def enum_exists(enum_name: str) -> bool:
    """检查枚举类型是否存在（PostgreSQL）"""
    bind = op.get_bind()
    result = bind.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = :enum_name)"
    ), {"enum_name": enum_name})
    return result.scalar()
def upgrade() -> None:
    """添加 tenant_id 字段到 model_configs 表"""
    
    # 1. 检查并添加字段（如果不存在）
    if not column_exists('model_configs', 'tenant_id'):
        # 添加 tenant_id 字段（先设为可空）
        op.add_column('model_configs', 
            sa.Column('tenant_id', sa.UUID(), nullable=True, comment='租户ID')
        )
        
        # 为现有记录设置默认 tenant_id（使用第一个租户）
        bind = op.get_bind()
        result = bind.execute(sa.text("SELECT id FROM tenants LIMIT 1"))
        default_tenant_id = result.scalar()
        
        if default_tenant_id:
            bind.execute(
                sa.text("UPDATE model_configs SET tenant_id = :tenant_id WHERE tenant_id IS NULL"),
                {"tenant_id": str(default_tenant_id)}
            )
        
        # 设置字段为 NOT NULL
        op.alter_column('model_configs', 'tenant_id', nullable=False)
    
    # 2. 创建索引（独立检查，即使字段已存在也要检查）
    if not index_exists('model_configs', 'ix_model_configs_tenant_id'):
        op.create_index('ix_model_configs_tenant_id', 'model_configs', ['tenant_id'])
    
    # 3. 添加外键约束（独立检查，即使字段已存在也要检查）
    if not constraint_exists('model_configs', 'fk_model_configs_tenant_id'):
        op.create_foreign_key(
            'fk_model_configs_tenant_id',
            'model_configs', 'tenants',
            ['tenant_id'], ['id']
        )
def downgrade() -> None:
    """移除 tenant_id 字段"""
    
    if column_exists('model_configs', 'tenant_id'):
        # 1. 删除外键约束
        if constraint_exists('model_configs', 'fk_model_configs_tenant_id'):
            op.drop_constraint('fk_model_configs_tenant_id', 'model_configs', type_='foreignkey')
        
        # 2. 删除索引
        if index_exists('model_configs', 'ix_model_configs_tenant_id'):
            op.drop_index('ix_model_configs_tenant_id', 'model_configs')
        
        # 3. 删除字段
        op.drop_column('model_configs', 'tenant_id')
