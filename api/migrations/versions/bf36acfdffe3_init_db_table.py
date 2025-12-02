"""init_db_table

Revision ID: bf36acfdffe3
Revises: 5de5ec651b01
Create Date: 2025-11-13 11:31:57.671799

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bf36acfdffe3'
down_revision: Union[str, None] = '5de5ec651b01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None



def upgrade():
    # 创建或更新统计信息的函数
    op.execute("""
    CREATE OR REPLACE FUNCTION update_knowledge_stats()
    RETURNS TRIGGER AS $$
    BEGIN
        -- 处理 documents 表的插入、更新或删除
        IF TG_TABLE_NAME = 'documents' THEN
            -- 更新 knowledges 表的 doc_num
            UPDATE knowledges 
            SET doc_num = (
                SELECT COUNT(*) 
                FROM documents 
                WHERE kb_id = knowledges.id AND status = 1
            )
            WHERE id = NEW.kb_id OR id = OLD.kb_id;
            
            -- 更新 knowledges 表的 chunk_num
            UPDATE knowledges 
            SET chunk_num = (
                SELECT COALESCE(SUM(chunk_num), 0) 
                FROM documents 
                WHERE kb_id = knowledges.id AND status = 1
            )
            WHERE id = NEW.kb_id OR id = OLD.kb_id;
            
            -- 通过 knowledge_shares 表同步统计信息
            -- 1. 使用 source_kb_id 的 doc_num 更新 target_kb_id 的 doc_num
            UPDATE knowledges AS target
            SET doc_num = source.doc_num
            FROM knowledge_shares ks
            JOIN knowledges AS source ON source.id = ks.source_kb_id
            WHERE ks.target_kb_id = target.id 
            AND (source.id = NEW.kb_id OR source.id = OLD.kb_id);
            
            -- 2. 使用 source_kb_id 的 chunk_num 更新 target_kb_id 的 chunk_num
            UPDATE knowledges AS target
            SET chunk_num = source.chunk_num
            FROM knowledge_shares ks
            JOIN knowledges AS source ON source.id = ks.source_kb_id
            WHERE ks.target_kb_id = target.id 
            AND (source.id = NEW.kb_id OR source.id = OLD.kb_id);
        
        END IF;
        
        RETURN NULL;
    END;
    $$ LANGUAGE plpgsql;
    """)
    
    # 创建 documents 表上的触发器
    op.execute("""
    CREATE TRIGGER tr_documents_update_stats
    AFTER INSERT OR UPDATE OR DELETE ON documents
    FOR EACH ROW
    EXECUTE FUNCTION update_knowledge_stats();
    """)


def downgrade():
    # 删除触发器
    op.execute("DROP TRIGGER IF EXISTS tr_documents_update_stats ON documents;")
    
    # 删除函数
    op.execute("DROP FUNCTION IF EXISTS update_knowledge_stats();")
