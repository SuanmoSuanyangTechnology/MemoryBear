import uuid
from typing import Any

from sqlalchemy.orm import Session
from app.models.user_model import User
from app.models.knowledge_model import Knowledge, KnowledgeType
from app.models.workspace_model import Workspace
from app.models.models_model import ModelConfig
from app.schemas import knowledge_schema
from app.schemas.knowledge_schema import KnowledgeCreate, KnowledgeUpdate
from app.repositories import knowledge_repository
from app.core.logging_config import get_business_logger
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode
from app.models.models_model import ModelType

business_logger = get_business_logger()


def _build_knowledge_items_with_folder_trees(
        db: Session,
        items: list,
        workspace_id: uuid.UUID,
) -> list[dict[str, Any]]:
    knowledge_items = [
        knowledge_schema.Knowledge.model_validate(item)
        for item in items
    ]
    folder_ids = [
        item.id for item in knowledge_items
        if item.type == KnowledgeType.FOLDER
    ]
    if not folder_ids:
        return [item.model_dump(mode="json") for item in knowledge_items]

    children_by_parent: dict[uuid.UUID, list[knowledge_schema.Knowledge]] = {}
    pending_parent_ids = list(folder_ids)
    visited_parent_ids: set[uuid.UUID] = set()
    all_folder_ids = list(folder_ids)

    while pending_parent_ids:
        current_parent_ids = [
            parent_id for parent_id in pending_parent_ids
            if parent_id not in visited_parent_ids
        ]
        if not current_parent_ids:
            break

        visited_parent_ids.update(current_parent_ids)
        children = knowledge_repository.get_knowledges_by_parent_ids(
            db=db,
            parent_ids=current_parent_ids,
            workspace_id=workspace_id,
        )
        pending_parent_ids = []

        for child in children:
            children_by_parent.setdefault(child.parent_id, []).append(child)
            if child.type == KnowledgeType.FOLDER:
                all_folder_ids.append(child.id)
                pending_parent_ids.append(child.id)

    all_folder_ids = list(dict.fromkeys(all_folder_ids))
    knowledge_ids_by_folder: dict[uuid.UUID, set[uuid.UUID]] = {}

    def collect_knowledge_ids(folder_id: uuid.UUID, ancestors: set[uuid.UUID]) -> set[uuid.UUID]:
        if folder_id in knowledge_ids_by_folder:
            return knowledge_ids_by_folder[folder_id]
        if folder_id in ancestors:
            return set()

        knowledge_ids = {folder_id}
        next_ancestors = ancestors | {folder_id}
        for child in children_by_parent.get(folder_id, []):
            if child.id in next_ancestors:
                continue
            knowledge_ids.add(child.id)
            if child.type == KnowledgeType.FOLDER:
                knowledge_ids.update(collect_knowledge_ids(child.id, next_ancestors))

        knowledge_ids_by_folder[folder_id] = knowledge_ids
        return knowledge_ids

    for folder_id in all_folder_ids:
        collect_knowledge_ids(folder_id, set())

    counted_knowledge_ids = list({
        knowledge_id
        for knowledge_ids in knowledge_ids_by_folder.values()
        for knowledge_id in knowledge_ids
    })
    document_counts = knowledge_repository.get_document_counts_by_knowledge_ids(
        db=db,
        knowledge_ids=counted_knowledge_ids,
    )
    doc_counts = {
        folder_id: sum(document_counts.get(knowledge_id, 0) for knowledge_id in knowledge_ids)
        for folder_id, knowledge_ids in knowledge_ids_by_folder.items()
    }

    def build_item(item: knowledge_schema.Knowledge, ancestors: set[uuid.UUID]) -> dict[str, Any]:
        data = item.model_dump(mode="json")
        if item.id in doc_counts:
            data["doc_num"] = doc_counts[item.id]
        if item.type == KnowledgeType.FOLDER:
            next_ancestors = ancestors | {item.id}
            data["children"] = [
                build_item(child, next_ancestors)
                for child in children_by_parent.get(item.id, [])
                if child.id not in next_ancestors
            ]
        return data

    return [
        build_item(item, set())
        for item in knowledge_items
    ]


