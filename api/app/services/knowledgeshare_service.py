import uuid
from typing import NoReturn

from error_core.codes import BizCode
from error_core.knowledge import knowledge_message
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.i18n.exceptions import get_current_locale
from app.models.knowledge_model import Knowledge, KnowledgeType, PermissionType
from app.models.knowledgeshare_model import KnowledgeShare
from app.models.user_model import User
from app.models.workspace_model import Workspace
from app.repositories import knowledge_repository, knowledgeshare_repository
from app.schemas.knowledge_schema import KnowledgeCreate
from app.schemas.knowledgeshare_schema import KnowledgeShareCreate, KnowledgeShareRequest
from app.services import knowledge_service

# Obtain a dedicated logger for business logic
business_logger = get_business_logger()


def get_knowledgeshares_paginated(
        db: Session,
        current_user: User,
        filters: list,
        page: int,
        pagesize: int,
        orderby: str = None,
        desc: bool = False
) -> tuple[int, list]:
    business_logger.debug(f"Query knowledge base sharing in pages: username={current_user.username}, page={page}, pagesize={pagesize}, orderby={orderby}, desc={desc}")

    try:
        total, items = knowledgeshare_repository.get_knowledgeshares_paginated(
            db=db,
            filters=filters,
            page=page,
            pagesize=pagesize,
            orderby=orderby,
            desc=desc
        )
        business_logger.info(f"The knowledge base sharing paging query has been successful: username={current_user.username}, total={total}, Number of current page={len(items)}")
        return total, items
    except Exception as e:
        business_logger.error(f"Querying knowledge base sharing pagination failed: username={current_user.username} - {str(e)}")
        raise


async def get_knowledgeshares_paginated_async(
        db: AsyncSession,
        current_user: User,
        filters: list,
        page: int,
        pagesize: int,
        orderby: str = None,
        desc: bool = False
) -> tuple[int, list]:
    business_logger.debug(
        f"Query knowledge base sharing in pages (async): username={current_user.username}, "
        f"page={page}, pagesize={pagesize}, orderby={orderby}, desc={desc}"
    )

    try:
        return await knowledgeshare_repository.get_knowledgeshares_paginated_async(
            db=db,
            filters=filters,
            page=page,
            pagesize=pagesize,
            orderby=orderby,
            desc=desc,
        )
    except Exception as e:
        business_logger.error(f"Querying knowledge base sharing pagination failed (async): username={current_user.username} - {str(e)}")
        raise


def get_source_kb_ids_by_target_kb_id(
        db: Session,
        current_user: User,
        filters: list
) -> list:
    business_logger.debug(f"Query the original knowledge base id list by sharing the knowledge base: username={current_user.username}")

    try:
        items = knowledgeshare_repository.get_source_kb_ids_by_target_kb_id(
            db=db,
            filters=filters
        )
        business_logger.info(f"Successfully queried the original knowledge base ID list by sharing the knowledge base: username={current_user.username} count={len(items)}")
        return items
    except Exception as e:
        business_logger.error(f"Failed to query the original knowledge base ID list through knowledge base sharing: username={current_user.username} - {str(e)}")
        raise


async def get_source_kb_ids_by_target_kb_id_async(
        db: AsyncSession,
        current_user: User,
        filters: list
) -> list:
    try:
        return await knowledgeshare_repository.get_source_kb_ids_by_target_kb_id_async(
            db=db,
            filters=filters,
        )
    except Exception as e:
        business_logger.error(
            f"Failed to query source KB ID list through knowledge share (async): "
            f"username={current_user.username} - {str(e)}"
        )
        raise


def create_knowledgeshare(
        db: Session, knowledgeshare: KnowledgeShareCreate, current_user: User
) -> KnowledgeShare:
    business_logger.info(f"Create a knowledge base sharing: creator: {current_user.username}")

    try:
        knowledgeshare.source_workspace_id = current_user.current_workspace_id
        knowledgeshare.shared_by = current_user.id
        business_logger.debug("Start creating a knowledge base sharing")
        db_knowledgeshare = knowledgeshare_repository.create_knowledgeshare(
            db=db, knowledgeshare=knowledgeshare
        )
        business_logger.info(f"knowledge base sharing created successfully: (ID: {db_knowledgeshare.id}), creator: {current_user.username}")
        return db_knowledgeshare
    except Exception as e:
        business_logger.error(f"Failed to create a knowledge base sharing - {str(e)}")
        raise


