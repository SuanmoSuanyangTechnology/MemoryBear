"""2026011240

Revision ID: 325b759cd66b
Revises: 9a936a9ebb20
Create Date: 2026-01-26 12:37:35.946749

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '325b759cd66b'
down_revision: Union[str, None] = '9a936a9ebb20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 重命名表 data_config -> memory_config
    op.rename_table('data_config', 'memory_config')
    
    # 2. 重命名列 group_id -> end_user_id
    op.alter_column('memory_config', 'group_id', new_column_name='end_user_id')
    
    # 3. config_id: INTEGER -> UUID（保留旧值以便回滚）
    op.alter_column('memory_config', 'config_id', new_column_name='config_id_old')
    op.add_column('memory_config', sa.Column('config_id', sa.UUID(), nullable=True))
    op.execute("UPDATE memory_config SET config_id = apply_id::uuid")
    op.drop_constraint('data_config_pkey', 'memory_config', type_='primary')
    op.alter_column('memory_config', 'config_id', nullable=False)
    op.create_primary_key('memory_config_pkey', 'memory_config', ['config_id'])
    op.execute("DROP SEQUENCE IF EXISTS data_config_config_id_seq")


def downgrade() -> None:
    # 1. config_id: UUID -> INTEGER（恢复旧值）
    op.drop_constraint('memory_config_pkey', 'memory_config', type_='primary')
    op.drop_column('memory_config', 'config_id')
    op.alter_column('memory_config', 'config_id_old', new_column_name='config_id')
    op.create_primary_key('data_config_pkey', 'memory_config', ['config_id'])
    op.execute("CREATE SEQUENCE IF NOT EXISTS data_config_config_id_seq OWNED BY memory_config.config_id")
    op.execute("SELECT setval('data_config_config_id_seq', COALESCE((SELECT MAX(config_id) FROM memory_config), 1))")
    
    # 2. 重命名列 end_user_id -> group_id
    op.alter_column('memory_config', 'end_user_id', new_column_name='group_id')
    
    # 3. 重命名表 memory_config -> data_config
    op.rename_table('memory_config', 'data_config')
