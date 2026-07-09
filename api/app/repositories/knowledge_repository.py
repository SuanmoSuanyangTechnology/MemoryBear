import uuid
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, selectinload
from app.models.document_model import Document
from app.models.knowledge_model import Knowledge, PermissionType
from app.schemas import knowledge_schema
from app.core.logging_config import get_db_logger

# Obtain a dedicated logger for the database
db_logger = get_db_logger()


def _knowledge_relationship_load_options():
    return (
        selectinload(Knowledge.created_user),
        selectinload(Knowledge.embedding),
        selectinload(Knowledge.reranker),
        selectinload(Knowledge.llm),
        selectinload(Knowledge.image2text),
    )


def get_knowledges_paginated(
        db: Session,
        filters: list,
        page: int,
        pagesize: int,
        orderby: str = None,
        desc: bool = False
) -> tuple[int, list]:
    """
    Paged query knowledge base (with filtering and sorting)
    """
    db_logger.debug(f"Query knowledge base in pages: page={page}, pagesize={pagesize}, orderby={orderby}, desc={desc}, filters_count={len(filters)}")
    
    try:
        query = db.query(Knowledge)

        # Apply filter conditions
        for filter_cond in filters:
            query = query.filter(filter_cond)

        # Calculate the total count (for pagination)
        total = query.count()
        db_logger.debug(f"Total number of knowledge base queries: {total}")

        # sort
        if orderby:
            order_attr = getattr(Knowledge, orderby, None)
            if order_attr is not None:
                if desc:
                    query = query.order_by(order_attr.desc())
                else:
                    query = query.order_by(order_attr.asc())
                db_logger.debug(f"sort: {orderby}, desc={desc}")

        # pagination
        items = query.offset((page - 1) * pagesize).limit(pagesize).all()
        db_logger.info(f"The knowledge base paging query has been successful: total={total}, Number of current page={len(items)}")

        return total, [knowledge_schema.Knowledge.model_validate(item) for item in items]
    except Exception as e:
        db_logger.error(f"Querying knowledge base pagination failed: page={page}, pagesize={pagesize} - {str(e)}")
        raise


async def get_knowledges_paginated_async(
        db: AsyncSession,
        filters: list,
        page: int,
        pagesize: int,
        orderby: str = None,
        desc: bool = False
) -> tuple[int, list]:
    """Async version of get_knowledges_paginated."""
    db_logger.debug(
        f"Query knowledge base in pages (async): page={page}, pagesize={pagesize}, "
        f"orderby={orderby}, desc={desc}, filters_count={len(filters)}"
    )

    try:
        stmt = select(Knowledge).options(*_knowledge_relationship_load_options())
        for filter_cond in filters:
            stmt = stmt.where(filter_cond)

        total_result = await db.execute(select(func.count()).select_from(stmt.subquery()))
        total = total_result.scalar_one()

        if orderby:
            order_attr = getattr(Knowledge, orderby, None)
            if order_attr is not None:
                stmt = stmt.order_by(order_attr.desc() if desc else order_attr.asc())

        stmt = stmt.offset((page - 1) * pagesize).limit(pagesize)
        result = await db.execute(stmt)
        items = result.scalars().all()
        db_logger.info(
            f"The knowledge base paging query has been successful (async): "
            f"total={total}, Number of current page={len(items)}"
        )
        return total, [knowledge_schema.Knowledge.model_validate(item) for item in items]
    except Exception as e:
        db_logger.error(
            f"Querying knowledge base pagination failed (async): page={page}, pagesize={pagesize} - {str(e)}"
        )
        raise


