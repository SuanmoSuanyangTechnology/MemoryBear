import uuid
from fastapi import APIRouter, Depends, Body
from sqlalchemy.orm import Session
from app.core.response_utils import success
from app.core.logging_config import get_api_logger
from app.core.exceptions import ResourceNotFoundException
from app.db import get_db
from app.dependencies import get_current_user
from app.services.rag_access_service import require_current_workspace_knowledge
from app.models.user_model import User
from app.schemas import knowledge_metadata_schema as schemas
from app.schemas.response_schema import ApiResponse
from app.services.knowledge_metadata_service import KnowledgeMetadataService

api_logger = get_api_logger()

router = APIRouter(
    prefix="/knowledges",
    tags=["knowledge-metadata"],
    dependencies=[Depends(get_current_user)]
)


def _format_builtin_fields(fields) -> list[schemas.KnowledgeMetadataResponse]:
    return [
        schemas.KnowledgeMetadataResponse(
            id=None,
            type=field.type,
            name=field.name,
            is_builtin=True,
        )
        for field in fields
    ]


def _format_metadata_fields_result(result: dict) -> dict:
    custom_responses = [
        schemas.KnowledgeMetadataResponse.model_validate(field)
        for field in result["custom"]
    ]
    return {
        "custom": custom_responses,
        "builtin_enabled": result["builtin_enabled"],
        "builtin_fields": _format_builtin_fields(result["builtin_fields"]),
    }


def _field_value(field, key: str):
    if isinstance(field, dict):
        return field.get(key)
    return getattr(field, key)


def _format_common_metadata_fields_result(result: dict) -> dict:
    return {
        "custom": [
            {
                "type": _field_value(field, "type"),
                "name": _field_value(field, "name"),
                "is_builtin": False,
            }
            for field in result["custom"]
        ],
        "builtin_enabled": result["builtin_enabled"],
        "builtin_fields": [
            {
                "type": _field_value(field, "type"),
                "name": _field_value(field, "name"),
                "is_builtin": True,
            }
            for field in result["builtin_fields"]
        ],
    }


@router.post("/metadata/fields", response_model=ApiResponse)
async def list_common_metadata_fields(
    data: schemas.KnowledgeMetadataFieldsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List common metadata fields across knowledge bases."""
    kb_ids = list(dict.fromkeys(data.kb_ids))
    api_logger.info(f"List common metadata fields: kb_ids={kb_ids}, user={current_user.username}")

    for kb_id in kb_ids:
        db_knowledge = require_current_workspace_knowledge(
            db=db,
            knowledge_id=kb_id,
            current_user=current_user,
        )
        if not db_knowledge:
            raise ResourceNotFoundException("知识库", str(kb_id))

    result = KnowledgeMetadataService.list_metadata_fields_for_knowledge_ids(
        db,
        kb_ids,
        include_counts=False,
    )

    return success(data=_format_common_metadata_fields_result(result))


@router.get("/{kb_id}/metadata", response_model=ApiResponse)
async def list_metadata_fields(
    kb_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取元数据字段列表（自定义 + 内置）"""
    api_logger.info(f"List metadata fields: kb_id={kb_id}, user={current_user.username}")

    # 校验知识库权限
    db_knowledge = require_current_workspace_knowledge(
        db=db,
        knowledge_id=kb_id,
        current_user=current_user,
    )
    if not db_knowledge:
        raise ResourceNotFoundException("知识库", str(kb_id))

    result = KnowledgeMetadataService.list_metadata_fields(db, kb_id)

    return success(data=_format_metadata_fields_result(result))


@router.post("/{kb_id}/metadata", response_model=ApiResponse)
async def create_metadata_field(
    kb_id: uuid.UUID,
    data: schemas.KnowledgeMetadataCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建自定义元数据字段"""
    api_logger.info(f"Create metadata field: kb_id={kb_id}, name={data.name}, type={data.type}")

    db_knowledge = require_current_workspace_knowledge(
        db=db,
        knowledge_id=kb_id,
        current_user=current_user,
    )
    if not db_knowledge:
        raise ResourceNotFoundException("知识库", str(kb_id))

    field = KnowledgeMetadataService.create_metadata_field(
        db=db,
        knowledge_id=kb_id,
        name=data.name,
        field_type=data.type.value,
        tenant_id=current_user.tenant_id,
        created_by=current_user.id,
    )

    return success(
        data=schemas.KnowledgeMetadataResponse.model_validate(field),
        msg="字段创建成功",
    )


@router.put("/{kb_id}/metadata/{metadata_id}", response_model=ApiResponse)
async def update_metadata_field(
    kb_id: uuid.UUID,
    metadata_id: uuid.UUID,
    data: schemas.KnowledgeMetadataUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新自定义元数据字段"""
    api_logger.info(f"Update metadata field: kb_id={kb_id}, metadata_id={metadata_id}")

    db_knowledge = require_current_workspace_knowledge(
        db=db,
        knowledge_id=kb_id,
        current_user=current_user,
    )
    if not db_knowledge:
        raise ResourceNotFoundException("知识库", str(kb_id))

    field = KnowledgeMetadataService.update_metadata_field(
        db=db,
        metadata_id=metadata_id,
        knowledge_id=kb_id,
        name=data.name,
        updated_by=current_user.id,
    )

    return success(
        data=schemas.KnowledgeMetadataResponse.model_validate(field),
        msg="字段更新成功",
    )


@router.delete("/{kb_id}/metadata/{metadata_id}", response_model=ApiResponse)
async def delete_metadata_field(
    kb_id: uuid.UUID,
    metadata_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除自定义元数据字段"""
    api_logger.info(f"Delete metadata field: kb_id={kb_id}, metadata_id={metadata_id}")

    db_knowledge = require_current_workspace_knowledge(
        db=db,
        knowledge_id=kb_id,
        current_user=current_user,
    )
    if not db_knowledge:
        raise ResourceNotFoundException("知识库", str(kb_id))

    KnowledgeMetadataService.delete_metadata_field(db, metadata_id, kb_id)

    return success(msg="字段删除成功")


@router.get("/{kb_id}/metadata/builtin", response_model=ApiResponse)
async def get_builtin_metadata_fields(
    kb_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取内置元数据字段列表"""
    api_logger.info(f"Get builtin metadata fields: kb_id={kb_id}")

    db_knowledge = require_current_workspace_knowledge(
        db=db,
        knowledge_id=kb_id,
        current_user=current_user,
    )
    if not db_knowledge:
        raise ResourceNotFoundException("知识库", str(kb_id))

    result = KnowledgeMetadataService.get_builtin_fields(db, kb_id)

    return success(data=schemas.BuiltinMetadataListResponse(
        enabled=result["enabled"],
        fields=_format_builtin_fields(result["fields"]),
    ))


@router.post("/{kb_id}/metadata/builtin/enable", response_model=ApiResponse)
async def toggle_builtin_metadata(
    kb_id: uuid.UUID,
    data: schemas.BuiltinMetadataEnableRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新内置元数据开关"""
    api_logger.info(f"Toggle builtin metadata: kb_id={kb_id}, enabled={data.enabled}")

    db_knowledge = require_current_workspace_knowledge(
        db=db,
        knowledge_id=kb_id,
        current_user=current_user,
    )
    if not db_knowledge:
        raise ResourceNotFoundException("知识库", str(kb_id))

    enabled = KnowledgeMetadataService.set_builtin_metadata_enabled(db, kb_id, data.enabled)

    return success(data={"enabled": enabled}, msg="内置元数据开关更新成功")
