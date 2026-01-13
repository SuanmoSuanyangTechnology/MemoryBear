# -*- coding: utf-8 -*-
"""Memory Configuration Validators

This module provides validation functions for memory configuration models.

Functions:
    validate_model_exists_and_active: Validate model exists and is active
    validate_and_resolve_model_id: Validate and resolve model ID with DB lookup
    validate_embedding_model: Validate embedding model availability
    validate_llm_model: Validate LLM model availability
"""

import time
import uuid
from typing import Optional, Union
from uuid import UUID

from langchain_core.prompts import ChatPromptTemplate

from app.core.logging_config import get_config_logger
from app.core.models import RedBearEmbeddings, RedBearModelConfig, RedBearLLM
from app.repositories.model_repository import ModelConfigRepository
from app.schemas import model_schema
from app.schemas.memory_config_schema import (
    InvalidConfigError,
    ModelInactiveError,
    ModelNotFoundError, COnfigType,
)
from app.models.models_model import ModelApiKey
from sqlalchemy.orm import Session

from fastapi import Depends, Query

from app.schemas.response_schema import PageData
from app.services.model_service import ModelConfigService

logger = get_config_logger()


def _parse_model_id(model_id: Union[str, UUID, None], model_type: str,
                    config_id: Optional[int] = None, workspace_id: Optional[UUID] = None) -> Optional[UUID]:
    """Parse model ID from string or UUID."""
    if model_id is None:
        return None
    if isinstance(model_id, UUID):
        return model_id
    if isinstance(model_id, str):
        if not model_id.strip():
            return None
        try:
            return UUID(model_id.strip())
        except ValueError:
            raise InvalidConfigError(
                f"Invalid UUID format for {model_type} model ID: '{model_id}'",
                field_name=f"{model_type}_model_id",
                invalid_value=model_id,
                config_id=config_id,
                workspace_id=workspace_id
            )
    raise InvalidConfigError(
        f"Invalid type for {model_type} model ID: expected str or UUID, got {type(model_id).__name__}",
        field_name=f"{model_type}_model_id",
        invalid_value=model_id,
        config_id=config_id,
        workspace_id=workspace_id
    )


def validate_model_exists_and_active(
    model_id: UUID,
    model_type: str,
    db: Session,
    tenant_id: Optional[UUID] = None,
    config_id: Optional[int] = None,
    workspace_id: Optional[UUID] = None
) -> tuple[str, bool]:
    """Validate that a model exists and is active.
    
    This function performs tenant-aware model validation with detailed error messages:
    - If model doesn't exist at all: "Model not found"
    - If model exists but belongs to different tenant: "Model belongs to different tenant" with details
    - If model exists and accessible but inactive: "Model is inactive"
    
    Args:
        model_id: Model UUID to validate
        model_type: Type of model ("llm", "embedding", "rerank")
        db: Database session
        tenant_id: Optional tenant ID for filtering
        config_id: Optional configuration ID for error context
        workspace_id: Optional workspace ID for error context
        
    Returns:
        Tuple of (model_name, is_active)
        
    Raises:
        ModelNotFoundError: If model does not exist or belongs to different tenant
        ModelInactiveError: If model exists but is inactive
    """
    
    start_time = time.time()
    
    try:
        # First check if model exists at all (without tenant filtering)
        model_without_tenant = ModelConfigRepository.get_by_id(db, model_id, tenant_id=None)
        
        # Then check with tenant filtering
        model = ModelConfigRepository.get_by_id(db, model_id, tenant_id)
        elapsed_ms = (time.time() - start_time) * 1000
        
        if not model:
            if model_without_tenant:
                # Model exists but belongs to different tenant
                logger.warning(
                    "Model belongs to different tenant",
                    extra={
                        "model_id": str(model_id), 
                        "model_type": model_type, 
                        "model_name": model_without_tenant.name,
                        "model_tenant_id": str(model_without_tenant.tenant_id),
                        "requested_tenant_id": str(tenant_id),
                        "is_public": model_without_tenant.is_public,
                        "elapsed_ms": elapsed_ms
                    }
                )
                raise ModelNotFoundError(
                    model_id=model_id,
                    model_type=model_type,
                    config_id=config_id,
                    workspace_id=workspace_id,
                    message=f"{model_type.title()} model {model_id} ({model_without_tenant.name}) belongs to a different tenant (model tenant: {model_without_tenant.tenant_id}, workspace tenant: {tenant_id}). The model is not public and cannot be accessed from this workspace."
                )
            else:
                # Model doesn't exist at all
                logger.warning(
                    "Model not found",
                    extra={"model_id": str(model_id), "model_type": model_type, "elapsed_ms": elapsed_ms}
                )
                raise ModelNotFoundError(
                    model_id=model_id,
                    model_type=model_type,
                    config_id=config_id,
                    workspace_id=workspace_id,
                    message=f"{model_type.title()} model {model_id} not found"
                )
        
        if not model.is_active:
            logger.warning(
                "Model inactive",
                extra={"model_id": str(model_id), "model_name": model.name, "elapsed_ms": elapsed_ms}
            )
            raise ModelInactiveError(
                model_id=model_id,
                model_name=model.name,
                model_type=model_type,
                config_id=config_id,
                workspace_id=workspace_id,
                message=f"{model_type.title()} model {model_id} ({model.name}) is inactive"
            )
        
        logger.debug(
            "Model validation successful",
            extra={"model_id": str(model_id), "model_name": model.name, "elapsed_ms": elapsed_ms}
        )
        return model.name, model.is_active
        
    except (ModelNotFoundError, ModelInactiveError):
        raise
    except Exception as e:
        logger.error(f"Model validation failed: {e}", exc_info=True)
        raise


