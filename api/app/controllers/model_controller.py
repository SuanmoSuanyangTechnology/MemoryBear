from fastapi import APIRouter, Depends, status, Query
from sqlalchemy.orm import Session
from typing import Optional
import uuid


from app.db import get_db
from app.dependencies import get_current_user
from app.models.models_model import ModelProvider, ModelType
from app.models.user_model import User
from app.schemas import model_schema
from app.core.response_utils import success
from app.schemas.response_schema import ApiResponse, PageData
from app.services.model_service import ModelConfigService, ModelApiKeyService
from app.core.logging_config import get_api_logger

# 获取API专用日志器
api_logger = get_api_logger()

router = APIRouter(
    prefix="/models",
    tags=["Models"],
)

@router.get("/type", response_model=ApiResponse)
def get_model_types():
    
    return success(msg="获取模型类型成功", data=list(ModelType))


@router.get("/provider", response_model=ApiResponse)
def get_model_providers():
    return success(msg="获取模型提供商成功", data=list(ModelProvider))


@router.get("", response_model=ApiResponse)
def get_model_list(
    type: Optional[str] = Query(None, description="模型类型筛选（支持多个，如 ?type=LLM 或 ?type=LLM,EMBEDDING）"),
    provider: Optional[model_schema.ModelProvider] = Query(None, description="提供商筛选(基于API Key)"),
    is_active: Optional[bool] = Query(None, description="激活状态筛选"),
    is_public: Optional[bool] = Query(None, description="公开状态筛选"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    page: int = Query(1, ge=1, description="页码"),
    pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取模型配置列表
    
    支持多个 type 参数：
    - 单个：?type=LLM
    - 多个（逗号分隔）：?type=LLM,EMBEDDING
    - 多个（重复参数）：?type=LLM&type=EMBEDDING
    """
    api_logger.info(f"获取模型配置列表请求: type={type}, provider={provider}, page={page}, pagesize={pagesize}, tenant_id={current_user.tenant_id}")
    
    try:
        # 解析 type 参数（支持逗号分隔）
        type_list = None
        if type:
            type_values = [t.strip() for t in type.split(',')]
            type_list = [model_schema.ModelType(t.lower()) for t in type_values if t]
        
        api_logger.error(f"获取模型type_list: {type_list}")
        query = model_schema.ModelConfigQuery(
            type=type_list,
            provider=provider,
            is_active=is_active,
            is_public=is_public,
            search=search,
            page=page,
            pagesize=pagesize
        )
        
        api_logger.debug(f"开始获取模型配置列表: {query.dict()}")
        result_orm = ModelConfigService.get_model_list(db=db, query=query, tenant_id=current_user.tenant_id)
        result = PageData.model_validate(result_orm)
        api_logger.info(f"模型配置列表获取成功: 总数={result.page.total}, 当前页={len(result.items)}")
        return success(data=result, msg="模型配置列表获取成功")
    except Exception as e:
        api_logger.error(f"获取模型配置列表失败: {str(e)}")
        raise


@router.get("/{model_id}", response_model=ApiResponse)
def get_model_by_id(
    model_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    根据ID获取模型配置
    """
    api_logger.info(f"获取模型配置请求: model_id={model_id}, tenant_id={current_user.tenant_id}")
    
    try:
        api_logger.debug(f"开始获取模型配置: model_id={model_id}")
        result_orm = ModelConfigService.get_model_by_id(db=db, model_id=model_id, tenant_id=current_user.tenant_id)
        api_logger.info(f"模型配置获取成功: {result_orm.name}")
        
        # 将ORM对象转换为Pydantic模型
        result_pydantic = model_schema.ModelConfig.model_validate(result_orm)
        
        return success(data=result_pydantic, msg="模型配置获取成功")
    except Exception as e:
        api_logger.error(f"获取模型配置失败: model_id={model_id} - {str(e)}")
        raise


@router.post("", response_model=ApiResponse)
async def create_model(
    model_data: model_schema.ModelConfigCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    创建模型配置
    
    - 创建模型配置基础信息
    - 如果包含 API Key，会先验证配置有效性，然后创建
    - 验证失败时会抛出异常，不会创建配置
    - 可通过 skip_validation=true 跳过验证
    """
    api_logger.info(f"创建模型配置请求: {model_data.name}, 用户: {current_user.username}, tenant_id={current_user.tenant_id}")
    
    try:
        api_logger.debug(f"开始创建模型配置: {model_data.name}")
        result_orm = await ModelConfigService.create_model(db=db, model_data=model_data, tenant_id=current_user.tenant_id)
        api_logger.info(f"模型配置创建成功: {result_orm.name} (ID: {result_orm.id})")
        
        # 将ORM对象转换为Pydantic模型
        result = model_schema.ModelConfig.model_validate(result_orm)
        
        return success(data=result, msg="模型配置创建成功")
    except Exception as e:
        api_logger.error(f"创建模型配置失败: {model_data.name} - {str(e)}")
        raise


@router.put("/{model_id}", response_model=ApiResponse)
def update_model(
    model_id: uuid.UUID,
    model_data: model_schema.ModelConfigUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    更新模型配置
    """
    api_logger.info(f"更新模型配置请求: model_id={model_id}, 用户: {current_user.username}, tenant_id={current_user.tenant_id}")
    
    try:
        api_logger.debug(f"开始更新模型配置: model_id={model_id}")
        result_orm = ModelConfigService.update_model(db=db, model_id=model_id, model_data=model_data, tenant_id=current_user.tenant_id)
        api_logger.info(f"模型配置更新成功: {result_orm.name} (ID: {model_id})")
        
        # 将ORM对象转换为Pydantic模型
        result_pydantic = model_schema.ModelConfig.model_validate(result_orm)
        
        return success(data=result_pydantic, msg="模型配置更新成功")
    except Exception as e:
        api_logger.error(f"更新模型配置失败: model_id={model_id} - {str(e)}")
        raise


@router.delete("/{model_id}", response_model=ApiResponse)
def delete_model(
    model_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    删除模型配置
    """
    api_logger.info(f"删除模型配置请求: model_id={model_id}, 用户: {current_user.username}, tenant_id={current_user.tenant_id}")
    
    try:
        api_logger.debug(f"开始删除模型配置: model_id={model_id}")
        ModelConfigService.delete_model(db=db, model_id=model_id, tenant_id=current_user.tenant_id)
        api_logger.info(f"模型配置删除成功: model_id={model_id}")
        return success(msg="模型配置删除成功")
    except Exception as e:
        api_logger.error(f"删除模型配置失败: model_id={model_id} - {str(e)}")
        raise


# API Key 相关接口
@router.get("/{model_id}/apikeys", response_model=ApiResponse)
def get_model_api_keys(
    model_id: uuid.UUID,
    is_active: bool = Query(True, description="是否只获取活跃的API Key"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取模型的API Key列表
    """
    api_logger.info(f"获取模型API Key列表请求: model_id={model_id}, 用户: {current_user.username}")
    
    try:
        api_logger.debug(f"开始获取模型API Key列表: model_id={model_id}")
        result_orm = ModelApiKeyService.get_api_keys_by_model(
            db=db, model_config_id=model_id, is_active=is_active
        )
        
        # 将ORM对象列表转换为Pydantic模型列表
        result_pydantic = [model_schema.ModelApiKey.model_validate(item) for item in result_orm]

        api_logger.info(f"模型API Key列表获取成功: 数量={len(result_pydantic)}")
        return success(data=result_pydantic, msg="模型API Key列表获取成功")
    except Exception as e:
        api_logger.error(f"获取模型API Key列表失败: model_id={model_id} - {str(e)}")
        raise


@router.post("/{model_id}/apikeys", response_model=ApiResponse, status_code=status.HTTP_201_CREATED)
async def create_model_api_key(
    model_id: uuid.UUID,
    api_key_data: model_schema.ModelApiKeyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    为模型创建API Key
    """
    api_logger.info(f"创建模型API Key请求: model_id={model_id}, model_name={api_key_data.model_name}, 用户: {current_user.username}")
    
    try:
        # 设置模型配置ID
        api_key_data.model_config_id = model_id
        
        api_logger.debug(f"开始创建模型API Key: {api_key_data.model_name}")
        result = await ModelApiKeyService.create_api_key(db=db, api_key_data=api_key_data)
        api_logger.info(f"模型API Key创建成功: {result.model_name} (ID: {result.id})")
        return success(data=result, msg="模型API Key创建成功")
    except Exception as e:
        api_logger.error(f"创建模型API Key失败: {api_key_data.model_name} - {str(e)}")
        raise


@router.get("/apikeys/{api_key_id}", response_model=ApiResponse)
def get_api_key_by_id(
    api_key_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    根据ID获取API Key
    """
    api_logger.info(f"获取API Key请求: api_key_id={api_key_id}, 用户: {current_user.username}")
    
    try:
        api_logger.debug(f"开始获取API Key: api_key_id={api_key_id}")
        result = ModelApiKeyService.get_api_key_by_id(db=db, api_key_id=api_key_id)
        api_logger.info(f"API Key获取成功: {result.model_name}")
        return success(data=result, msg="API Key获取成功")
    except Exception as e:
        api_logger.error(f"获取API Key失败: api_key_id={api_key_id} - {str(e)}")
        raise


@router.put("/apikeys/{api_key_id}", response_model=ApiResponse)
async def update_api_key(
    api_key_id: uuid.UUID,
    api_key_data: model_schema.ModelApiKeyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    更新API Key
    """
    api_logger.info(f"更新API Key请求: api_key_id={api_key_id}, 用户: {current_user.username}")
    
    try:
        api_logger.debug(f"开始更新API Key: api_key_id={api_key_id}")
        result = await ModelApiKeyService.update_api_key(db=db, api_key_id=api_key_id, api_key_data=api_key_data)
        api_logger.info(f"API Key更新成功: {result.model_name} (ID: {api_key_id})")
        result_pydantic = model_schema.ModelApiKey.model_validate(result) 
        return success(data=result_pydantic, msg="API Key更新成功")
    except Exception as e:
        api_logger.error(f"更新API Key失败: api_key_id={api_key_id} - {str(e)}")
        raise


@router.delete("/apikeys/{api_key_id}", response_model=ApiResponse)
def delete_api_key(
    api_key_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    删除API Key
    """
    api_logger.info(f"删除API Key请求: api_key_id={api_key_id}, 用户: {current_user.username}")
    
    try:
        api_logger.debug(f"开始删除API Key: api_key_id={api_key_id}")
        ModelApiKeyService.delete_api_key(db=db, api_key_id=api_key_id)
        api_logger.info(f"API Key删除成功: api_key_id={api_key_id}")
        return success(msg="API Key删除成功")
    except Exception as e:
        api_logger.error(f"删除API Key失败: api_key_id={api_key_id} - {str(e)}")
        raise


@router.post("/validate", response_model=ApiResponse)
async def validate_model_config(
    validate_data: model_schema.ModelValidateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    验证模型配置是否有效
    
    支持验证不同类型的模型：
    - llm: 大语言模型
    - chat: 对话模型
    - embedding: 向量模型
    - rerank: 重排序模型
    """
    api_logger.info(f"验证模型配置请求: {validate_data.model_name} ({validate_data.model_type}), 用户: {current_user.username}")
    
    result = await ModelConfigService.validate_model_config(
        db=db,
        model_name=validate_data.model_name,
        provider=validate_data.provider,
        api_key=validate_data.api_key,
        api_base=validate_data.api_base,
        model_type=validate_data.model_type,
        test_message=validate_data.test_message
    )
    
    return success(data=model_schema.ModelValidateResponse(**result), msg="验证完成")




