"""记忆配置（Memory Config）服务接口 - 基于 JWT 认证

将原先分散在 memory_storage / memory_forget / emotion_config / memory_reflection
控制器中的「记忆配置」读写接口，统一收口到 /memory_config 前缀下，使对内 /api 路由与对外
/v1（memory_config_api_controller）路径一致。

异步改造范式：
- 认证走 ``get_current_user_async``（async JWT 校验，不阻塞事件循环）
- ``resolve_config_id`` 使用 async 版本
- 全链路 async：service 层使用 async static 方法 + AsyncSession，端点内通过
  ``async with get_async_db_context()`` 统一管理异步事务

路由前缀: /memory_config
认证方式: JWT Token
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse

from app.controllers.emotion_config_controller import EmotionConfigUpdate
from app.core.error_codes import BizCode
from app.core.language_utils import get_language_from_header
from app.core.logging_config import get_api_logger
from app.core.quota_manager import _check_quota_async
from app.core.response_utils import fail, success
from app.db import get_async_db_context
from app.dependencies import CurrentUserSnapshot, get_current_user_async
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
from app.utils.config_utils import resolve_config_id_async

api_logger = get_api_logger()

# 遗忘引擎服务（迁自 memory_forget_controller）
forget_service = MemoryForgetService()

router = APIRouter(
    prefix="/memory_config",
    tags=["Memory Config"],
)

async def _resolve_config_id(config_id: UUID | int) -> UUID:
    """短生命周期 async session 内解析 config_id（无需 controller 显式传 db）。"""
    async with get_async_db_context() as db:
        return await resolve_config_id_async(config_id, db)

# ==================== 读取类 ====================

@router.get("/read_all_config", response_model=ApiResponse)  # 读取所有配置文件列表
async def read_all_config(
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
) -> dict:
    workspace_id = current_user.current_workspace_id

    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试查询配置但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    api_logger.info(f"用户 {current_user.username} 在工作空间 {workspace_id} 请求读取所有配置")
    try:
        async with get_async_db_context() as db:
            result = await DataConfigService.get_all_async(db, workspace_id=workspace_id)
            await db.commit()
        return success(data=result, msg="查询成功")
    except Exception as e:
        api_logger.error(f"Read all config failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "查询所有配置失败", str(e))


@router.post('/active_config', response_model=ApiResponse)
async def active_config(
        config_id: UUID = Body(..., embed=True),
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
        language_type: Optional[str] = Header(None, alias="X-Language-Type"),
) -> dict:
    workspace_id = current_user.current_workspace_id
    if workspace_id is None:
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    from app.core.exceptions import BusinessException
    from app.schemas.memory_config_schema import ConfigurationError

    locale = get_language_from_header(language_type)
    try:
        async with get_async_db_context() as db:
            result = await DataConfigService.active_async(db, workspace_id, config_id, locale=locale)
            await db.commit()
        return success(data=result)
    except ConfigurationError as e:
        return fail(BizCode.INVALID_PARAMETER, str(e))
    except BusinessException as e:
        return fail(BizCode.INVALID_PARAMETER, str(e))


@router.get('/validate_active_config', response_model=ApiResponse)
async def validate_active_config_models(
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
        language_type: Optional[str] = Header(None, alias="X-Language-Type"),
) -> dict:
    """校验当前工作空间激活记忆配置中的模型 API 可用性"""
    from app.core.exceptions import BusinessException
    from app.services.memory_config_service import MemoryConfigService

    workspace_id = current_user.current_workspace_id
    if workspace_id is None:
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间")

    api_logger.info(f"用户 {current_user.username} 请求校验激活配置模型: workspace_id={workspace_id}")
    locale = get_language_from_header(language_type)

    try:
        async with get_async_db_context() as db:
            try:
                config_id = await MemoryConfigService(db).get_workspace_active_config_id_async(workspace_id)
            except BusinessException:
                from app.i18n.service import t
                return success(data={
                    "valid": False,
                    "warnings": [{"message": t("memory_config.workspace.no_active_config", locale=locale)}],
                })
            result = await MemoryConfigService(db).valid_config(config_id, locale=locale)
        return success(data=result)
    except Exception as e:
        api_logger.error(f"validate_active_config failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "校验激活配置失败", str(e))


@router.get("/read_config_extracted", response_model=ApiResponse)  # 读取某条抽取配置
async def read_config_extracted(
        config_id: UUID | int,
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
) -> dict:
    workspace_id = current_user.current_workspace_id
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试读取提取配置但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    resolved = await _resolve_config_id(config_id)
    api_logger.info(
        f"用户 {current_user.username} 在工作空间 {workspace_id} 请求读取提取配置: {resolved}"
    )
    try:
        async with get_async_db_context() as db:
            result = await DataConfigService.get_extracted_async(db, ConfigKey(config_id=resolved))
        return success(data=result, msg="查询成功")
    except Exception as e:
        api_logger.error(f"Read config extracted failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "查询配置失败", str(e))


@router.get("/read_config_forgetting", response_model=ApiResponse)
async def read_forgetting_config(
        config_id: UUID | int,
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """获取遗忘引擎配置"""
    workspace_id = current_user.current_workspace_id
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试读取遗忘引擎配置但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    api_logger.info(
        f"用户 {current_user.username} 在工作空间 {workspace_id} 请求读取遗忘引擎配置: {config_id}"
    )

    try:
        resolved = await _resolve_config_id(config_id)
        async with get_async_db_context() as db:
            config = await forget_service.read_forgetting_config_async(db=db, config_id=resolved)
        response_data = ForgettingConfigResponse(**config)
        return success(data=response_data.model_dump(), msg="查询成功")
    except ValueError as e:
        api_logger.warning(f"配置不存在: config_id={config_id}, 错误: {str(e)}")
        return fail(BizCode.INVALID_PARAMETER, f"配置不存在: {config_id}", str(e))
    except Exception as e:
        api_logger.error(f"读取遗忘引擎配置失败: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "查询遗忘引擎配置失败", str(e))


@router.get("/read_config_emotion", response_model=ApiResponse)
async def get_emotion_config(
        config_id: UUID | int = Query(..., description="配置ID"),
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """获取情绪引擎配置"""
    try:
        api_logger.info(
            f"用户 {current_user.username} 请求获取情绪配置",
            extra={"config_id": config_id}
        )
        resolved = await _resolve_config_id(config_id)
        async with get_async_db_context() as db:
            data = await EmotionConfigService.get_emotion_config_async(db, resolved)
        api_logger.info(
            "情绪配置获取成功",
            extra={"config_id": resolved, "emotion_enabled": data.get("emotion_enabled", False)}
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
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
) -> dict:
    """查询反思引擎配置（异步版本）"""
    try:
        resolved = await _resolve_config_id(config_id)
        api_logger.info(f"用户 {current_user.username} 查询反思配置，config_id: {resolved}")

        async with get_async_db_context() as db:
            result = await MemoryConfigRepository(db).query_reflection_config_by_id_async(resolved)

            reflection_config = {
                "config_id": result.config_id,
                "reflection_enabled": result.enable_self_reflexion,
                "reflection_period_in_hours": result.iteration_period,
                "reflexion_range": result.reflexion_range,
                "baseline": result.baseline,
                "reflection_model_id": result.reflection_model_id,
                "memory_verify": result.memory_verify,
                "quality_assessment": result.quality_assessment,
                "is_default": bool(result.is_default),
            }
        api_logger.info(f"成功查询反思配置，config_id: {resolved}")
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
async def create_config(
        payload: ConfigParamsCreate,
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
        x_language_type: Optional[str] = Header(None, alias="X-Language-Type"),
) -> dict:
    workspace_id = current_user.current_workspace_id
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试创建配置但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    api_logger.info(f"用户 {current_user.username} 在工作空间 {workspace_id} 请求创建配置: {payload.config_name}")
    try:
        payload.workspace_id = workspace_id
        async with get_async_db_context() as db:
            await _check_quota_async(db, current_user.tenant_id, "memory_engine_quota", "memory_engine", workspace_id=workspace_id)
            result = await DataConfigService.create_async(db, payload)
            await db.commit()
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
async def update_config(
        payload: ConfigUpdate,
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
) -> dict:
    workspace_id = current_user.current_workspace_id
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试更新配置但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    if payload.config_name is None and payload.config_desc is None and payload.scene_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试更新配置但未提供任何更新字段")
        return fail(BizCode.INVALID_PARAMETER, "请至少提供一个需要更新的字段",
                    "config_name, config_desc, scene_id 均为空")

    payload.config_id = await _resolve_config_id(payload.config_id)
    api_logger.info(f"用户 {current_user.username} 在工作空间 {workspace_id} 请求更新配置: {payload.config_id}")
    try:
        async with get_async_db_context() as db:
            result = await DataConfigService.update_async(db, payload)
            await db.commit()
        return success(data=result, msg="更新成功")
    except Exception as e:
        api_logger.error(f"Update config failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "更新配置失败", str(e))


@router.post("/update_config_extracted", response_model=ApiResponse)  # 更新抽取配置 所有业务字段均可选
async def update_config_extracted(
        payload: ConfigUpdateExtracted,
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
) -> dict:
    workspace_id = current_user.current_workspace_id
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试更新提取配置但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    payload.config_id = await _resolve_config_id(payload.config_id)
    api_logger.info(f"用户 {current_user.username} 在工作空间 {workspace_id} 请求更新提取配置: {payload.config_id}")
    try:
        async with get_async_db_context() as db:
            result = await DataConfigService.update_extracted_async(db, payload)
            await db.commit()
        return success(data=result, msg="更新成功")
    except Exception as e:
        api_logger.error(f"Update config extracted failed: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "更新配置失败", str(e))


@router.post("/update_config_forgetting", response_model=ApiResponse)
async def update_forgetting_config(
        payload: ForgettingConfigUpdateRequest,
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """更新遗忘引擎配置"""
    workspace_id = current_user.current_workspace_id
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试更新遗忘引擎配置但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    payload.config_id = await _resolve_config_id(payload.config_id)
    api_logger.info(
        f"用户 {current_user.username} 在工作空间 {workspace_id} 请求更新遗忘引擎配置: {payload.config_id}"
    )

    try:
        update_data = {
            key: value
            for key, value in payload.model_dump(exclude_none=True).items()
            if key != 'config_id'
        }
        async with get_async_db_context() as db:
            config = await forget_service.update_forgetting_config_async(
                db=db,
                config_id=payload.config_id,
                update_fields=update_data,
            )
            await db.commit()
        response_data = ForgettingConfigResponse(**config)
        return success(data=response_data.model_dump(), msg="更新成功")
    except ValueError as e:
        api_logger.warning(f"配置不存在: config_id={payload.config_id}, 错误: {str(e)}")
        return fail(BizCode.INVALID_PARAMETER, str(e), "ValueError")
    except Exception as e:
        api_logger.error(f"更新遗忘引擎配置失败: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "更新遗忘引擎配置失败", str(e))


@router.post("/update_config_emotion", response_model=ApiResponse)
async def update_emotion_config(
        config: EmotionConfigUpdate,
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """更新情绪引擎配置"""
    config.config_id = await _resolve_config_id(config.config_id)
    try:
        api_logger.info(
            f"用户 {current_user.username} 请求更新情绪配置",
            extra={
                "config_id": config.config_id,
                "emotion_enabled": config.emotion_enabled,
                "emotion_min_intensity": config.emotion_min_intensity,
            }
        )
        async with get_async_db_context() as db:
            config_data = config.model_dump(exclude={'config_id'})
            data = await EmotionConfigService.update_emotion_config_async(db, config.config_id, config_data)
            await db.commit()
        api_logger.info(
            "情绪配置更新成功",
            extra={"config_id": config.config_id, "emotion_enabled": data.get("emotion_enabled", False)},
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
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
) -> dict:
    """保存反思引擎配置"""
    try:
        config_id = await _resolve_config_id(request.config_id)
        if not config_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="缺少必需参数: config_id"
            )
        api_logger.info(f"用户 {current_user.username} 保存反思配置，config_id: {config_id}")

        async with get_async_db_context() as db:
            memory_config = await MemoryConfigRepository(db).update_reflection_config_async(
                config_id=config_id,
                enable_self_reflexion=request.reflection_enabled,
                iteration_period=request.reflection_period_in_hours,
                reflexion_range=request.reflexion_range,
                baseline=request.baseline,
                reflection_model_id=request.reflection_model_id,
                memory_verify=request.memory_verify,
                quality_assessment=request.quality_assessment,
            )
            await db.commit()
            await db.refresh(memory_config)

            reflection_result = {
                "config_id": memory_config.config_id,
                "enable_self_reflexion": memory_config.enable_self_reflexion,
                "iteration_period": memory_config.iteration_period,
                "reflexion_range": memory_config.reflexion_range,
                "baseline": memory_config.baseline,
                "reflection_model_id": memory_config.reflection_model_id,
                "memory_verify": memory_config.memory_verify,
                "quality_assessment": memory_config.quality_assessment,
            }

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
async def delete_config(
        config_id: UUID | int,
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
) -> dict:
    """删除记忆配置（带终端用户保护）"""
    workspace_id = current_user.current_workspace_id
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试删除配置但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    resolved = await _resolve_config_id(config_id)
    api_logger.info(
        f"用户 {current_user.username} 在工作空间 {workspace_id} 请求删除配置: config_id={resolved}"
    )

    try:
        from app.services.memory_config_service import MemoryConfigService

        async with get_async_db_context() as db:
            result = await MemoryConfigService.delete_config_async(db, config_id=resolved, workspace_id=workspace_id)
            await db.commit()

        if result["status"] == "error":
            api_logger.warning(
                f"记忆配置删除被拒绝: config_id={resolved}, reason={result['message']}"
            )
            return fail(
                code=BizCode.FORBIDDEN,
                msg=result["message"],
                data={"config_id": str(resolved), "is_default": result.get("is_default", False)},
            )

        if result["status"] == "warning":
            api_logger.warning(
                f"记忆配置正在使用，无法删除: config_id={resolved}"
            )
            return fail(
                code=BizCode.RESOURCE_IN_USE,
                msg=result["message"],
                data={"config_id": str(resolved), "is_default": result.get("is_default", False)},
            )

        api_logger.info(f"记忆配置删除成功: config_id={resolved}")
        return success(
            msg=result["message"],
            data={"config_id": str(resolved), "is_default": result.get("is_default", False)},
        )

    except Exception as e:
        api_logger.error(f"Delete config failed: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "删除配置失败", str(e))