def validate_and_resolve_model_id(
    model_id_str: Union[str, UUID, None],
    model_type: str,
    db: Session,
    tenant_id: Optional[UUID] = None,
    required: bool = False,
    config_id: Optional[int] = None,
    workspace_id: Optional[UUID] = None
) -> tuple[Optional[UUID], Optional[str]]:
    """Validate and resolve a model ID, checking existence and active status.
    
    Returns:
        Tuple of (validated_uuid, model_name) or (None, None) if not required and empty
    """
    if model_id_str is None or (isinstance(model_id_str, str) and not model_id_str.strip()):
        if required:
            raise InvalidConfigError(
                f"{model_type.title()} model ID is required",
                field_name=f"{model_type}_model_id",
                invalid_value=model_id_str,
                config_id=config_id,
                workspace_id=workspace_id
            )
        return None, None
    
    model_uuid = _parse_model_id(model_id_str, model_type, config_id, workspace_id)
    if model_uuid is None:
        if required:
            raise InvalidConfigError(
                f"{model_type.title()} model ID is required",
                field_name=f"{model_type}_model_id",
                invalid_value=model_id_str,
                config_id=config_id,
                workspace_id=workspace_id
            )
        return None, None
    
    model_name, _ = validate_model_exists_and_active(
        model_uuid, model_type, db, tenant_id, config_id, workspace_id
    )
    return model_uuid, model_name


def validate_embedding_model(
    config_id: int,
    embedding_id: Union[str, UUID, None],
    db: Session,
    tenant_id: Optional[UUID] = None,
    workspace_id: Optional[UUID] = None
) -> UUID:
    """Validate that embedding model is available and return its UUID.
    
    Raises:
        InvalidConfigError: If embedding_id is not provided or invalid
        ModelNotFoundError: If embedding model does not exist
        ModelInactiveError: If embedding model is inactive
    """
    if embedding_id is None or (isinstance(embedding_id, str) and not embedding_id.strip()):
        raise InvalidConfigError(
            f"Configuration {config_id} has no embedding model configured",
            field_name="embedding_model_id",
            invalid_value=embedding_id,
            config_id=config_id,
            workspace_id=workspace_id
        )

    model_without_tenant = ModelConfigRepository.get_by_id(db, embedding_id)
    model_without_tenant_name=model_without_tenant.name
    embd = emb_model_config(embedding_id, db)
    if embd != COnfigType.SUCCESS :
        models=models_list(db=db,type=model_schema.ModelType.EMBEDDING,tenant_id=tenant_id)
        models_id=models[0]
        models_name=models[1]
        for embedding_id,embedding_name in zip(models_id,models_name):
            embd = emb_model_config(embedding_id, db)
            if COnfigType.SUCCESS==embd and model_without_tenant_name==embedding_name:
                embedding_id=embedding_id
                update_data_config_model_field(db, config_id, "embedding_id", embedding_id)
                logger.info("已替换失效的embedding_id配置")
                break

    embedding_uuid, _ = validate_and_resolve_model_id(
        embedding_id, "embedding", db, tenant_id, required=True,
        config_id=config_id, workspace_id=workspace_id
    )
    if embedding_uuid is None:
        raise InvalidConfigError(
            f"Configuration {config_id} has no embedding model configured",
            field_name="embedding_model_id",
            invalid_value=embedding_id,
            config_id=config_id,
            workspace_id=workspace_id
        )
    return embedding_uuid