def get_chunked_knowledgeids(
        db: Session,
        filters: list
) -> list:
    """
    Query the list of vectorized knowledge base IDs
    Return: list[(id,workspace_id)] - List of knowledge base id and workspace_id
    """
    db_logger.debug(f"Query the list of vectorized knowledge base IDs: filters_count={len(filters)}")

    try:
        # Only query the id field
        query = db.query(Knowledge.id, Knowledge.workspace_id)

        # Apply filter conditions
        for filter_cond in filters:
            query = query.filter(filter_cond)

        # Get all IDs
        items = query.all()
        db_logger.info(f"Querying the vectorized knowledge base id list succeeded: count={len(items)}")

        # Return the list of ID and workspace_id directly. Since only the ID and workspace_id field is queried
        return items
    except Exception as e:
        db_logger.error(f"Querying the vectorized knowledge base id list failed: {str(e)}")
        raise


async def get_chunked_knowledgeids_async(
        db: AsyncSession,
        filters: list
) -> list:
    """Async version of get_chunked_knowledgeids."""
    try:
        stmt = select(Knowledge.id, Knowledge.workspace_id)
        for filter_cond in filters:
            stmt = stmt.where(filter_cond)
        result = await db.execute(stmt)
        return result.all()
    except Exception as e:
        db_logger.error(f"Querying vectorized knowledge IDs failed (async): {str(e)}")
        raise


def create_knowledge(db: Session, knowledge: knowledge_schema.KnowledgeCreate) -> Knowledge:
    db_logger.debug(f"Create a knowledge base record: name={knowledge.name}")
    
    try:
        db_knowledge = Knowledge(**knowledge.model_dump())
        db.add(db_knowledge)
        db.commit()
        db_logger.info(f"knowledge base record created successfully: {knowledge.name} (ID: {db_knowledge.id})")
        return db_knowledge
    except Exception as e:
        db_logger.error(f"Failed to create a knowledge base record: name={knowledge.name} - {str(e)}")
        db.rollback()
        raise


async def create_knowledge_async(db: AsyncSession, knowledge: knowledge_schema.KnowledgeCreate) -> Knowledge:
    """Async version of create_knowledge."""
    db_logger.debug(f"Create a knowledge base record (async): name={knowledge.name}")

    try:
        db_knowledge = Knowledge(**knowledge.model_dump())
        db.add(db_knowledge)
        await db.commit()
        await db.refresh(db_knowledge)
        loaded_knowledge = await get_knowledge_by_id_async(db, db_knowledge.id)
        db_logger.info(f"knowledge base record created successfully (async): {knowledge.name} (ID: {db_knowledge.id})")
        return loaded_knowledge or db_knowledge
    except Exception as e:
        db_logger.error(f"Failed to create a knowledge base record (async): name={knowledge.name} - {str(e)}")
        await db.rollback()
        raise


def get_knowledge_by_id(db: Session, knowledge_id: uuid.UUID) -> Knowledge | None:
    db_logger.debug(f"Query knowledge base based on ID: knowledge_id={knowledge_id}")

    try:
        knowledge = db.query(Knowledge).filter(Knowledge.id == knowledge_id).first()
        if knowledge:
            db_logger.debug(f"knowledge base query successful: {knowledge.name} (ID: {knowledge_id})")
        else:
            db_logger.debug(f"knowledge base does not exist: knowledge_id={knowledge_id}")
        return knowledge
    except Exception as e:
        db_logger.error(f"Failed to query the knowledge base based on the ID: knowledge_id={knowledge_id} - {str(e)}")
        raise


async def get_knowledge_by_id_async(db: AsyncSession, knowledge_id: uuid.UUID) -> Knowledge | None:
    """Async version of get_knowledge_by_id."""
    db_logger.debug(f"Query knowledge base based on ID (async): knowledge_id={knowledge_id}")

    try:
        stmt = select(Knowledge).options(*_knowledge_relationship_load_options()).where(Knowledge.id == knowledge_id)
        result = await db.execute(stmt)
        knowledge = result.scalars().first()
        if knowledge:
            db_logger.debug(f"knowledge base query successful (async): {knowledge.name} (ID: {knowledge_id})")
        else:
            db_logger.debug(f"knowledge base does not exist (async): knowledge_id={knowledge_id}")
        return knowledge
    except Exception as e:
        db_logger.error(f"Failed to query the knowledge base based on the ID (async): knowledge_id={knowledge_id} - {str(e)}")
        raise