async def create_knowledgeshare_async(
        db: AsyncSession, knowledgeshare: KnowledgeShareCreate, current_user: User
) -> KnowledgeShare:
    business_logger.info(f"Create a knowledge base sharing (async): creator: {current_user.username}")

    try:
        knowledgeshare.source_workspace_id = current_user.current_workspace_id
        knowledgeshare.shared_by = current_user.id
        return await knowledgeshare_repository.create_knowledgeshare_async(
            db=db, knowledgeshare=knowledgeshare
        )
    except Exception as e:
        business_logger.error(f"Failed to create knowledge base sharing (async) - {str(e)}")
        raise


def get_knowledgeshare_by_id(db: Session, knowledgeshare_id: uuid.UUID, current_user: User) -> KnowledgeShare | None:
    business_logger.debug(f"Query knowledge base sharing based on ID: knowledgeshare_id={knowledgeshare_id}, username: {current_user.username}")

    try:
        knowledgeshare = knowledgeshare_repository.get_knowledgeshare_by_id(db=db, knowledgeshare_id=knowledgeshare_id)
        if knowledgeshare:
            business_logger.info(f"knowledge base sharing query successful: (ID: {knowledgeshare_id})")
        else:
            business_logger.warning(f"knowledge base sharing does not exist: knowledgeshare_id={knowledgeshare_id}")
        return knowledgeshare
    except Exception as e:
        business_logger.error(f"Failed to query the knowledge base sharing: knowledgeshare_id={knowledgeshare_id} - {str(e)}")
        raise


async def get_knowledgeshare_by_id_async(
        db: AsyncSession,
        knowledgeshare_id: uuid.UUID,
        current_user: User
) -> KnowledgeShare | None:
    try:
        workspace_id = current_user.current_workspace_id
        if workspace_id is None:
            return None
        return await knowledgeshare_repository.get_knowledgeshare_by_id_in_source_workspace_async(
            db=db,
            knowledgeshare_id=knowledgeshare_id,
            source_workspace_id=workspace_id,
        )
    except Exception as e:
        business_logger.error(f"Failed to query knowledge share (async): knowledgeshare_id={knowledgeshare_id} - {str(e)}")
        raise


def delete_knowledgeshare_by_id(db: Session, knowledgeshare_id: uuid.UUID, current_user: User) -> None:
    business_logger.info(f"Delete knowledge base sharing: knowledgeshare_id={knowledgeshare_id}, operator: {current_user.username}")

    try:
        # First, query the knowledge base sharing information for logging purposes
        knowledgeshare = knowledgeshare_repository.get_knowledgeshare_by_id(db=db, knowledgeshare_id=knowledgeshare_id)
        if knowledgeshare:
            business_logger.debug(f"Execute knowledge base sharing deletion: (ID: {knowledgeshare_id})")
        else:
            business_logger.warning(f"The knowledge base sharing does not exist: knowledgeshare_id={knowledgeshare_id}")

        knowledgeshare_repository.delete_knowledgeshare_by_id(db=db, knowledgeshare_id=knowledgeshare_id)
        business_logger.info(f"knowledge base sharing deleted successfully: knowledgeshare_id={knowledgeshare_id}, operator: {current_user.username}")
    except Exception as e:
        business_logger.error(f"Failed to delete knowledge base sharing: knowledgeshare_id={knowledgeshare_id} - {str(e)}")
        raise


async def delete_knowledgeshare_by_id_async(db: AsyncSession, knowledgeshare_id: uuid.UUID, current_user: User) -> bool:
    business_logger.info(f"Delete knowledge base sharing (async): knowledgeshare_id={knowledgeshare_id}, operator: {current_user.username}")

    try:
        share = await get_knowledgeshare_by_id_async(db, knowledgeshare_id, current_user)
        if share is None:
            return False

        target_kb_id = share.target_kb_id
        await knowledge_repository.lock_share_source_async(db, share.source_kb_id)
        deleted = await knowledgeshare_repository.delete_knowledgeshare_by_id_in_source_workspace_async(
            db=db,
            knowledgeshare_id=share.id,
            source_workspace_id=share.source_workspace_id,
            commit=False,
        )
        if not deleted:
            await db.rollback()
            return False

        # The target deletion commits both deletes together.
        await knowledge_service.delete_knowledge_by_id_async(
            db, knowledge_id=target_kb_id, current_user=current_user
        )
        business_logger.info(
            f"knowledge base sharing deleted successfully (async): "
            f"knowledgeshare_id={knowledgeshare_id}, operator: {current_user.username}"
        )
        return True
    except Exception as e:
        await db.rollback()
        business_logger.error(f"Failed to delete knowledge base sharing (async): knowledgeshare_id={knowledgeshare_id} - {str(e)}")
        raise


