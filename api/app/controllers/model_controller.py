from fastapi import APIRouter, Depends, status, Query, Response
from fastapi.responses import JSONResponse
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
from app.core.response_utils import success, fail
from app.schemas.response_schema import ApiResponse, PageData
from app.services.model_service import ModelConfigService, ModelBaseService
from app.services.model_channel_service import ChannelApiKeyService
from app.services.model_impact_service import collect_model_impact
from app.core.logging_config import get_api_logger
from app.core.quota_stub import check_model_quota, check_model_activation_quota
from app.core.model_provider_config import get_model_provider_metadata

# 获取API专用日志器
api_logger = get_api_logger()

router = APIRouter(
    prefix="/models",
    tags=["Models"],
)


def _model_in_use_response(exc: BusinessException) -> JSONResponse | None:
    """模型被业务引用（RESOURCE_IN_USE）时渲染 409 + 影响面清单；其他业务异常返回 None。

    全局异常处理器会丢弃 context，故此处按既有 JSONResponse 先例自行渲染。
    """
    impact = exc.context.get("impact") if exc.code == BizCode.RESOURCE_IN_USE else None
    if impact is None:
        return None
    code = exc.code.value if isinstance(exc.code, BizCode) else exc.code
    return JSONResponse(status_code=409, content=fail(code, exc.message, data=impact))


@router.get("/type", response_model=ApiResponse)
def get_model_types():
    return success(msg="获取模型类型成功", data=list(ModelType))


@router.get("/provider", response_model=ApiResponse)
def get_model_providers(
    current_user: User = Depends(get_current_user),
):
    _ = current_user
    return success(msg="获取模型提供商成功", data=get_model_provider_metadata())

@router.get("/strategy", response_model=ApiResponse)
def get_model_strategies():
    return success(msg="获取模型策略成功", data=list(LoadBalanceStrategy))