def get_knowledge_by_external_id(db: Session, external_id: str, workspace_id: uuid.UUID) -> Knowledge | None:
    db_logger.debug(f"Query knowledge base based on external_id: external_id={external_id}, workspace_id={workspace_id}")

    try:
        knowledge = db.query(Knowledge).filter(
            Knowledge.external_id == external_id,
            Knowledge.workspace_id == workspace_id,
            Knowledge.status == 1
        ).first()
        if knowledge:
            db_logger.debug(f"knowledge base query successful: {knowledge.name} (external_id: {external_id})")
        else:
            db_logger.debug(f"knowledge base does not exist: external_id={external_id}")
        return knowledge
    except Exception as e:
        db_logger.error(f"Failed to query the knowledge base based on external_id: external_id={external_id} - {str(e)}")
        raise


async def get_knowledge_by_external_id_async(
        db: AsyncSession,
        external_id: str,
        workspace_id: uuid.UUID
) -> Knowledge | None:
    """Async version of get_knowledge_by_external_id."""
    try:
        stmt = select(Knowledge).options(*_knowledge_relationship_load_options()).where(
            Knowledge.external_id == external_id,
            Knowledge.workspace_id == workspace_id,
            Knowledge.status == 1,
        )
        result = await db.execute(stmt)
        return result.scalars().first()
    except Exception as e:
        db_logger.error(f"Failed to query knowledge by external_id (async): external_id={external_id} - {str(e)}")
        raise


def get_knowledge_ids_by_external_ids(db: Session, external_ids: list[str], workspace_id: uuid.UUID) -> list[uuid.UUID]:
    """解析 external_ids 为 knowledge UUID 列表（仅返回存在的）"""
    db_logger.debug(f"Resolve external_ids to knowledge UUIDs: external_ids={external_ids}, workspace_id={workspace_id}")

    try:
        results = db.query(Knowledge.id).filter(
            Knowledge.external_id.in_(external_ids),
            Knowledge.workspace_id == workspace_id,
            Knowledge.status == 1
        ).all()
        ids = [r[0] for r in results]
        db_logger.debug(f"Resolved {len(external_ids)} external_ids to {len(ids)} UUIDs")
        return ids
    except Exception as e:
        db_logger.error(f"Failed to resolve external_ids: external_ids={external_ids} - {str(e)}")
        raise


async def get_knowledge_ids_by_external_ids_async(
        db: AsyncSession,
        external_ids: list[str],
        workspace_id: uuid.UUID
) -> list[uuid.UUID]:
    """Async version of get_knowledge_ids_by_external_ids."""
    try:
        stmt = select(Knowledge.id).where(
            Knowledge.external_id.in_(external_ids),
            Knowledge.workspace_id == workspace_id,
            Knowledge.status == 1,
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())
    except Exception as e:
        db_logger.error(f"Failed to resolve external_ids (async): external_ids={external_ids} - {str(e)}")
        raise


def get_knowledges_by_parent_id(db: Session, parent_id: uuid.UUID) -> list[Knowledge]:
    db_logger.debug(f"Query knowledge bases based on parent ID: parent_id={parent_id}")
    try:
        knowledges = db.query(Knowledge).filter(Knowledge.parent_id == parent_id, Knowledge.status == 1).all()
        if knowledges:
            db_logger.debug(f"Knowledge bases query successful: count={len(knowledges)} (parent_id: {parent_id})")
        else:
            db_logger.debug(f"No knowledge bases found for given parent: parent_id={parent_id}")
        return knowledges
    except Exception as e:
        db_logger.error(f"Failed to query the knowledge bases based on parent ID: parent_id={parent_id} - {str(e)}")
        raise


