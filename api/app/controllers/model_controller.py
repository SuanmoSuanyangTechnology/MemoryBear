from fastapi import APIRouter, Depends, status, Query
from sqlalchemy.orm import Session
from typing import Optional
import uuid

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.db import get_db
from app.dependencies import get_current_user
from app.models.models_model import ModelProvider, ModelType, LoadBalanceStrategy
from app.models.user_model import User
from app.repositories.model_repository import ModelConfigRepository
from app.schemas import model_schema
from app.core.response_utils import success
from app.schemas.response_schema import ApiResponse, PageData
from app.services.model_service import ModelConfigService, ModelApiKeyService, ModelBaseService
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
    providers = [p for p in ModelProvider if p != ModelProvider.COMPOSITE]
    return success(msg="获取模型提供商成功", data=providers)

@router.get("/strategy", response_model=ApiResponse)
def get_model_strategies():
    return success(msg="获取模型策略成功", data=list(LoadBalanceStrategy))


@router.get("", response_model=ApiResponse)
def get_model_list(
        type: Optional[list[str]] = Query(None, description="模型类型筛选（支持多个，如 ?type=LLM 或 ?type=LLM,EMBEDDING）"),
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
    api_logger.info(
        f"获取模型配置列表请求: type={type}, provider={provider}, page={page}, pagesize={pagesize}, tenant_id={current_user.tenant_id}")

    try:
        # 解析 type 参数（支持逗号分隔）
        type_list = []
        if type is not None:
            flat_type = []
            for item in type:
                split_items = [t.strip() for t in item.split(',') if t.strip()]
                flat_type.extend(split_items)

            unique_flat_type = list(dict.fromkeys(flat_type))
            type_list = [ModelType(t.lower()) for t in unique_flat_type]

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


@router.get("/new", response_model=ApiResponse)
def get_model_list_new(
    type: Optional[list[str]] = Query(None, description="模型类型筛选（支持多个，如 ?type=LLM 或 ?type=LLM,EMBEDDING）"),
    provider: Optional[model_schema.ModelProvider] = Query(None, description="提供商筛选(基于ModelConfig)"),
    is_active: Optional[bool] = Query(None, description="激活状态筛选"),
    is_public: Optional[bool] = Query(None, description="公开状态筛选"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    is_composite: Optional[bool] = Query(None, description="组合模型筛选"),
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
    api_logger.info(f"获取模型配置列表请求: type={type}, provider={provider}, tenant_id={current_user.tenant_id}")
    
    try:
        # 解析 type 参数（支持逗号分隔）
        type_list = []
        if type is not None:
            flat_type = []
            for item in type:
                split_items = [t.strip() for t in item.split(',') if t.strip()]
                flat_type.extend(split_items)

            unique_flat_type = list(dict.fromkeys(flat_type))
            type_list = [ModelType(t.lower()) for t in unique_flat_type]
        
        api_logger.info(f"获取模型type_list: {type_list}")
        query = model_schema.ModelConfigQueryNew(
            type=type_list,
            provider=provider,
            is_active=is_active,
            is_public=is_public,
            is_composite=is_composite,
            search=search
        )
        
        api_logger.debug(f"开始获取模型配置列表: {query.model_dump()}")
        result = ModelConfigService.get_model_list_new(db=db, query=query, tenant_id=current_user.tenant_id)
        api_logger.info(f"模型配置列表获取成功: 分组数={len(result)}, 总模型数={sum(len(item['models']) for item in result)}")
        return success(data=result, msg="模型配置列表获取成功")
    except Exception as e:
        api_logger.error(f"获取模型配置列表失败: {str(e)}")
        raise


@router.get("/model_plaza", response_model=ApiResponse)
def get_model_plaza_list(
    type: Optional[ModelType] = Query(None, description="模型类型"),
    provider: Optional[ModelProvider] = Query(None, description="供应商"),
    is_official: Optional[bool] = Query(None, description="是否官方模型"),
    is_deprecated: Optional[bool] = Query(None, description="是否弃用"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """模型广场查询接口（按供应商分组）"""
    
    query = model_schema.ModelBaseQuery(
        type=type,
        provider=provider,
        is_official=is_official,
        is_deprecated=is_deprecated,
        search=search
    )
    result = ModelBaseService.get_model_base_list(db=db, query=query, tenant_id=current_user.tenant_id)
    return success(data=result, msg="模型广场列表获取成功")


@router.get("/model_plaza/{model_base_id}", response_model=ApiResponse)
def get_model_base_by_id(
    model_base_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取基础模型详情"""
    
    result = ModelBaseService.get_model_base_by_id(db=db, model_base_id=model_base_id)
    return success(data=model_schema.ModelBase.model_validate(result), msg="基础模型获取成功")


@router.post("/model_plaza", response_model=ApiResponse)
def create_model_base(
    data: model_schema.ModelBaseCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """创建基础模型"""
    
    result = ModelBaseService.create_model_base(db=db, data=data)
    return success(data=model_schema.ModelBase.model_validate(result), msg="基础模型创建成功")


@router.put("/model_plaza/{model_base_id}", response_model=ApiResponse)
def update_model_base(
    model_base_id: uuid.UUID,
    data: model_schema.ModelBaseUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """更新基础模型"""
    
    # 不允许更改type类型
    if data.type is not None or data.provider is not None:
        raise BusinessException("不允许更改模型类型和供应商", BizCode.INVALID_PARAMETER)
    
    result = ModelBaseService.update_model_base(db=db, model_base_id=model_base_id, data=data)
    return success(data=model_schema.ModelBase.model_validate(result), msg="基础模型更新成功")


@router.delete("/model_plaza/{model_base_id}", response_model=ApiResponse)
def delete_model_base(
    model_base_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """删除基础模型"""
    
    ModelBaseService.delete_model_base(db=db, model_base_id=model_base_id)
    return success(msg="基础模型删除成功")


@router.post("/model_plaza/{model_base_id}/add", response_model=ApiResponse)
def add_model_from_plaza(
    model_base_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """从模型广场添加模型到模型列表"""
    
    result = ModelBaseService.add_model_from_plaza(db=db, model_base_id=model_base_id, tenant_id=current_user.tenant_id)
    return success(data=model_schema.ModelConfig.model_validate(result), msg="模型添加成功")


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


@router.post("/composite", response_model=ApiResponse)
async def create_composite_model(
    model_data: model_schema.CompositeModelCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    创建组合模型
    
    - 绑定一个或多个现有的 API Key
    - 所有 API Key 必须来自非组合模型
    - 所有 API Key 关联的模型类型必须与组合模型类型一致
    """
    api_logger.info(f"创建组合模型请求: {model_data.name}, 用户: {current_user.username}, tenant_id={current_user.tenant_id}")
    
    try:
        result_orm = await ModelConfigService.create_composite_model(db=db, model_data=model_data, tenant_id=current_user.tenant_id)
        api_logger.info(f"组合模型创建成功: {result_orm.name} (ID: {result_orm.id})")
        
        result = model_schema.ModelConfig.model_validate(result_orm)
        return success(data=result, msg="组合模型创建成功")
    except Exception as e:
        api_logger.error(f"创建组合模型失败: {model_data.name} - {str(e)}")
        raise


@router.put("/composite/{model_id}", response_model=ApiResponse)
async def update_composite_model(
    model_id: uuid.UUID,
    model_data: model_schema.CompositeModelCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """更新组合模型"""
    api_logger.info(f"更新组合模型请求: model_id={model_id}, 用户: {current_user.username}")
    
    try:
        if model_data.type is not None:
            raise BusinessException("不允许更改模型类型和供应商", BizCode.INVALID_PARAMETER)
        result_orm = await ModelConfigService.update_composite_model(db=db, model_id=model_id, model_data=model_data, tenant_id=current_user.tenant_id)
        api_logger.info(f"组合模型更新成功: {result_orm.name} (ID: {model_id})")
        
        result = model_schema.ModelConfig.model_validate(result_orm)
        return success(data=result, msg="组合模型更新成功")
    except Exception as e:
        api_logger.error(f"更新组合模型失败: model_id={model_id} - {str(e)}")
        raise


@router.delete("/composite/{model_id}", response_model=ApiResponse)
def delete_composite_model(
    model_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """删除组合模型"""
    api_logger.info(f"删除组合模型请求: model_id={model_id}, 用户: {current_user.username}")
    
    try:
        ModelConfigService.delete_model(db=db, model_id=model_id, tenant_id=current_user.tenant_id)
        api_logger.info(f"组合模型删除成功: model_id={model_id}")
        return success(msg="组合模型删除成功")
    except Exception as e:
        api_logger.error(f"删除组合模型失败: model_id={model_id} - {str(e)}")
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


@router.post("/provider/apikeys", response_model=ApiResponse)
async def create_model_api_key_by_provider(
        api_key_data: model_schema.ModelApiKeyCreateByProvider,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    根据供应商为所有匹配的模型创建API Key
    """
    api_logger.info(f"创建API Key请求: provider={api_key_data.provider}, 用户: {current_user.username}")

    try:
        # 根据tenant_id和provider筛选model_config_id列表
        model_config_ids = api_key_data.model_config_ids
        if not model_config_ids:
            model_config_ids = ModelConfigRepository.get_model_config_ids_by_provider(
                db=db,
                tenant_id=current_user.tenant_id,
                provider=api_key_data.provider
            )
        
        if not model_config_ids:
            raise BusinessException(f"未找到供应商 {api_key_data.provider} 的模型配置", BizCode.MODEL_NOT_FOUND)
        
        # 构造schema并调用service
        create_data = model_schema.ModelApiKeyCreateByProvider(
            provider=api_key_data.provider,
            api_key=api_key_data.api_key,
            api_base=api_key_data.api_base,
            description=api_key_data.description,
            config=api_key_data.config,
            is_active=api_key_data.is_active,
            priority=api_key_data.priority,
            model_config_ids=model_config_ids
        )
        created_keys, failed_models = await ModelApiKeyService.create_api_key_by_provider(db=db, data=create_data)
        
        api_logger.info(f"API Key创建成功: 关联{len(created_keys)}个模型")
        # result_list = [model_schema.ModelApiKey.model_validate(key) for key in created_keys]
        result = "API Key已存在" if len(created_keys) == 0 and len(failed_models) == 0 else \
            f"成功为 {len(created_keys)} 个模型创建API Key, 失败模型列表{failed_models}"
        return success(data=result, msg=f"成功为 {len(created_keys)} 个模型创建API Key")
    except Exception as e:
        api_logger.error(f"创建API Key失败: {str(e)}")
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
        api_key_data.model_config_ids = [model_id]
        
        api_logger.debug(f"开始创建模型API Key: {api_key_data.model_name}")
        result_orm = await ModelApiKeyService.create_api_key(db=db, api_key_data=api_key_data)
        api_logger.info(f"模型API Key创建成功: {result_orm.model_name} (ID: {result_orm.id})")
        result = model_schema.ModelApiKey.model_validate(result_orm)
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