def get_knowledges_paginated(
        db: Session,
        current_user: User,
        filters: list,
        page: int,
        pagesize: int,
        orderby: str = None,
        desc: bool = False
) -> tuple[int, list]:
    business_logger.debug(f"Query knowledge base in pages: username={current_user.username}, page={page}, pagesize={pagesize}, orderby={orderby}, desc={desc}")
    
    try:
        total, items = knowledge_repository.get_knowledges_paginated(
                db=db,
                filters=filters,
                page=page,
                pagesize=pagesize,
                orderby=orderby,
                desc=desc
            )
        items = _build_knowledge_items_with_folder_trees(
            db=db,
            items=items,
            workspace_id=current_user.current_workspace_id,
        )
        business_logger.info(f"The knowledge base paging query has been successful: username={current_user.username}, total={total}, Number of current page={len(items)}")
        return total, items
    except Exception as e:
        business_logger.error(f"Querying knowledge base pagination failed: username={current_user.username} - {str(e)}")
        raise


def get_chunked_knowledgeids(
        db: Session,
        current_user: User,
        filters: list
) -> list:
    business_logger.debug(f"Query the list of vectorized knowledge base IDs: username={current_user.username}")

    try:
        items = knowledge_repository.get_chunked_knowledgeids(
            db=db,
            filters=filters
        )
        business_logger.info(f"Querying the vectorized knowledge base id list succeeded: username={current_user.username} count={len(items)}")
        return items
    except Exception as e:
        business_logger.error(f"Querying the vectorized knowledge base id list failed: username={current_user.username} - {str(e)}")
        raise


def create_knowledge(
        db: Session, knowledge: KnowledgeCreate, current_user: User
) -> Knowledge:
    business_logger.info(f"Create a knowledge base: {knowledge.name}, creator: {current_user.username}")

    try:
        knowledge.created_by = current_user.id
        if knowledge.workspace_id is None:
            knowledge.workspace_id = current_user.current_workspace_id
        if knowledge.parent_id is None:
            knowledge.parent_id = knowledge.workspace_id

        if knowledge.external_id:
            if not (1 <= len(knowledge.external_id) <= 36):
                raise BusinessException(
                    "external_id must be between 1 and 36 characters",
                    code=BizCode.VALIDATION_FAILED,
                )
            existing = knowledge_repository.get_knowledge_by_external_id(
                db, knowledge.external_id, knowledge.workspace_id
            )
            if existing:
                raise BusinessException(
                    f"external_id already exists in this workspace: {knowledge.external_id}",
                    code=BizCode.VALIDATION_FAILED,
                )

        workspace = db.query(Workspace).filter(Workspace.id == knowledge.workspace_id).first()
        if not workspace:
            raise Exception(f"Workspace {knowledge.workspace_id} not found")

        tenant_id = workspace.tenant_id

        if not knowledge.embedding_id:
            if not workspace.embedding:
                raise Exception("工作空间未配置 Embedding 模型，请先完善工作空间配置后重试")
            knowledge.embedding_id = workspace.embedding

        if not knowledge.reranker_id:
            if not workspace.rerank:
                raise Exception("工作空间未配置 Rerank 模型，请先完善工作空间配置后重试")
            knowledge.reranker_id = workspace.rerank

        if not knowledge.llm_id:
            if not workspace.llm:
                raise Exception("工作空间未配置 LLM 模型，请先完善工作空间配置后重试")
            knowledge.llm_id = workspace.llm

        if not knowledge.image2text_id:
            model = db.query(ModelConfig).filter(
                ModelConfig.tenant_id == tenant_id,
                ModelConfig.type.in_([ModelType.CHAT.value, ModelType.LLM.value]),
                ModelConfig.capability.contains(["vision"]),
                ModelConfig.is_active == True,
            ).order_by(ModelConfig.created_at.desc()).first()
            if not model:
                raise Exception("租户下没有可用的视觉模型，创建知识库失败")
            knowledge.image2text_id = model.id
            business_logger.debug(f"Auto-bind image2text model: {model.id}")

        business_logger.debug(f"Start creating the knowledge base: {knowledge.name}")
        db_knowledge = knowledge_repository.create_knowledge(
            db=db, knowledge=knowledge
        )
        business_logger.info(f"The knowledge base has been successfully created: {knowledge.name} (ID: {db_knowledge.id}), creator: {current_user.username}")
        return db_knowledge
    except Exception as e:
        business_logger.error(f"Failed to create a knowledge base: {knowledge.name} - {str(e)}")
        raise


