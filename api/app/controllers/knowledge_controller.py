from typing import Optional
import datetime
import json
import uuid
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user
from app.models.user_model import User
from app.models import knowledge_model, document_model, file_model
from app.schemas import knowledge_schema
from app.schemas.response_schema import ApiResponse
from app.core.response_utils import success
from app.services import knowledge_service, document_service
from app.core.rag.llm.chat_model import Base
from app.core.rag.prompts.generator import graph_entity_types
from app.core.rag.vdb.elasticsearch.elasticsearch_vector import ElasticSearchVectorFactory
from app.core.logging_config import get_api_logger
from app.core.rag.nlp import rag_tokenizer, search
from app.core.rag.common import settings
from app.celery_app import celery_app

# Obtain a dedicated API logger
api_logger = get_api_logger()

router = APIRouter(
    prefix="/knowledges",
    tags=["knowledges"],
    dependencies=[Depends(get_current_user)]  # Apply auth to all routes in this controller
)


@router.get("/knowledgetype", response_model=ApiResponse)
def get_knowledge_types():
    return success(msg="Successfully obtained the knowledge type", data=list(knowledge_model.KnowledgeType))


@router.get("/permissiontype", response_model=ApiResponse)
def get_permission_types():
    return success(msg="Successfully obtained the knowledge permission type", data=list(knowledge_model.PermissionType))


@router.get("/parsertype", response_model=ApiResponse)
def get_parser_types():
    return success(msg="Successfully obtained the knowledge parser type", data=list(knowledge_model.ParserType))