async def get_knowledges_by_parent_id_async(db: AsyncSession, parent_id: uuid.UUID) -> list[Knowledge]:
    """Async version of get_knowledges_by_parent_id."""
    try:
        stmt = select(Knowledge).where(Knowledge.parent_id == parent_id, Knowledge.status == 1)
        result = await db.execute(stmt)
        return list(result.scalars().all())
    except Exception as e:
        db_logger.error(f"Failed to query knowledge bases by parent ID (async): parent_id={parent_id} - {str(e)}")
        raise


def get_knowledges_by_parent_ids(
        db: Session,
        parent_ids: list[uuid.UUID],
        workspace_id: uuid.UUID,
) -> list[knowledge_schema.Knowledge]:
    db_logger.debug(
        f"Batch query knowledge bases by parent IDs: parent_count={len(parent_ids)}, workspace_id={workspace_id}"
    )
    if not parent_ids:
        return []

    try:
        knowledges = (
            db.query(Knowledge)
            .filter(
                Knowledge.parent_id.in_(parent_ids),
                Knowledge.workspace_id == workspace_id,
                Knowledge.status != 2,
                Knowledge.permission_id != PermissionType.Memory,
            )
            .all()
        )
        db_logger.debug(
            f"Batch knowledge bases query successful: parent_count={len(parent_ids)}, count={len(knowledges)}"
        )
        return [knowledge_schema.Knowledge.model_validate(item) for item in knowledges]
    except Exception as e:
        db_logger.error(
            f"Failed to batch query knowledge bases by parent IDs: workspace_id={workspace_id} - {str(e)}"
        )
        raise


async def get_knowledges_by_parent_ids_async(
        db: AsyncSession,
        parent_ids: list[uuid.UUID],
        workspace_id: uuid.UUID,
) -> list[knowledge_schema.Knowledge]:
    """Async version of get_knowledges_by_parent_ids."""
    if not parent_ids:
        return []

    try:
        stmt = select(Knowledge).options(*_knowledge_relationship_load_options()).where(
            Knowledge.parent_id.in_(parent_ids),
            Knowledge.workspace_id == workspace_id,
            Knowledge.status != 2,
            Knowledge.permission_id != PermissionType.Memory,
        )
        result = await db.execute(stmt)
        return [knowledge_schema.Knowledge.model_validate(item) for item in result.scalars().all()]
    except Exception as e:
        db_logger.error(f"Failed to batch query knowledge bases by parent IDs (async): workspace_id={workspace_id} - {str(e)}")
        raise


