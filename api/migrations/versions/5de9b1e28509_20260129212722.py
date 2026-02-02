"""20260129212722

Revision ID: 5de9b1e28509
Revises: 5ca246ee7dd4
Create Date: 2026-01-29 21:34:30.978031

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '5de9b1e28509'
down_revision: Union[str, None] = '5ca246ee7dd4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Neo4j migration: rename group_id to end_user_id
    import asyncio

    from app.repositories.neo4j.neo4j_connector import Neo4jConnector
    
    async def run_neo4j_upgrade():
        connector = Neo4jConnector()
        try:
            async def transaction_func(tx):
                result = await tx.run("""
                    MATCH (n)
                    WHERE n.group_id IS NOT NULL
                    SET n.end_user_id = n.group_id
                    REMOVE n.group_id
                    WITH count(n) AS node_count
                    MATCH ()-[r]->()
                    WHERE r.group_id IS NOT NULL
                    SET r.end_user_id = r.group_id
                    REMOVE r.group_id
                    RETURN node_count, count(r) AS rel_count
                """)
                return await result.data()
            
            await connector.execute_write_transaction(transaction_func)
        finally:
            await connector.close()
    
    asyncio.run(run_neo4j_upgrade())


def downgrade() -> None:
    # Neo4j migration: rename end_user_id back to group_id
    import asyncio

    from app.repositories.neo4j.neo4j_connector import Neo4jConnector
    
    async def run_neo4j_downgrade():
        connector = Neo4jConnector()
        try:
            async def transaction_func(tx):
                result = await tx.run("""
                    MATCH (n)
                    WHERE n.end_user_id IS NOT NULL
                    SET n.group_id = n.end_user_id
                    REMOVE n.end_user_id
                    WITH count(n) AS node_count
                    MATCH ()-[r]->()
                    WHERE r.end_user_id IS NOT NULL
                    SET r.group_id = r.end_user_id
                    REMOVE r.end_user_id
                    RETURN node_count, count(r) AS rel_count
                """)
                return await result.data()
            
            await connector.execute_write_transaction(transaction_func)
        finally:
            await connector.close()
    
    asyncio.run(run_neo4j_downgrade())