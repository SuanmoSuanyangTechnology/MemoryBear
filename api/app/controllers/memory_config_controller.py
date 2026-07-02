"""记忆配置（Memory Config）服务接口 - 基于 JWT 认证

将原先分散在 memory_storage / memory_forget / emotion_config / memory_reflection
控制器中的「记忆配置」读写接口，统一收口到 /memory_config 前缀下，使对内 /api 路由与对外
/v1（memory_config_api_controller）路径一致。

各 handler 由原域控制器整体迁入（函数体逻辑不变，仅装饰器路径调整为 /memory_config/*）。
方法沿用对内现状（读 GET、创建/更新 POST、删除 DELETE）。

路由前缀: /memory_config
认证方式: JWT Token
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.controllers.emotion_config_controller import EmotionConfigUpdate
from app.core.error_codes import BizCode
from app.core.language_utils import get_language_from_header
from app.core.logging_config import get_api_logger
from app.core.quota_stub import check_memory_engine_quota
from app.core.response_utils import fail, success
from app.db import get_db
from app.dependencies import get_current_user
from app.models.user_model import User
from app.repositories.memory_config_repository import MemoryConfigRepository
from app.schemas.memory_reflection_schemas import Memory_Reflection
from app.schemas.memory_storage_schema import (
    ConfigKey,
    ConfigParamsCreate,
    ConfigUpdate,
    ConfigUpdateExtracted,
    ForgettingConfigResponse,
    ForgettingConfigUpdateRequest,
)
from app.schemas.response_schema import ApiResponse
from app.services.emotion_config_service import EmotionConfigService
from app.services.memory_forget_service import MemoryForgetService
from app.services.memory_storage_service import DataConfigService
from app.utils.config_utils import resolve_config_id

api_logger = get_api_logger()

# 遗忘引擎服务（迁自 memory_forget_controller）
forget_service = MemoryForgetService()

router = APIRouter(
    prefix="/memory_config",
    tags=["Memory Config"],
)


# ==================== 读取类 ====================

@router.get("/read_all_config", response_model=ApiResponse)  # 读取所有配置文件列表
def read_all_config(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
) -> dict:
    workspace_id = current_user.current_workspace_id

    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试查询配置但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    api_logger.info(f"用户 {current_user.username} 在工作空间 {workspace_id} 请求读取所有配置")
    try:
        svc = DataConfigService(db)
        # 传递 workspace_id 进行过滤（保持为 UUID 类型）
        result = svc.get_all(workspace_id=workspace_id)
        return success(data=result, msg="查询成功")
    except Exception as e:
        api_logger.error(f"Read all config failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "查询所有配置失败", str(e))


@router.post('/active_config', response_model=ApiResponse)
async def active_config(
        config_id: UUID = Body(..., embed=True),
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
) -> dict:
    workspace_id = current_user.current_workspace_id

    if workspace_id is None:
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    from app.core.exceptions import BusinessException
    from app.schemas.memory_config_schema import ConfigurationError

    svc = DataConfigService(db)
    try:
        result = await svc.active(workspace_id, config_id)
        if result.get("success"):
            return success(data=result)
        else:
            return fail(code=BizCode.API_KEY_INACTIVE, msg="配置异常", data=result)
    except ConfigurationError as e:
        return fail(BizCode.INVALID_PARAMETER, str(e))
    except BusinessException as e:
        return fail(BizCode.INVALID_PARAMETER, str(e))


@router.get('/validate_active_config', response_model=ApiResponse)
async def validate_active_config_models(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
) -> dict:
    """校验当前工作空间激活记忆配置中的模型 API 可用性"""
    from app.core.exceptions import BusinessException
    from app.services.memory_config_service import MemoryConfigService

    workspace_id = current_user.current_workspace_id
    if workspace_id is None:
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间")

    api_logger.info(f"用户 {current_user.username} 请求校验激活配置模型: workspace_id={workspace_id}")

    try:
        config_id = MemoryConfigService(db).get_workspace_active_config_id(workspace_id)
    except BusinessException:
        return success(data={"valid": False, "warnings": [{"message": "当前工作空间无启用的记忆配置"}]})

    result = await MemoryConfigService(db).valid_config(config_id)
    return success(data=result)


@router.get("/read_config_extracted", response_model=ApiResponse)  # 读取某条抽取配置
def read_config_extracted(
        config_id: UUID | int,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
) -> dict:
    workspace_id = current_user.current_workspace_id
    config_id = resolve_config_id(config_id, db)
    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试读取提取配置但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    api_logger.info(f"用户 {current_user.username} 在工作空间 {workspace_id} 请求读取提取配置: {config_id}")
    try:
        svc = DataConfigService(db)
        result = svc.get_extracted(ConfigKey(config_id=config_id))
        return success(data=result, msg="查询成功")
    except Exception as e:
        api_logger.error(f"Read config extracted failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "查询配置失败", str(e))


@router.get("/read_config_forgetting", response_model=ApiResponse)
async def read_forgetting_config(
        config_id: UUID | int,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    """获取遗忘引擎配置"""
    workspace_id = current_user.current_workspace_id

    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试读取遗忘引擎配置但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    api_logger.info(
        f"用户 {current_user.username} 在工作空间 {workspace_id} 请求读取遗忘引擎配置: {config_id}"
    )

    try:
        config_id = resolve_config_id(config_id, db)
        config = forget_service.read_forgetting_config(db=db, config_id=config_id)
        response_data = ForgettingConfigResponse(**config)
        return success(data=response_data.model_dump(), msg="查询成功")
    except ValueError as e:
        api_logger.warning(f"配置不存在: config_id={config_id}, 错误: {str(e)}")
        return fail(BizCode.INVALID_PARAMETER, f"配置不存在: {config_id}", str(e))
    except Exception as e:
        api_logger.error(f"读取遗忘引擎配置失败: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "查询遗忘引擎配置失败", str(e))


@router.get("/read_config_emotion", response_model=ApiResponse)
def get_emotion_config(
        config_id: UUID | int = Query(..., description="配置ID"),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    """获取情绪引擎配置"""
    try:
        api_logger.info(
            f"用户 {current_user.username} 请求获取情绪配置",
            extra={"config_id": config_id}
        )
        config_id = resolve_config_id(config_id, db)
        config_service = EmotionConfigService(db)
        data = config_service.get_emotion_config(config_id)
        api_logger.info(
            "情绪配置获取成功",
            extra={"config_id": config_id, "emotion_enabled": data.get("emotion_enabled", False)}
        )
        return success(data=data, msg="情绪配置获取成功")
    except ValueError as e:
        api_logger.warning(f"获取情绪配置失败: {str(e)}", extra={"config_id": config_id})
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        api_logger.error(f"获取情绪配置失败: {str(e)}", extra={"config_id": config_id}, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取情绪配置失败: {str(e)}"
        )


@router.get("/read_config_reflection", response_model=ApiResponse)
async def start_reflection_configs(
        config_id: UUID | int,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
) -> dict:
    """查询反思引擎配置"""
    config_id = resolve_config_id(config_id, db)
    try:
        config_id = resolve_config_id(config_id, db)
        api_logger.info(f"用户 {current_user.username} 查询反思配置，config_id: {config_id}")
        result = MemoryConfigRepository.query_reflection_config_by_id(db, config_id)
        memory_config_id = resolve_config_id(result.config_id, db)

        reflection_config = {
            "config_id": memory_config_id,
            "reflection_enabled": result.enable_self_reflexion,
            "reflection_period_in_hours": result.iteration_period,
            "reflexion_range": result.reflexion_range,
            "baseline": result.baseline,
            "reflection_model_id": result.reflection_model_id,
            "memory_verify": result.memory_verify,
            "quality_assessment": result.quality_assessment,
            "is_default": result.is_default
        }
        api_logger.info(f"成功查询反思配置，config_id: {config_id}")
        return success(data=reflection_config, msg="反思配置查询成功")
    except HTTPException:
        raise
    except Exception as e:
        api_logger.error(f"查询反思配置失败: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询反思配置失败: {str(e)}"
        )


# ==================== 创建 / 更新 / 删除 ====================

@router.post("/create_config", response_model=ApiResponse)  # 创建配置文件，其他参数默认
@check_memory_engine_quota
def create_config(
        payload: ConfigParamsCreate,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
        x_language_type: Optional[str] = Header(None, alias="X-Language-Type"),
) -> dict:
    workspace_id = current_user.current_workspace_id
    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试创建配置但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    api_logger.info(f"用户 {current_user.username} 在工作空间 {workspace_id} 请求创建配置: {payload.config_name}")
    try:
        # 将 workspace_id 注入到 payload 中（保持为 UUID 类型）
        payload.workspace_id = workspace_id
        svc = DataConfigService(db)
        result = svc.create(payload)
        return success(data=result, msg="创建成功")
    except ValueError as e:
        err_str = str(e)
        if err_str.startswith("DUPLICATE_CONFIG_NAME:"):
            config_name = err_str.split(":", 1)[1]
            api_logger.warning(f"重复的配置名称 '{config_name}' 在工作空间 {workspace_id}")
            lang = get_language_from_header(x_language_type)
            if lang == "en":
                msg = fail(BizCode.BAD_REQUEST, "Config name already exists",
                           f"A config named \"{config_name}\" already exists in the current workspace. Please use a different name.")
            else:
                msg = fail(BizCode.BAD_REQUEST, "配置名称已存在",
                           f"当前工作空间下已存在名为「{config_name}」的记忆配置，请使用其他名称")
            return JSONResponse(status_code=400, content=msg)
        api_logger.error(f"Create config failed: {err_str}")
        return fail(BizCode.INTERNAL_ERROR, "创建配置失败", err_str)
    except Exception as e:
        from sqlalchemy.exc import IntegrityError
        if isinstance(e, IntegrityError) and "uq_workspace_config_name" in str(getattr(e, 'orig', '')):
            api_logger.warning(f"重复的配置名称 '{payload.config_name}' 在工作空间 {workspace_id}")
            lang = get_language_from_header(x_language_type)
            if lang == "en":
                msg = fail(BizCode.BAD_REQUEST, "Config name already exists",
                           f"A config named \"{payload.config_name}\" already exists in the current workspace. Please use a different name.")
            else:
                msg = fail(BizCode.BAD_REQUEST, "配置名称已存在",
                           f"当前工作空间下已存在名为「{payload.config_name}」的记忆配置，请使用其他名称")
            return JSONResponse(status_code=400, content=msg)
        api_logger.error(f"Create config failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "创建配置失败", str(e))


@router.post("/update_config", response_model=ApiResponse)  # 更新配置文件中name和desc
def update_config(
        payload: ConfigUpdate,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
) -> dict:
    workspace_id = current_user.current_workspace_id
    payload.config_id = resolve_config_id(payload.config_id, db)
    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试更新配置但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    # 校验至少有一个字段需要更新
    if payload.config_name is None and payload.config_desc is None and payload.scene_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试更新配置但未提供任何更新字段")
        return fail(BizCode.INVALID_PARAMETER, "请至少提供一个需要更新的字段",
                    "config_name, config_desc, scene_id 均为空")

    api_logger.info(f"用户 {current_user.username} 在工作空间 {workspace_id} 请求更新配置: {payload.config_id}")
    try:
        svc = DataConfigService(db)
        result = svc.update(payload)
        return success(data=result, msg="更新成功")
    except Exception as e:
        api_logger.error(f"Update config failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "更新配置失败", str(e))


@router.post("/update_config_extracted", response_model=ApiResponse)  # 更新抽取配置 所有业务字段均可选
def update_config_extracted(
        payload: ConfigUpdateExtracted,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
) -> dict:
    workspace_id = current_user.current_workspace_id
    payload.config_id = resolve_config_id(payload.config_id, db)
    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试更新提取配置但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    api_logger.info(f"用户 {current_user.username} 在工作空间 {workspace_id} 请求更新提取配置: {payload.config_id}")
    try:
        svc = DataConfigService(db)
        result = svc.update_extracted(payload)
        return success(data=result, msg="更新成功")
    except Exception as e:
        api_logger.error(f"Update config extracted failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "更新配置失败", str(e))


@router.post("/update_config_forgetting", response_model=ApiResponse)
async def update_forgetting_config(
        payload: ForgettingConfigUpdateRequest,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    """更新遗忘引擎配置"""
    workspace_id = current_user.current_workspace_id
    payload.config_id = resolve_config_id((payload.config_id), db)

    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试更新遗忘引擎配置但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    api_logger.info(
        f"用户 {current_user.username} 在工作空间 {workspace_id} 请求更新遗忘引擎配置: {payload.config_id}"
    )

    try:
        # 构建更新字段字典（排除 None 值和 config_id）
        update_data = {
            key: value
            for key, value in payload.model_dump(exclude_none=True).items()
            if key != 'config_id'
        }
        config = forget_service.update_forgetting_config(
            db=db,
            config_id=payload.config_id,
            update_fields=update_data
        )
        response_data = ForgettingConfigResponse(**config)
        return success(data=response_data.model_dump(), msg="更新成功")
    except ValueError as e:
        api_logger.warning(f"配置不存在: config_id={payload.config_id}, 错误: {str(e)}")
        return fail(BizCode.INVALID_PARAMETER, str(e), "ValueError")
    except Exception as e:
        db.rollback()
        api_logger.error(f"更新遗忘引擎配置失败: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "更新遗忘引擎配置失败", str(e))


@router.post("/update_config_emotion", response_model=ApiResponse)
def update_emotion_config(
        config: EmotionConfigUpdate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    """更新情绪引擎配置"""
    config.config_id = resolve_config_id(config.config_id, db)
    try:
        api_logger.info(
            f"用户 {current_user.username} 请求更新情绪配置",
            extra={
                "config_id": config.config_id,
                "emotion_enabled": config.emotion_enabled,
                "emotion_min_intensity": config.emotion_min_intensity
            }
        )
        config_service = EmotionConfigService(db)
        config_data = config.model_dump(exclude={'config_id'})
        data = config_service.update_emotion_config(config.config_id, config_data)
        api_logger.info(
            "情绪配置更新成功",
            extra={"config_id": config.config_id, "emotion_enabled": data.get("emotion_enabled", False)}
        )
        return success(data=data, msg="情绪配置更新成功")
    except ValueError as e:
        api_logger.warning(f"更新情绪配置失败: {str(e)}", extra={"config_id": config.config_id})
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        api_logger.error(f"更新情绪配置失败: {str(e)}", extra={"config_id": config.config_id}, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新情绪配置失败: {str(e)}"
        )


@router.post("/update_config_reflection", response_model=ApiResponse)
async def save_reflection_config(
        request: Memory_Reflection,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
) -> dict:
    """保存反思引擎配置"""
    try:
        config_id = request.config_id
        config_id = resolve_config_id(config_id, db)
        if not config_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="缺少必需参数: config_id"
            )
        api_logger.info(f"用户 {current_user.username} 保存反思配置，config_id: {config_id}")

        memory_config = MemoryConfigRepository.update_reflection_config(
            db,
            config_id=config_id,
            enable_self_reflexion=request.reflection_enabled,
            iteration_period=request.reflection_period_in_hours,
            reflexion_range=request.reflexion_range,
            baseline=request.baseline,
            reflection_model_id=request.reflection_model_id,
            memory_verify=request.memory_verify,
            quality_assessment=request.quality_assessment
        )

        db.commit()
        db.refresh(memory_config)

        reflection_result = {
            "config_id": memory_config.config_id,
            "enable_self_reflexion": memory_config.enable_self_reflexion,
            "iteration_period": memory_config.iteration_period,
            "reflexion_range": memory_config.reflexion_range,
            "baseline": memory_config.baseline,
            "reflection_model_id": memory_config.reflection_model_id,
            "memory_verify": memory_config.memory_verify,
            "quality_assessment": memory_config.quality_assessment}

        return success(data=reflection_result, msg="反思配置成功")
    except ValueError as ve:
        api_logger.error(f"参数错误: {str(ve)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"参数错误: {str(ve)}"
        )
    except Exception as e:
        api_logger.error(f"反思配置保存失败: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"反思配置保存失败: {str(e)}"
        )


@router.delete("/delete_config", response_model=ApiResponse)  # 删除记忆配置（按配置ID）
def delete_config(
        config_id: UUID | int,
        force: bool = Query(False, description="是否强制删除（即使有终端用户正在使用）"),
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
) -> dict:
    """删除记忆配置（带终端用户保护）

    - 检查是否为默认配置，默认配置不允许删除
    - 检查是否有终端用户连接到该配置
    - 如果有连接且 force=False，返回警告
    - 如果 force=True，清除终端用户引用后删除配置
    """
    workspace_id = current_user.current_workspace_id
    config_id = resolve_config_id(config_id, db)
    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试删除配置但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    api_logger.info(
        f"用户 {current_user.username} 在工作空间 {workspace_id} 请求删除配置: "
        f"config_id={config_id}, force={force}"
    )

    try:
        from app.services.memory_config_service import MemoryConfigService

        config_service = MemoryConfigService(db)
        result = config_service.delete_config(config_id=config_id, force=force)

        if result["status"] == "error":
            api_logger.warning(
                f"记忆配置删除被拒绝: config_id={config_id}, reason={result['message']}"
            )
            return fail(
                code=BizCode.FORBIDDEN,
                msg=result["message"],
                data={"config_id": str(config_id), "is_default": result.get("is_default", False)}
            )

        if result["status"] == "warning":
            api_logger.warning(
                f"记忆配置正在使用，无法删除: config_id={config_id}, "
                f"connected_count={result['connected_count']}"
            )
            return fail(
                code=BizCode.RESOURCE_IN_USE,
                msg=result["message"],
                data={
                    "connected_count": result["connected_count"],
                    "force_required": result["force_required"]
                }
            )

        api_logger.info(
            f"记忆配置删除成功: config_id={config_id}, "
            f"affected_users={result['affected_users']}"
        )
        return success(
            msg=result["message"],
            data={"affected_users": result["affected_users"]}
        )

    except Exception as e:
        api_logger.error(f"Delete config failed: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "删除配置失败", str(e))