def validate_llm_model(
    config_id: int,
    llm_id: Union[str, UUID, None],
    db: Session,
    tenant_id: Optional[UUID] = None,
    workspace_id: Optional[UUID] = None
) -> UUID:
    """Validate that LLM model is available and return its UUID.

    Raises:
        InvalidConfigError: If llm_id is not provided or invalid
        ModelNotFoundError: If LLM model does not exist
        ModelInactiveError: If LLM model is inactive
    """
    if llm_id is None or (isinstance(llm_id, str) and not llm_id.strip()):
        raise InvalidConfigError(
            f"Configuration {config_id} has no LLM model configured",
            field_name="llm_model_id",
            invalid_value=llm_id,
            config_id=config_id,
            workspace_id=workspace_id
        )

    # llm = llm_model_config(llm_id, db)
    # if llm != "测试成功":
    #     models=models_list(db=db,type=model_schema.ModelType.LLM,tenant_id=tenant_id)
    #     models_id=models[0]
    #     models_name=models[1]
    #     for llm_id,llm_name in zip(models_id,models_name):
    #         llm = emb_model_config(llm_id, db)
    #         if "测试成功"==llm:
    #             llm_id=llm_id
    #             update_data_config_model_field(db, config_id, "llm_id", llm_id)
    #             update_data_config_model_field(db, config_id, "llm", llm_id)
    #             logger.info("已替换失效的embedding_id配置")
    #             break

    llm_uuid, _ = validate_and_resolve_model_id(
        llm_id, "llm", db, tenant_id, required=True,
        config_id=config_id, workspace_id=workspace_id
    )

    if llm_uuid is None:
        raise InvalidConfigError(
            f"Configuration {config_id} has no LLM model configured",
            field_name="llm_model_id",
            invalid_value=llm_id,
            config_id=config_id,
            workspace_id=workspace_id
        )

    return llm_uuid

def models_list(type: str, db: Session, tenant_id: Optional[UUID] = None):
    """获取模型列表，返回model_config_id和model_name"""
    try:
        from app.services.model_service import ModelConfigService
        
        # 将字符串转换为对应的枚举值
        type_mapping = {
            "embedding": model_schema.ModelType.EMBEDDING,
            "llm": model_schema.ModelType.LLM,
            "chat": model_schema.ModelType.CHAT,
            "rerank": model_schema.ModelType.RERANK
        }
        
        model_type_enum = type_mapping.get(type.lower())
        if not model_type_enum:
            return [], []
        
        query = model_schema.ModelConfigQuery(
            type=[model_type_enum],  # 使用正确的枚举值
            provider=None,
            is_active=True,  # 只获取激活的模型
            is_public=None,
            search=None,
            page=1,
            pagesize=100
        )
        
        get_model = ModelConfigService.get_model_list(db=db, query=query, tenant_id=tenant_id)

        model_config_ids = []
        model_names = []
        

        for model_config in get_model.items:
            # 模型配置ID (ModelConfig的ID)
            config_id = model_config.id
            config_name = model_config.name

            # 从每个API Key中提取信息
            if model_config.api_keys:
                for api_key in model_config.api_keys:
                    model_config_ids.append(config_id)  # 使用ModelConfig的ID
                    model_names.append(api_key.model_name)  # 使用API Key的model_name
            else:
                # 如果没有API Key，也添加配置信息
                model_config_ids.append(config_id)
                model_names.append(config_name)
        return model_config_ids, model_names
        
    except Exception as e:
        print(f"获取模型列表失败: {e}")
        return [], []
