"""add_updated_at_to_model_configs
Revision ID: 85270245ff06
Revises: 12a85ac5a2e9
Create Date: 2025-12-06 20:17:07.947597
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
# revision identifiers, used by Alembic.
revision: str = '85270245ff06'
down_revision: Union[str, None] = '12a85ac5a2e9'
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
    """添加 updated_at 字段到 model_configs 表"""
    
    # 检查字段是否已存在
    if not column_exists('model_configs', 'updated_at'):
        # 添加 updated_at 字段
        op.add_column('model_configs',
            sa.Column('updated_at', sa.DateTime(), nullable=True, comment='更新时间')
        )
        
        # 为现有记录设置 updated_at = created_at
        bind = op.get_bind()
        bind.execute(sa.text(
            "UPDATE model_configs SET updated_at = created_at WHERE updated_at IS NULL"
        ))
def downgrade() -> None:
    """移除 updated_at 字段"""
    
    if column_exists('model_configs', 'updated_at'):
        op.drop_column('model_configs', 'updated_at')