def get_knowledge_by_id(db: Session, knowledge_id: uuid.UUID, current_user: User) -> Knowledge | None:
    business_logger.debug(f"Query knowledge base based on ID: knowledge_id={knowledge_id}, username: {current_user.username}")
    
    try:
        knowledge = knowledge_repository.get_knowledge_by_id(db=db, knowledge_id=knowledge_id)
        if knowledge:
            business_logger.info(f"knowledge base query successful: {knowledge.name} (ID: {knowledge_id})")
        else:
            business_logger.warning(f"knowledge base does not exist: knowledge_id={knowledge_id}")
        return knowledge
    except Exception as e:
        business_logger.error(f"Failed to query the knowledge base based on the ID: knowledge_id={knowledge_id} - {str(e)}")
        raise


def get_knowledge_by_external_id(db: Session, external_id: str, workspace_id: uuid.UUID, current_user: User) -> Knowledge | None:
    business_logger.debug(f"Query knowledge base based on external_id: external_id={external_id}, username: {current_user.username}")

    try:
        knowledge = knowledge_repository.get_knowledge_by_external_id(db=db, external_id=external_id, workspace_id=workspace_id)
        if knowledge:
            business_logger.info(f"knowledge base query successful: {knowledge.name} (external_id: {external_id})")
        else:
            business_logger.warning(f"knowledge base does not exist: external_id={external_id}")
        return knowledge
    except Exception as e:
        business_logger.error(f"Failed to query the knowledge base based on external_id: external_id={external_id} - {str(e)}")
        raise


def get_knowledge_ids_by_external_ids(db: Session, external_ids: list[str], workspace_id: uuid.UUID, current_user: User) -> list[uuid.UUID]:
    business_logger.debug(f"Resolve external_ids to knowledge UUIDs: external_ids={external_ids}, username: {current_user.username}")

    try:
        ids = knowledge_repository.get_knowledge_ids_by_external_ids(db=db, external_ids=external_ids, workspace_id=workspace_id)
        business_logger.info(f"Resolved external_ids: {len(external_ids)} -> {len(ids)} UUIDs")
        return ids
    except Exception as e:
        business_logger.error(f"Failed to resolve external_ids: external_ids={external_ids} - {str(e)}")
        raise


def get_knowledge_by_name(db: Session, name: str, current_user: User) -> Knowledge | None:
    business_logger.debug(f"Query knowledge base based on name: name={name}, username: {current_user.username}")

    try:
        knowledge = knowledge_repository.get_knowledge_by_name(db=db, name=name, workspace_id=current_user.current_workspace_id)
        if knowledge:
            business_logger.info(f"knowledge base query successful: {name} (ID: {knowledge.id})")
        else:
            business_logger.warning(f"knowledge base does not exist: name={name}")
        return knowledge
    except Exception as e:
        business_logger.error(f"Failed to query the knowledge base based on the name: name={name} - {str(e)}")
        raise


def delete_knowledge_by_id(db: Session, knowledge_id: uuid.UUID, current_user: User) -> None:
    business_logger.info(f"Delete knowledge base: knowledge_id={knowledge_id}, operator: {current_user.username}")
    
    try:
        # First, query the knowledge base information for logging purposes
        knowledge = knowledge_repository.get_knowledge_by_id(db=db, knowledge_id=knowledge_id)
        if knowledge:
            business_logger.debug(f"Execute knowledge base deletion: {knowledge.name} (ID: {knowledge_id})")
        else:
            business_logger.warning(f"The knowledge base to be deleted does not exist: knowledge_id={knowledge_id}")
        
        knowledge_repository.delete_knowledge_by_id(db=db, knowledge_id=knowledge_id)
        business_logger.info(f"knowledge base record deleted successfully: knowledge_id={knowledge_id}, operator: {current_user.username}")
    except Exception as e:
        business_logger.error(f"Failed to delete knowledge base: knowledge_id={knowledge_id} - {str(e)}")
        raise