def emb_model_config(model_id: uuid.UUID, db: Session ):
    try:
        config = ModelConfigService.get_model_by_id(db=db, model_id=model_id)
    except Exception as e:
        return COnfigType.MODEL_FAIL
    if not config:
        return COnfigType.MODEL_FAIL
    try:
        apiConfig: ModelApiKey = config.api_keys[0]
        model = RedBearEmbeddings(RedBearModelConfig(
            model_name=apiConfig.model_name,
            provider=apiConfig.provider,
            api_key=apiConfig.api_key,
            base_url=apiConfig.api_base
        ))
        query = "我想找一个适合学习的地方。"
        query_embedding = model.embed_query(query)
        return COnfigType.SUCCESS
    except Exception as e:
        return COnfigType.FAIL
def llm_model_config(model_id: uuid.UUID, db: Session ):
    try:
        config = ModelConfigService.get_model_by_id(db=db, model_id=model_id)
    except Exception as e:
        return COnfigType.MODEL_FAIL
    if not config:
        return COnfigType.MODEL_FAIL

    try:
        apiConfig: ModelApiKey = config.api_keys[0]
        llm = RedBearLLM(RedBearModelConfig(
            model_name=apiConfig.model_name,
            provider=apiConfig.provider,
            api_key=apiConfig.api_key,
            base_url=apiConfig.api_base
        ), type=config.type)
        template = """Question: {question}

    Answer: Let's think step by step."""
        prompt = ChatPromptTemplate.from_template(template)
        chain = prompt | llm
        answer = chain.invoke({"question": "What is LangChain?"})
        print("Answer:", answer)
        return COnfigType.SUCCESS

    except Exception as e:
        return COnfigType.FAIL

def update_data_config_model_field(db: Session, config_id: int, field_name: str, model_id: UUID) -> bool:
    """
    更新data_config表中的模型字段
    
    Args:
        db: 数据库会话
        config_id: data_config表的config_id
        field_name: 要更新的字段名 ('embedding_id', 'llm_id', 'rerank_id')
        model_id: 新的模型ID
        
    Returns:
        bool: 更新成功返回True，失败返回False
    """
    try:
        from app.repositories.data_config_repository import DataConfigRepository
        from app.services.model_service import ModelConfigService
        
        # 验证字段名
        valid_fields = ['embedding_id', 'llm_id', 'rerank_id']
        if field_name not in valid_fields:
            logger.info(f"无效的字段名: {field_name}，支持的字段: {', '.join(valid_fields)}")
        # 验证模型是否存在
        try:
            model = ModelConfigService.get_model_by_id(db=db, model_id=model_id)
        except Exception as e:
            logger.info(f"指定的模型不存在: {model_id}")
            return False
        
        # 验证模型类型是否匹配字段
        field_type_mapping = {
            'embedding_id': ['embedding'],
            'llm_id': ['llm', 'chat'],
            'rerank_id': ['rerank']
        }
        
        expected_types = field_type_mapping.get(field_name, [])
        if model.type.lower() not in expected_types:
            logger.info(f"模型类型不匹配: 字段 {field_name} 需要 {'/'.join(expected_types)} 类型的模型，但提供的是 {model.type} 类型")
            return False
        
        # 获取data_config记录
        config_record = DataConfigRepository.get_by_id(db, config_id)
        if not config_record:
            logger.info(f"配置记录不存在: config_id={config_id}")
            return False
        # 更新字段
        setattr(config_record, field_name, str(model_id))
        db.commit()
        return True
        
    except Exception as e:
        db.rollback()
        logger.info(f"数据库更新失败: {str(e)}")
        return False