@router.get("/knowledges", response_model=ApiResponse)
async def get_knowledges(
        parent_id: Optional[uuid.UUID] = Query(None, description="parent folder id"),
        page: int = Query(1, gt=0),  # Default: 1, which must be greater than 0
        pagesize: int = Query(20, gt=0, le=100),  # Default: 20 items per page, maximum: 100 items
        orderby: Optional[str] = Query(None, description="Sort fields, such as: created_at,updated_at"),
        desc: Optional[bool] = Query(False, description="Is it descending order"),
        keywords: Optional[str] = Query(None, description="Search keywords (knowledge base name)"),
        kb_ids: Optional[str] = Query(None, description="Knowledge base ids, separated by commas"),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Query the knowledge base list in pages
    - Support filtering by parent_id
    -  Support keyword search for knowledge base names
    - Support dynamic sorting
    - Return paging metadata + file list
    """
    api_logger.info(f"Query knowledge base list: workspace_id={current_user.current_workspace_id}, page={page}, pagesize={pagesize}, keywords={keywords}, kb_ids={kb_ids}, username: {current_user.username}")
    
    # 1. parameter validation
    if page < 1 or pagesize < 1:
        api_logger.warning(f"Error in paging parameters: page={page}, pagesize={pagesize}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The paging parameter must be greater than 0"
        )

    # 2. Construct query conditions
    filters = [
        knowledge_model.Knowledge.workspace_id == current_user.current_workspace_id
    ]

    # Keyword search (fuzzy matching of knowledge base name)
    if keywords:
        api_logger.debug(f"Add keyword search criteria: {keywords}")
        filters.append(
            or_(
                knowledge_model.Knowledge.name.ilike(f"%{keywords}%"),
                knowledge_model.Knowledge.description.ilike(f"%{keywords}%")
            )
        )
    # Knowledge base ids
    if kb_ids:
        filters.append(knowledge_model.Knowledge.id.in_(kb_ids.split(',')))
    else:
        filters.append(knowledge_model.Knowledge.status != 2)
        if parent_id:
            filters.append(knowledge_model.Knowledge.parent_id == parent_id)
        else:
            filters.append(knowledge_model.Knowledge.parent_id == current_user.current_workspace_id)
    filters.append(knowledge_model.Knowledge.permission_id != knowledge_model.PermissionType.Memory)
    # 3. Execute paged query
    try:
        api_logger.debug("Start executing knowledge base paging query")
        total, items = knowledge_service.get_knowledges_paginated(
            db=db,
            filters=filters,
            page=page,
            pagesize=pagesize,
            orderby=orderby,
            desc=desc,
            current_user=current_user
        )
        api_logger.info(f"Knowledge base query successful: total={total}, returned={len(items)} records")
    except Exception as e:
        api_logger.error(f"Knowledge base query failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Query failed: {str(e)}"
        )

    # 4. Return structured response
    result = {
        "items": items,
        "page": {
            "page": page,
            "pagesize": pagesize,
            "total": total,
            "has_next": True if page*pagesize < total else False
        }
    }
    return success(data=result, msg="Query of knowledge base list successful")


@router.post("/knowledge", response_model=ApiResponse)
async def create_knowledge(
        create_data: knowledge_schema.KnowledgeCreate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    create knowledge
    """
    api_logger.info(f"Request to create a knowledge base: name={create_data.name}, workspace_id={current_user.current_workspace_id}, username: {current_user.username}")
    
    try:
        api_logger.debug(f"Start creating the knowledge base: {create_data.name}")
        # 1. Check if the knowledge base name already exists
        db_knowledge_exist = knowledge_service.get_knowledge_by_name(db, name=create_data.name, current_user=current_user)
        if db_knowledge_exist:
            api_logger.warning(f"The knowledge base name already exists: {create_data.name}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"The knowledge base name already exists: {create_data.name}"
            )
        db_knowledge = knowledge_service.create_knowledge(db=db, knowledge=create_data, current_user=current_user)
        api_logger.info(f"The knowledge base has been successfully created: {db_knowledge.name} (ID: {db_knowledge.id})")
        return success(data=knowledge_schema.Knowledge.model_validate(db_knowledge), msg="The knowledge base has been successfully created")
    except Exception as e:
        api_logger.error(f"The creation of the knowledge base failed: {create_data.name} - {str(e)}")
        raise


@router.get("/{knowledge_id}", response_model=ApiResponse)
async def get_knowledge(
        knowledge_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Retrieve knowledge base information based on knowledge_id
    """
    api_logger.info(f"Obtain details of the knowledge base: knowledge_id={knowledge_id}, username: {current_user.username}")
    
    try:
        # 1. Query knowledge base information from the database
        api_logger.debug(f"Query knowledge base: {knowledge_id}")
        db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=knowledge_id, current_user=current_user)
        if not db_knowledge:
            api_logger.warning(f"The knowledge base does not exist or access is denied: knowledge_id={knowledge_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The knowledge base does not exist or access is denied"
            )
        
        api_logger.info(f"Knowledge base query successful: {db_knowledge.name} (ID: {db_knowledge.id})")
        return success(data=knowledge_schema.Knowledge.model_validate(db_knowledge), msg="Successfully obtained knowledge base information")
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"Knowledge base query failed: knowledge_id={knowledge_id} - {str(e)}")
        raise


@router.put("/{knowledge_id}", response_model=ApiResponse)
async def update_knowledge(
        knowledge_id: uuid.UUID,
        update_data: knowledge_schema.KnowledgeUpdate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    api_logger.info(f"Update knowledge base request: knowledge_id={knowledge_id}, username: {current_user.username}")
    db_knowledge = await _update_knowledge(knowledge_id=knowledge_id, update_data=update_data, db=db, current_user=current_user)
    return success(data=knowledge_schema.Knowledge.model_validate(db_knowledge), msg="The knowledge base information has been successfully updated")


async def _update_knowledge(
        knowledge_id: uuid.UUID,
        update_data: knowledge_schema.KnowledgeUpdate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
) -> knowledge_schema.Knowledge:
    """
    Update knowledge base information
    """
    try:
        # 1. Check whether the knowledge base exists
        api_logger.debug(f"Query the knowledge base to be updated: {knowledge_id}")
        db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=knowledge_id, current_user=current_user)

        if not db_knowledge:
            api_logger.warning(f"The knowledge base does not exist or you do not have permission to access it: knowledge_id={knowledge_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The knowledge base does not exist or you do not have permission to access it"
            )

        # 2. If updating the embedding_id, delete the knowledge base vector index, reset all document parsing progress to 0, and set chunk_num to 0
        update_dict = update_data.dict(exclude_unset=True)
        if "name" in update_dict:
            name = update_dict["name"]
            if name != db_knowledge.name:
                # Check if the knowledge base name already exists
                db_knowledge_exist = knowledge_service.get_knowledge_by_name(db, name=name, current_user=current_user)
                if db_knowledge_exist:
                    api_logger.warning(f"The knowledge base name already exists: {name}")
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"The knowledge base name already exists: {name}"
                    )
        if "embedding_id" in update_dict:
            embedding_id = update_dict["embedding_id"]
            if embedding_id != db_knowledge.embedding_id:
                vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)
                vector_service.delete()
                document_service.reset_documents_progress_by_kb_id(db, kb_id=db_knowledge.id, current_user=current_user)

        # 2. Update fields (only update non-null fields)
        api_logger.debug(f"Start updating the knowledge base fields: {knowledge_id}")
        updated_fields = []
        for field, value in update_data.dict(exclude_unset=True).items():
            if hasattr(db_knowledge, field):
                old_value = getattr(db_knowledge, field)
                if old_value != value:
                    # update value
                    setattr(db_knowledge, field, value)
                    updated_fields.append(f"{field}: {old_value} -> {value}")
        
        if updated_fields:
            api_logger.debug(f"updated fields: {', '.join(updated_fields)}")
        
        db_knowledge.updated_at = datetime.datetime.now()

        # 3. Save to database
        db.commit()
        db.refresh(db_knowledge)
        api_logger.info(f"The knowledge base has been successfully updated: {db_knowledge.name} (ID: {db_knowledge.id})")

        # 4. Return the updated knowledge base
        return db_knowledge
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        api_logger.error(f"Knowledge base update failed: knowledge_id={knowledge_id} - {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Knowledge base update failed: {str(e)}"
        )


@router.delete("/{knowledge_id}", response_model=ApiResponse)
async def delete_knowledge(
        knowledge_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Soft-delete knowledge base
    """
    api_logger.info(f"Request to delete knowledge base: knowledge_id={knowledge_id}, username: {current_user.username}")
    
    try:
        # 1. Check whether the knowledge base exists
        api_logger.debug(f"Check whether the knowledge base exists: {knowledge_id}")
        db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=knowledge_id, current_user=current_user)

        if not db_knowledge:
            api_logger.warning(f"The knowledge base does not exist or you do not have permission to access it: knowledge_id={knowledge_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The knowledge base does not exist or you do not have permission to access it"
            )

        # 2. Soft-delete knowledge base
        api_logger.debug(f"Perform a soft delete: {db_knowledge.name} (ID: {knowledge_id})")
        db_knowledge.status = 2
        db.commit()
        api_logger.info(f"The knowledge base has been successfully deleted: {db_knowledge.name} (ID: {knowledge_id})")
        return success(msg="The knowledge base has been successfully deleted")
    except Exception as e:
        api_logger.error(f"Failed to delete from the knowledge base: knowledge_id={knowledge_id} - {str(e)}")
        raise


@router.get("/{knowledge_id}/knowledge_graph", response_model=ApiResponse)
async def get_knowledge_graph(
        knowledge_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Retrieve knowledge_graph base information based on knowledge_id
    """
    api_logger.info(f"Obtain details of the knowledge graph: knowledge_id={knowledge_id}, username: {current_user.username}")

    try:
        # 1. Query knowledge base information from the database
        api_logger.debug(f"Query knowledge base: {knowledge_id}")
        db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=knowledge_id, current_user=current_user)
        if not db_knowledge:
            api_logger.warning(f"The knowledge base does not exist or access is denied: knowledge_id={knowledge_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The knowledge base does not exist or access is denied"
            )

        req = {
            "kb_id": [str(db_knowledge.id)],
            "knowledge_graph_kwd": ["graph"]
        }

        obj = {"graph": {}, "mind_map": {}}
        if not settings.docStoreConn.indexExist(search.index_name(str(db_knowledge.workspace_id)), str(db_knowledge.id)):
            return success(data=obj, msg="Successfully obtained knowledge graph information")
        sres = settings.retriever.search(req, search.index_name(str(db_knowledge.workspace_id)), [str(db_knowledge.id)])
        if not len(sres.ids):
            return success(data=obj, msg="Successfully obtained knowledge graph information")

        for id in sres.ids[:1]:
            ty = sres.field[id]["knowledge_graph_kwd"]
            try:
                content_json = json.loads(sres.field[id]["page_content"])
            except Exception:
                continue

            obj[ty] = content_json

        if "nodes" in obj["graph"]:
            obj["graph"]["nodes"] = sorted(obj["graph"]["nodes"], key=lambda x: x.get("pagerank", 0), reverse=True)[:256]
            if "edges" in obj["graph"]:
                node_id_set = {o["id"] for o in obj["graph"]["nodes"]}
                filtered_edges = [o for o in obj["graph"]["edges"] if o["source"] != o["target"] and o["source"] in node_id_set and o["target"] in node_id_set]
                obj["graph"]["edges"] = sorted(filtered_edges, key=lambda x: x.get("weight", 0), reverse=True)[:128]
        return success(data=obj, msg="Successfully obtained knowledge graph information")
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"Knowledge graph query failed: knowledge_id={knowledge_id} - {str(e)}")
        raise


@router.delete("/{knowledge_id}/knowledge_graph", response_model=ApiResponse)
async def delete_knowledge_graph(
        knowledge_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    Soft-delete knowledge graph
    """
    api_logger.info(f"Request to delete knowledge graph: knowledge_id={knowledge_id}, username: {current_user.username}")

    try:
        # 1. Check whether the knowledge base exists
        api_logger.debug(f"Check whether the knowledge base exists: {knowledge_id}")
        db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=knowledge_id, current_user=current_user)

        if not db_knowledge:
            api_logger.warning(f"The knowledge base does not exist or you do not have permission to access it: knowledge_id={knowledge_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The knowledge base does not exist or you do not have permission to access it"
            )

        # 2. delete knowledge graph
        settings.docStoreConn.delete({"knowledge_graph_kwd": ["graph", "subgraph", "entity", "relation"]}, search.index_name(str(db_knowledge.workspace_id)), str(db_knowledge.id))
        api_logger.info(f"The knowledge graph has been successfully deleted: {db_knowledge.name} (ID: {knowledge_id})")
        return success(msg="The knowledge graph has been successfully deleted")
    except Exception as e:
        api_logger.error(f"Failed to delete from the knowledge base: knowledge_id={knowledge_id} - {str(e)}")
        raise


@router.post("/{knowledge_id}/knowledge_graph", response_model=ApiResponse)
async def rebuild_knowledge_graph(
        knowledge_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    rebuild knowledge graph
    """
    api_logger.info(f"Request to rebuild knowledge graph: knowledge_id={knowledge_id}, username: {current_user.username}")

    try:
        # 1. Check whether the knowledge base exists
        api_logger.debug(f"Check whether the knowledge base exists: {knowledge_id}")
        db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=knowledge_id, current_user=current_user)

        if not db_knowledge:
            api_logger.warning(
                f"The knowledge base does not exist or you do not have permission to access it: knowledge_id={knowledge_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The knowledge base does not exist or you do not have permission to access it"
            )

        # 2. delete knowledge graph
        settings.docStoreConn.delete({"knowledge_graph_kwd": ["graph", "subgraph", "entity", "relation"]}, search.index_name(str(db_knowledge.workspace_id)), str(db_knowledge.id))

        # 3. build knowledge graph
        # from app.tasks import build_graphrag_for_kb
        # build_graphrag_for_kb(kb_id)
        task = celery_app.send_task("app.core.rag.tasks.build_graphrag_for_kb", args=[knowledge_id])
        result = {
            "task_id": task.id
        }
        return success(data=result, msg="Task accepted. rebuild knowledge graph is being processed in the background.")
    except Exception as e:
        api_logger.error(f"Failed to rebuild knowledge graph: knowledge_id={knowledge_id} - {str(e)}")
        raise


@router.get("/{knowledge_id}/knowledge_graph_entity_types", response_model=ApiResponse)
async def get_knowledge_graph_entity_types(
        knowledge_id: uuid.UUID,
        scenario: str,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    get knowledge graph entity types based on knowledge_id
    """
    api_logger.info(f"Obtain details of the knowledge graph: knowledge_id={knowledge_id}, username: {current_user.username}")

    try:
        # 1. Check whether the knowledge base exists
        api_logger.debug(f"Check whether the knowledge base exists: {knowledge_id}")
        db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=knowledge_id, current_user=current_user)

        if not db_knowledge:
            api_logger.warning(
                f"The knowledge base does not exist or you do not have permission to access it: knowledge_id={knowledge_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The knowledge base does not exist or you do not have permission to access it"
            )
        # 2. Prepare to configure chat_mdl information
        chat_model = Base(
            key=db_knowledge.llm.api_keys[0].api_key,
            model_name=db_knowledge.llm.api_keys[0].model_name,
            base_url=db_knowledge.llm.api_keys[0].api_base
        )
        response = graph_entity_types(chat_model, scenario)
        return success(data=response, msg="Successfully obtained knowledge graph entity types")
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"get knowledge graph entity types failed: knowledge_id={knowledge_id} - {str(e)}")
        raise