async def create_or_reuse_knowledgeshare_async(
    db: AsyncSession, knowledgeshare: KnowledgeShareRequest, current_user: User
) -> tuple[KnowledgeShare, bool]:
    """Create or reuse a reference while holding the source lock through commit."""
    try:
        source = await knowledge_repository.lock_share_source_async(
            db,
            knowledgeshare.source_kb_id,
        )
        if source is None:
            _reject_share("KB_SHARE_SOURCE_NOT_FOUND")
        if source.workspace_id != current_user.current_workspace_id:
            _reject_share("KB_SHARE_SOURCE_OUTSIDE_WORKSPACE")
        source_workspace = await db.get(Workspace, source.workspace_id)
        target_workspace = await db.get(Workspace, knowledgeshare.target_workspace_id)
        if source_workspace is None:
            _reject_share("KB_SHARE_SOURCE_UNAVAILABLE")
        if target_workspace is None:
            _reject_share("KB_SHARE_TARGET_NOT_FOUND")
        if (
            source_workspace.tenant_id != current_user.tenant_id
            or target_workspace.tenant_id != current_user.tenant_id
        ):
            _reject_share("KB_SHARE_CROSS_TENANT")
        if not source_workspace.is_active or source.status != 1:
            _reject_share("KB_SHARE_SOURCE_UNAVAILABLE")
        if not target_workspace.is_active:
            _reject_share("KB_SHARE_TARGET_UNAVAILABLE")
        if source.workspace_id == target_workspace.id:
            _reject_share("KB_SHARE_SAME_WORKSPACE")
        if source.permission_id != PermissionType.Private or source.type not in (
            KnowledgeType.General,
            KnowledgeType.Web,
            KnowledgeType.ThirdParty,
        ):
            _reject_share("KB_SHARE_SOURCE_NOT_ALLOWED")

        shares = await knowledgeshare_repository.get_shares_for_target_async(
            db,
            source.id,
            target_workspace.id,
        )
        active = []
        for share in shares:
            target = await db.get(Knowledge, share.target_kb_id)
            if (
                share.source_workspace_id == source.workspace_id
                and target is not None
                and target.workspace_id == target_workspace.id
                and target.permission_id == PermissionType.Share
                and target.status == 1
            ):
                active.append(share)
        if len(active) > 1:
            _reject_share("KB_SHARE_DUPLICATE")
        if shares and not active:
            _reject_share("KB_SHARE_REFERENCE_UNAVAILABLE")
        created = not active
        if active:
            share = active[0]
        else:
            target = await knowledge_repository.create_knowledge_async(
                db,
                KnowledgeCreate(
                    workspace_id=target_workspace.id,
                    parent_id=target_workspace.id,
                    created_by=current_user.id,
                    name=source.name,
                    description=source.description,
                    avatar=source.avatar,
                    type=source.type,
                    permission_id=PermissionType.Share,
                    embedding_id=source.embedding_id,
                    reranker_id=source.reranker_id,
                    llm_id=source.llm_id,
                    image2text_id=source.image2text_id,
                    audio2text_id=source.audio2text_id,
                    video2text_id=source.video2text_id,
                    doc_num=source.doc_num,
                    chunk_num=source.chunk_num,
                    parser_id=source.parser_id,
                    parser_config=source.parser_config,
                ),
                preserve_source_parser_config=True,
                commit=False,
            )
            target.builtin_metadata_enabled = source.builtin_metadata_enabled
            share = await knowledgeshare_repository.create_knowledgeshare_async(
                db,
                KnowledgeShareCreate(
                    source_kb_id=source.id,
                    source_workspace_id=source.workspace_id,
                    target_kb_id=target.id,
                    target_workspace_id=target_workspace.id,
                    shared_by=current_user.id,
                ),
                commit=False,
            )
        await db.commit()
        return share, created
    except Exception:
        await db.rollback()
        raise


def _reject_share(code: str) -> NoReturn:
    numeric = BizCode[code]
    raise BusinessException(
        knowledge_message(numeric, get_current_locale() or "zh"),
        code=numeric,
    )
