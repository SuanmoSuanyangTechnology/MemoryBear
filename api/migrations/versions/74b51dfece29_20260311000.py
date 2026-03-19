"""20260311000

Revision ID: 74b51dfece29
Revises: f017efe4831c
Create Date: 2026-03-19 10:15:42.488027

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '74b51dfece29'
down_revision: Union[str, None] = 'f017efe4831c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 先删除旧的触发器（如果存在）
    op.execute("DROP TRIGGER IF EXISTS tr_documents_update_stats ON documents;")

    # 创建或更新 knowledges 统计信息的函数
    op.execute("""
CREATE OR REPLACE FUNCTION update_knowledge_stats()
RETURNS TRIGGER AS $$
DECLARE
    -- 声明变量用于存储当前处理的知识库ID
    current_kb_id UUID;
    -- 声明变量用于存储文件夹知识库ID（如果存在）
    folder_kb_id UUID;
    -- 声明变量用于存储递归查询结果
    folder_ids UUID[];
BEGIN
    -- 处理 documents 表的插入、更新或删除
    IF TG_TABLE_NAME = 'documents' THEN
        -- 1. 更新 knowledges 表的 doc_num
        UPDATE knowledges SET doc_num = (
            SELECT COUNT(*) FROM documents
            WHERE kb_id = knowledges.id AND status = 1
        )
        WHERE id = NEW.kb_id OR id = OLD.kb_id;

        -- 2. 更新 knowledges 表的 chunk_num
        UPDATE knowledges SET chunk_num = (
            SELECT COALESCE(SUM(chunk_num), 0) FROM documents
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

        -- 处理文件夹知识库的统计更新
        -- 获取当前处理的知识库ID（可能是NEW或OLD中的kb_id）
        IF NEW.kb_id IS NOT NULL THEN
            current_kb_id := NEW.kb_id;
        ELSIF OLD.kb_id IS NOT NULL THEN
            current_kb_id := OLD.kb_id;
        ELSE
            RETURN NULL;
        END IF;

        -- 查找当前知识库的父文件夹（如果有）
        SELECT id INTO folder_kb_id FROM knowledges
        WHERE id IN (
            SELECT parent_id FROM knowledges WHERE id = current_kb_id
        ) AND type = 'Folder';

        -- 如果存在父文件夹，递归处理所有父文件夹
        IF folder_kb_id IS NOT NULL THEN
            -- 使用递归CTE获取所有父文件夹ID（包括多级嵌套）
            WITH RECURSIVE folder_hierarchy AS (
                -- 基础查询：获取直接父文件夹
                SELECT id FROM knowledges
                WHERE id = folder_kb_id AND type = 'Folder'
                UNION ALL
                -- 递归查询：获取父文件夹的父文件夹
                SELECT k.id FROM knowledges k
                JOIN folder_hierarchy fh ON k.id = k.parent_id
                WHERE k.type = 'Folder'
            )
            -- 将结果存入数组以便处理
            SELECT array_agg(id) INTO folder_ids FROM folder_hierarchy;

            -- 遍历所有父文件夹并更新统计信息
            FOR i IN 1..array_length(folder_ids, 1) LOOP
                -- 更新文件夹的doc_num（汇总所有子知识库的doc_num）
                UPDATE knowledges SET doc_num = (
                    -- 汇总直接子知识库的doc_num
                    SELECT COALESCE(SUM(child.doc_num), 0)
                    FROM knowledges child
                    WHERE child.parent_id = folder_ids[i] AND child.status = 1
                    -- 加上直接属于该文件夹的文档数（如果有）
                    UNION ALL
                    SELECT COALESCE(COUNT(*), 0)
                    FROM documents
                    WHERE kb_id = folder_ids[i] AND status = 1
                    LIMIT 1
                )
                WHERE id = folder_ids[i];

                -- 更新文件夹的chunk_num（汇总所有子知识库的chunk_num）
                UPDATE knowledges SET chunk_num = (
                    -- 汇总直接子知识库的chunk_num
                    SELECT COALESCE(SUM(child.chunk_num), 0)
                    FROM knowledges child
                    WHERE child.parent_id = folder_ids[i] AND child.status = 1
                    -- 加上直接属于该文件夹的文档的chunk_num（如果有）
                    UNION ALL
                    SELECT COALESCE(SUM(d.chunk_num), 0)
                    FROM documents d
                    WHERE d.kb_id = folder_ids[i] AND d.status = 1
                    LIMIT 1
                )
                WHERE id = folder_ids[i];
            END LOOP;
        END IF;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
    """)

    # documents 表上的触发器（插入、更新、删除后）
    op.execute("""
CREATE TRIGGER tr_documents_update_stats
    AFTER INSERT OR UPDATE OR DELETE ON documents
    FOR EACH ROW
    EXECUTE FUNCTION update_knowledge_stats();
    """)


def downgrade() -> None:
    # 删除触发器
    op.execute("DROP TRIGGER IF EXISTS tr_documents_update_stats ON documents;")
    # 删除函数
    op.execute("DROP FUNCTION IF EXISTS update_knowledge_stats();")