@router.get("", response_model=ApiResponse)
def get_model_list(
        type: Optional[list[str]] = Query(None, description="模型类型筛选（支持多个，如 ?type=LLM 或 ?type=LLM,EMBEDDING）"),
        capability: Optional[list[str]] = Query(None, description="能力筛选（支持多个，如 ?capability=vision 或 ?capability=vision, video）"),
        provider: Optional[model_schema.ModelProvider] = Query(None, description="提供商筛选(基于API Key)"),
        is_active: Optional[bool] = Query(None, description="激活状态筛选"),
        is_public: Optional[bool] = Query(None, description="公开状态筛选"),
        is_available: Optional[bool] = Query(None, description="可用性筛选（未弃用且渠道候选非空）"),
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

    is_available=true 时仅返回"未弃用且渠道候选非空"的模型（服务端全量探测后内存分页），
    供选择器隐藏已弃用/无渠道模型；is_deprecated 详情见响应字段。
    """
    api_logger.info(
        f"获取模型配置列表请求: type={type}, provider={provider}, is_available={is_available}, page={page}, pagesize={pagesize}, tenant_id={current_user.tenant_id}")

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

        capability_list = []
        if capability is not None:
            flat_capability = []
            for item in capability:
                split_items = [c.strip() for c in item.split(',') if c.strip()]
                flat_capability.extend(split_items)

            unique_flat_capability = list(dict.fromkeys(flat_capability))
            capability_list = unique_flat_capability

        api_logger.info(f"获取模型type_list: {type_list}")
        query = model_schema.ModelConfigQuery(
            type=type_list,
            provider=provider,
            capability=capability_list,
            is_active=is_active,
            is_public=is_public,
            is_available=is_available,
            search=search,
            page=page,
            pagesize=pagesize
        )

        api_logger.debug(f"开始获取模型配置列表: {query.model_dump()}")
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
    """停用基础模型（软停用；引用面清单随响应返回，前端据此提示影响范围）"""

    impact = ModelBaseService.delete_model_base(db=db, model_base_id=model_base_id)
    return success(data=impact, msg="基础模型已停用")


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
    根据ID获取模型配置（管理详情；弃用模型返回 200 并携带 is_deprecated 标记）
    """
    api_logger.info(f"获取模型配置请求: model_id={model_id}, tenant_id={current_user.tenant_id}")

    try:
        api_logger.debug(f"开始获取模型配置: model_id={model_id}")
        result_orm = ModelConfigService.get_model_detail(db=db, model_id=model_id, tenant_id=current_user.tenant_id)
        api_logger.info(f"模型配置获取成功: {result_orm.name}")
        
        # 将ORM对象转换为Pydantic模型
        result_pydantic = model_schema.ModelConfig.model_validate(result_orm)
        result_pydantic.is_available = ModelConfigService.is_model_available(
            db, result_orm, current_user.tenant_id
        )

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
    创建自定义模型

    - 内嵌 credential 必填：创建时以该凭据做活体验证，验证通过后 config 与点名渠道
      单事务落库；验证失败拒绝创建（零写入）
    """
    api_logger.info(f"创建模型配置请求: {model_data.name}, 用户: {current_user.username}, tenant_id={current_user.tenant_id}")

    try:
        api_logger.debug(f"开始创建模型配置: {model_data.name}")
        result_orm = await ModelConfigService.create_model(
            db=db, model_data=model_data, tenant_id=current_user.tenant_id,
            created_by=current_user.id,
        )
        api_logger.info(f"模型配置创建成功: {result_orm.name} (ID: {result_orm.id})")
        
        # 将ORM对象转换为Pydantic模型
        result = model_schema.ModelConfig.model_validate(result_orm)
        
        return success(data=result, msg="模型配置创建成功")
    except Exception as e:
        api_logger.error(f"创建模型配置失败: {model_data.name} - {str(e)}")
        raise


@router.post("/composite", response_model=ApiResponse)
@check_model_quota
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
@check_model_activation_quota
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
            raise BusinessException("不允许更改模型类型", BizCode.INVALID_PARAMETER)
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
    except BusinessException as exc:
        response = _model_in_use_response(exc)
        if response is None:
            raise
        api_logger.warning(f"组合模型被业务引用，拒绝删除: model_id={model_id}, total={exc.context['impact']['total']}")
        return response
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
    更新模型配置（启用前做渠道可用性预检：无候选 409，组合成员为空 400；
    显式禁用命中业务引用 409 + data.impact）
    """
    api_logger.info(f"更新模型配置请求: model_id={model_id}, 用户: {current_user.username}, tenant_id={current_user.tenant_id}")

    if model_data.type is not None or model_data.provider is not None:
        raise BusinessException("不允许更改模型类型和供应商", BizCode.INVALID_PARAMETER)

    if model_data.is_active is not None:
        model_config = ModelConfigRepository.get_by_id(db, model_id, tenant_id=current_user.tenant_id)
        if not model_config:
            raise BusinessException("模型配置不存在", BizCode.MODEL_NOT_FOUND)
        if model_data.is_active:
            ChannelApiKeyService.assert_enableable(db, model_config, current_user.tenant_id)
        elif model_config.is_active:
            # 显式禁用（true→false 跃迁）引用门禁（D13②）：编辑已禁用模型不误拦
            impact = collect_model_impact(db, [model_id])
            if impact["total"] > 0:
                api_logger.warning(f"模型被业务引用，拒绝禁用: model_id={model_id}, total={impact['total']}")
                exc = BusinessException(
                    f"模型正被 {impact['total']} 处业务引用，无法禁用",
                    BizCode.RESOURCE_IN_USE,
                    context={"impact": impact},
                )
                response = _model_in_use_response(exc)
                if response is not None:
                    return response
                raise exc

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
    except BusinessException as exc:
        response = _model_in_use_response(exc)
        if response is None:
            raise
        api_logger.warning(f"模型被业务引用，拒绝删除: model_id={model_id}, total={exc.context['impact']['total']}")
        return response
    except Exception as e:
        api_logger.error(f"删除模型配置失败: model_id={model_id} - {str(e)}")
        raise


# ---------- Provider 域凭据（provider 级公共渠道；声明必须先于 /{model_id}/apikeys） ----------
@router.get("/provider/apikeys", response_model=ApiResponse)
def list_provider_api_keys(
    provider: Optional[str] = Query(None, description="供应商筛选"),
    is_active: Optional[bool] = Query(None, description="渠道启停筛选"),
    source: Optional[str] = Query(None, description="来源筛选（manual/platform/import）"),
    page: int = Query(1, ge=1, description="页码"),
    pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取租户 provider 级公共凭据列表（脱敏；覆盖该供应商全部未点名模型；点名渠道在模型域管理）"""
    api_logger.info(f"获取渠道凭据列表请求: provider={provider}, 用户: {current_user.username}")

    try:
        result = ChannelApiKeyService.list_provider_keys(
            db=db,
            tenant_id=current_user.tenant_id,
            provider=provider,
            is_active=is_active,
            source=source,
            page=page,
            pagesize=pagesize,
        )
        return success(data=result, msg="渠道凭据列表获取成功")
    except Exception as e:
        api_logger.error(f"获取渠道凭据列表失败: {str(e)}")
        raise


@router.post("/provider/apikeys", response_model=ApiResponse)
async def create_provider_api_key(
    api_key_data: model_schema.ProviderApiKeyCreate,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    登记供应商公共凭据（覆盖该供应商全部未点名模型）

    登记前服务端自选锚点模型做一次活体验证，失败 400 零落库。
    同凭据同端点已存在时幂等合并（200，不覆盖既有属性）；命中同凭据点名行时
    原地升级为 provider 级（200，覆盖集扩展为全量）；新建 201。
    """
    api_logger.info(f"登记供应商公共凭据请求: provider={api_key_data.provider}, 用户: {current_user.username}")

    try:
        result, action = await ChannelApiKeyService.create_provider_key(
            db=db,
            data=api_key_data,
            tenant_id=current_user.tenant_id,
            created_by=current_user.id,
        )
        if action == "created":
            response.status_code = status.HTTP_201_CREATED
        msg = {
            "created": "凭据登记成功",
            "upgraded": "凭据已合并，该渠道已升级为 provider 级公共凭据",
        }.get(action, "凭据已存在（合并到既有渠道）")
        api_logger.info(f"供应商公共凭据登记完成: provider={api_key_data.provider} action={action}")
        return success(data=result, msg=msg)
    except Exception as e:
        api_logger.error(f"登记供应商公共凭据失败: {str(e)}")
        raise


@router.get("/provider/apikeys/{apikey_id}", response_model=ApiResponse)
def get_provider_api_key(
    apikey_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取渠道凭据详情（脱敏；删除影响面 = model_names 覆盖清单）"""
    api_logger.info(f"获取渠道凭据详情请求: apikey_id={apikey_id}, 用户: {current_user.username}")

    try:
        result = ChannelApiKeyService.get_provider_key(
            db=db, apikey_id=apikey_id, tenant_id=current_user.tenant_id
        )
        return success(data=result, msg="渠道凭据获取成功")
    except Exception as e:
        api_logger.error(f"获取渠道凭据失败: apikey_id={apikey_id} - {str(e)}")
        raise


@router.put("/provider/apikeys/{apikey_id}", response_model=ApiResponse)
async def update_provider_api_key(
    apikey_id: uuid.UUID,
    api_key_data: model_schema.ProviderApiKeyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """更新渠道凭据（属性 / 启停 / 重填凭据；model_names 不可改）。

    provider 级渠道重填 api_key 前先做活体验证，失败 400 零落库。
    """
    api_logger.info(f"更新渠道凭据请求: apikey_id={apikey_id}, 用户: {current_user.username}")

    try:
        result = await ChannelApiKeyService.update_provider_key(
            db=db, apikey_id=apikey_id, data=api_key_data, tenant_id=current_user.tenant_id
        )
        return success(data=result, msg="渠道凭据更新成功")
    except Exception as e:
        api_logger.error(f"更新渠道凭据失败: apikey_id={apikey_id} - {str(e)}")
        raise


@router.delete("/provider/apikeys/{apikey_id}", response_model=ApiResponse)
def delete_provider_api_key(
    apikey_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """删除渠道凭据本体（不可恢复；影响面由列表/详情前置提示）"""
    api_logger.info(f"删除渠道凭据请求: apikey_id={apikey_id}, 用户: {current_user.username}")

    try:
        ChannelApiKeyService.delete_provider_key(
            db=db, apikey_id=apikey_id, tenant_id=current_user.tenant_id
        )
        api_logger.info(f"渠道凭据删除成功: apikey_id={apikey_id}")
        return success(msg="渠道凭据删除成功")
    except Exception as e:
        api_logger.error(f"删除渠道凭据失败: apikey_id={apikey_id} - {str(e)}")
        raise


# ---------- 模型域凭据（点名渠道列表 + 登记 + 解绑） ----------
@router.get("/{model_id}/apikeys", response_model=ApiResponse)
def get_model_api_keys(
    model_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取模型级（点名）渠道列表（脱敏；含停用，停用态可在此重新启用）

    管理面口径（非运行期候选链）：不含供应商公共备援；启停/编辑复用
    `PUT /models/provider/apikeys/{apikey_id}`，解绑走本域 DELETE。
    """
    api_logger.info(f"获取模型渠道凭据列表请求: model_id={model_id}, 用户: {current_user.username}")

    try:
        result = ChannelApiKeyService.list_model_candidates(
            db=db, model_id=model_id, tenant_id=current_user.tenant_id
        )
        api_logger.info(f"模型渠道凭据列表获取成功: 数量={len(result)}")
        return success(data=result, msg="模型渠道凭据列表获取成功")
    except Exception as e:
        api_logger.error(f"获取模型渠道凭据列表失败: model_id={model_id} - {str(e)}")
        raise


@router.post("/{model_id}/apikeys", response_model=ApiResponse, status_code=status.HTTP_201_CREATED)
async def create_model_api_key(
    model_id: uuid.UUID,
    api_key_data: model_schema.ApiKeyRegister,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    为模型登记点名凭据（provider/真实模型名由服务端按模型配置读取）

    登记前会做一次活体验证；同凭据同端点已存在时幂等合并（公共渠道吸收为 no-op）。
    新建 201；幂等合并/吸收 200。
    """
    api_logger.info(f"登记模型凭据请求: model_id={model_id}, 用户: {current_user.username}")

    try:
        result, action = await ChannelApiKeyService.add_model_key(
            db=db,
            model_id=model_id,
            data=api_key_data,
            tenant_id=current_user.tenant_id,
            created_by=current_user.id,
        )
        if action != "created":
            response.status_code = status.HTTP_200_OK
        msg = "凭据登记成功" if action == "created" else "凭据已存在（合并到既有渠道）"
        api_logger.info(f"模型凭据登记完成: model_id={model_id} action={action}")
        return success(data=result, msg=msg)
    except Exception as e:
        api_logger.error(f"登记模型凭据失败: model_id={model_id} - {str(e)}")
        raise


@router.delete("/{model_id}/apikeys/{apikey_id}", response_model=ApiResponse)
def unbind_model_api_key(
    model_id: uuid.UUID,
    apikey_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    解绑模型凭据（点名渠道移除该模型；移除后无点名模型则凭据随之删除）

    供应商公共凭据不可在此解绑，请前往 Provider 域停用/删除。
    """
    api_logger.info(
        f"解绑模型凭据请求: model_id={model_id}, apikey_id={apikey_id}, 用户: {current_user.username}"
    )

    try:
        result = ChannelApiKeyService.unbind_model_key(
            db=db, model_id=model_id, apikey_id=apikey_id, tenant_id=current_user.tenant_id
        )
        api_logger.info(f"模型凭据解绑成功: model_id={model_id}, apikey_id={apikey_id} deleted={result['deleted']}")
        msg = "凭据解绑成功（该凭据已随之删除）" if result["deleted"] else "凭据解绑成功"
        return success(data=result, msg=msg)
    except Exception as e:
        api_logger.error(f"解绑模型凭据失败: model_id={model_id}, apikey_id={apikey_id} - {str(e)}")
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


