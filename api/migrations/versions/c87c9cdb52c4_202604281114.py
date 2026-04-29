"""202604281114

Revision ID: c87c9cdb52c4
Revises: e2d60c6d1a1a
Create Date: 2026-04-28 11:13:02.441905

"""
from typing import Dict, List, Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c87c9cdb52c4'
down_revision: Union[str, None] = 'e2d60c6d1a1a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BATCH_SIZE = 500


def _chunked(values: List[str], size: int) -> List[List[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def _load_neo4j_end_user_ids(connection) -> List[str]:
    """加载所有需要从 Neo4j 同步 memory_count 的宿主。

    RAG 工作空间的记忆数量以 documents.chunk_num 为准，不写入 end_users.memory_count。
    """
    rows = connection.execute(sa.text("""
        SELECT eu.id::text AS end_user_id
        FROM end_users eu
        JOIN workspaces w ON eu.workspace_id = w.id
        WHERE w.storage_type IS NULL OR w.storage_type <> 'rag'
    """)).all()
    return [row[0] for row in rows]


async def _fetch_neo4j_counts(end_user_ids: List[str]) -> Dict[str, int]:
    if not end_user_ids:
        return {}

    from app.repositories.memory_config_repository import MemoryConfigRepository
    from app.repositories.neo4j.neo4j_connector import Neo4jConnector

    connector = Neo4jConnector()
    try:
        result = await connector.execute_query(
            MemoryConfigRepository.SEARCH_FOR_ALL_BATCH,
            end_user_ids=end_user_ids,
        )
    finally:
        await connector.close()

    counts = {str(row["user_id"]): int(row["total"]) for row in result}
    for end_user_id in end_user_ids:
        counts.setdefault(end_user_id, 0)
    return counts


def _update_memory_counts(connection, counts: Dict[str, int]) -> int:
    updated = 0
    for end_user_id, memory_count in counts.items():
        result = connection.execute(
            sa.text("""
                UPDATE end_users
                SET memory_count = :memory_count
                WHERE id = CAST(:end_user_id AS uuid)
            """),
            {
                "end_user_id": end_user_id,
                "memory_count": memory_count,
            },
        )
        updated += result.rowcount or 0
    return updated


def _sync_memory_count_from_neo4j() -> None:
    """迁移时初始化 Neo4j 模式宿主的 memory_count。

    """
    import asyncio

    print("[memory_count] 开始同步 Neo4j 模式宿主 memory_count")
    connection = op.get_bind()
    target_ids = _load_neo4j_end_user_ids(connection)
    if not target_ids:
        print("[memory_count] 没有需要同步的 Neo4j 模式宿主")
        return

    print(
        f"[memory_count] 待同步宿主数量: {len(target_ids)}, "
        f"batch_size={BATCH_SIZE}"
    )

    total_updated = 0
    batches = _chunked(target_ids, BATCH_SIZE)
    for batch_index, batch_ids in enumerate(batches, start=1):
        print(
            f"[memory_count] 正在查询 Neo4j: "
            f"batch={batch_index}/{len(batches)}, size={len(batch_ids)}"
        )
        counts = asyncio.run(_fetch_neo4j_counts(batch_ids))
        total_updated += _update_memory_counts(connection, counts)
        print(
            f"[memory_count] 已写入 PostgreSQL: "
            f"updated={total_updated}/{len(target_ids)}"
        )

    print(
        f"[memory_count] Neo4j 模式宿主同步完成: "
        f"total={len(target_ids)}, updated={total_updated}"
    )


def upgrade() -> None:
    op.add_column(
        'end_users',
        sa.Column(
            'memory_count',
            sa.Integer(),
            server_default='0',
            nullable=False,
            comment='记忆节点总数',
        ),
    )
    _sync_memory_count_from_neo4j()
    op.create_index(
        op.f('ix_end_users_memory_count'),
        'end_users',
        ['memory_count'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_end_users_memory_count'), table_name='end_users')
    op.drop_column('end_users', 'memory_count')