def get_document_counts_by_knowledge_ids(
        db: Session,
        knowledge_ids: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    db_logger.debug(
        f"Query document counts by knowledge IDs: knowledge_count={len(knowledge_ids)}"
    )
    if not knowledge_ids:
        return {}

    unique_knowledge_ids = list(dict.fromkeys(knowledge_ids))

    try:
        document_count_rows = (
            db.query(Document.kb_id, func.count(Document.id))
            .filter(
                Document.kb_id.in_(unique_knowledge_ids),
                Document.status == 1,
            )
            .group_by(Document.kb_id)
            .all()
        )
        return {
            kb_id: int(count)
            for kb_id, count in document_count_rows
        }
    except Exception as e:
        db_logger.error(
            f"Failed to query document counts by knowledge IDs: {str(e)}"
        )
        raise


async def get_document_counts_by_knowledge_ids_async(
        db: AsyncSession,
        knowledge_ids: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    """Async version of get_document_counts_by_knowledge_ids."""
    if not knowledge_ids:
        return {}

    unique_knowledge_ids = list(dict.fromkeys(knowledge_ids))
    try:
        stmt = (
            select(Document.kb_id, func.count(Document.id))
            .where(
                Document.kb_id.in_(unique_knowledge_ids),
                Document.status == 1,
            )
            .group_by(Document.kb_id)
        )
        result = await db.execute(stmt)
        return {kb_id: int(count) for kb_id, count in result.all()}
    except Exception as e:
        db_logger.error(f"Failed to query document counts by knowledge IDs (async): {str(e)}")
        raise


def get_knowledge_by_name(db: Session, name: str, workspace_id: uuid.UUID) -> Knowledge | None:
    db_logger.debug(f"Query knowledge base based on name and workspace_id: name={name}, workspace_id={workspace_id}")

    try:
        knowledge = db.query(Knowledge).filter(Knowledge.name == name,
                                               Knowledge.workspace_id == workspace_id,
                                               Knowledge.status == 1).first()
        if knowledge:
            db_logger.debug(f"knowledge base query successful: {name} (ID: {knowledge.id})")
        else:
            db_logger.debug(f"knowledge base does not exist: name={name}, workspace_id={workspace_id}")
        return knowledge
    except Exception as e:
        db_logger.error(f"Failed to query the knowledge base based on the name and workspace_id: name={name}, workspace_id={workspace_id} - {str(e)}")
        raise


async def get_knowledge_by_name_async(db: AsyncSession, name: str, workspace_id: uuid.UUID) -> Knowledge | None:
    """Async version of get_knowledge_by_name."""
    db_logger.debug(f"Query knowledge base based on name and workspace_id (async): name={name}, workspace_id={workspace_id}")

    try:
        stmt = (
            select(Knowledge)
            .options(*_knowledge_relationship_load_options())
            .where(Knowledge.name == name, Knowledge.workspace_id == workspace_id, Knowledge.status == 1)
        )
        result = await db.execute(stmt)
        knowledge = result.scalars().first()
        if knowledge:
            db_logger.debug(f"knowledge base query successful (async): {name} (ID: {knowledge.id})")
        else:
            db_logger.debug(f"knowledge base does not exist (async): name={name}, workspace_id={workspace_id}")
        return knowledge
    except Exception as e:
        db_logger.error(f"Failed to query the knowledge base based on the name and workspace_id (async): {str(e)}")
        raise


def delete_knowledge_by_id(db: Session, knowledge_id: uuid.UUID):
    db_logger.debug(f"Delete knowledge base record: knowledge_id={knowledge_id}")
    
    try:
        # First, query the knowledge base information for logging purposes
        knowledge = db.query(Knowledge).filter(Knowledge.id == knowledge_id).first()
        if knowledge:
            knowledge_name = knowledge.name
        else:
            knowledge_name = "unknown"
            
        result = db.query(Knowledge).filter(Knowledge.id == knowledge_id).delete()
        db.commit()
        
        if result > 0:
            db_logger.info(f"knowledge base record deleted successfully: {knowledge_name} (ID: {knowledge_id})")
        else:
            db_logger.warning(f"The knowledge base record does not exist, and cannot be deleted: knowledge_id={knowledge_id}")
    except Exception as e:
        db_logger.error(f"Failed to delete knowledge base record: knowledge_id={knowledge_id} - {str(e)}")
        db.rollback()
        raise


async def delete_knowledge_by_id_async(db: AsyncSession, knowledge_id: uuid.UUID):
    """Async version of delete_knowledge_by_id."""
    try:
        knowledge = await get_knowledge_by_id_async(db, knowledge_id)
        knowledge_name = knowledge.name if knowledge else "unknown"

        result = await db.execute(delete(Knowledge).where(Knowledge.id == knowledge_id))
        await db.commit()

        if result.rowcount and result.rowcount > 0:
            db_logger.info(f"knowledge base record deleted successfully (async): {knowledge_name} (ID: {knowledge_id})")
        else:
            db_logger.warning(f"The knowledge base record does not exist, and cannot be deleted (async): knowledge_id={knowledge_id}")
    except Exception as e:
        db_logger.error(f"Failed to delete knowledge base record (async): knowledge_id={knowledge_id} - {str(e)}")
        await db.rollback()
        raise


def get_total_doc_num_by_workspace(db: Session, workspace_id: uuid.UUID) -> int:
    """
    根据workspace_id查询knowledges表所有doc_num的总和
    """
    db_logger.debug(f"Query total doc_num by workspace_id: workspace_id={workspace_id}")
    
    try:
        from sqlalchemy import func
        result = db.query(func.sum(Knowledge.doc_num)).filter(
            Knowledge.workspace_id == workspace_id,
            Knowledge.status == 1
        ).scalar()
        
        total = result if result is not None else 0
        db_logger.info(f"Total doc_num query successful: workspace_id={workspace_id}, total={total}")
        return total
    except Exception as e:
        db_logger.error(f"Failed to query total doc_num: workspace_id={workspace_id} - {str(e)}")
        raise


def get_total_chunk_num_by_workspace(db: Session, workspace_id: uuid.UUID) -> int:
    """
    根据workspace_id查询knowledges表所有chunk_num的总和
    """
    db_logger.debug(f"Query total chunk_num by workspace_id: workspace_id={workspace_id}")
    
    try:
        from sqlalchemy import func
        result = db.query(func.sum(Knowledge.chunk_num)).filter(
            Knowledge.workspace_id == workspace_id,
            Knowledge.status == 1
        ).scalar()
        
        total = result if result is not None else 0
        db_logger.info(f"Total chunk_num query successful: workspace_id={workspace_id}, total={total}")
        return total
    except Exception as e:
        db_logger.error(f"Failed to query total chunk_num: workspace_id={workspace_id} - {str(e)}")
        raise


def get_total_kb_count_by_workspace(db: Session, workspace_id: uuid.UUID) -> int:
    """
    根据workspace_id查询knowledges表所有不同id的数量（知识库总数）
    """
    db_logger.debug(f"Query total knowledge base count by workspace_id: workspace_id={workspace_id}")
    
    try:
        count = db.query(Knowledge).filter(
            Knowledge.workspace_id == workspace_id,
            Knowledge.status == 1
        ).count()
        
        db_logger.info(f"Total knowledge base count query successful: workspace_id={workspace_id}, count={count}")
        return count
    except Exception as e:
        db_logger.error(f"Failed to query total knowledge base count: workspace_id={workspace_id} - {str(e)}")
        raise


def get_user_kb_chunk_num_by_workspace(db: Session, workspace_id: uuid.UUID) -> int:
    """
    根据workspace_id查询knowledges表中permission_id='Memory'（用户知识库）的chunk_num总和
    """
    db_logger.debug(f"Query user KB chunk_num by workspace_id: workspace_id={workspace_id}")

    try:
        from sqlalchemy import func
        result = db.query(func.sum(Knowledge.chunk_num)).filter(
            Knowledge.workspace_id == workspace_id,
            Knowledge.status == 1,
            Knowledge.permission_id == "Memory"
        ).scalar()

        total = result if result is not None else 0
        db_logger.info(f"User KB chunk_num query successful: workspace_id={workspace_id}, total={total}")
        return total
    except Exception as e:
        db_logger.error(f"Failed to query user KB chunk_num: workspace_id={workspace_id} - {str(e)}")
        raise


def get_non_user_kb_count_by_workspace(db: Session, workspace_id: uuid.UUID) -> int:
    """
    根据workspace_id查询knowledges表中排除用户知识库（permission_id!='Memory'）的数量
    """
    db_logger.debug(f"Query non-user KB count by workspace_id: workspace_id={workspace_id}")

    try:
        count = db.query(Knowledge).filter(
            Knowledge.workspace_id == workspace_id,
            Knowledge.status == 1,
            Knowledge.permission_id != "Memory"
        ).count()

        db_logger.info(f"Non-user KB count query successful: workspace_id={workspace_id}, count={count}")
        return count
    except Exception as e:
        db_logger.error(f"Failed to query non-user KB count: workspace_id={workspace_id} - {str(e)}")
        raise
