"""
Memory Reflection Controller

This module provides REST API endpoints for managing memory reflection configurations
and operations. It handles reflection engine setup, configuration management, and
execution of self-reflection processes across memory systems.

Key Features:
- Reflection configuration management (save, retrieve, update)
- Workspace-wide reflection execution across multiple applications
- Individual configuration-based reflection runs
- Multi-language support for reflection outputs
- Integration with Neo4j memory storage and LLM models
- Comprehensive error handling and logging
"""

import uuid
from typing import Optional
from uuid import UUID

from app.core.language_utils import get_language_from_header
from app.core.logging_config import get_api_logger
from app.core.memory.storage_services.reflection_engine.self_reflexion import (
    ReflectionConfig,
    ReflectionEngine, ReflectionRange, ReflectionBaseline,
)
from app.core.models import RedBearLLM, RedBearModelConfig
from app.core.response_utils import fail, success
from app.db import get_async_db_context
from app.dependencies import get_current_user_async, CurrentUserSnapshot
from app.repositories.end_user_repository import EndUserRepository
from app.repositories.memory_config_repository import MemoryConfigRepository
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.repositories.reflection_log_repository import ReflectionLogRepository
from app.schemas.memory_reflection_schemas import (
    ReflectionLogListItem,
    ReflectionLogDetail,
    SubProblemEnum,
    TriggerTypeEnum,
    LogStatusEnum,
)
from app.services.memory_reflection_service import (
    MemoryReflectionService,
    get_workspace_apps_detailed_async,
)
from app.services.model_service import ModelApiKeyService, ModelConfigService
from app.utils.config_utils import resolve_config_id_async
from app.core.error_codes import BizCode
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, status, Header, Query, Path

# Load environment variables for configuration
load_dotenv()

# Initialize API logger for request tracking and debugging
api_logger = get_api_logger()

# Configure router with prefix and tags for API organization
router = APIRouter(
    prefix="/memory",
    tags=["Memory"],
)


@router.get("/reflection/logs/stats")
async def get_reflection_log_stats(
        # HACK（end_user_id数据类型修改）临时end_user_id为str，后续再改为uuid.UUID，可能需要修改前端代码
        end_user_id: str = Query(..., description="终端用户ID"),
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """获取反思日志统计概览（纯异步版本）"""
    # HACK（end_user_id数据类型修改）改成end_user_uuid: UUID后，数据类型校验可以移除
    try:
        end_user_uuid = uuid.UUID(end_user_id)
    except (ValueError, AttributeError):
        return fail(BizCode.INVALID_PARAMETER, "请求参数无效", "无效的终端用户ID格式")

    api_logger.info(f"用户 {current_user.username} 查询反思日志统计: end_user_id={end_user_id}")

    try:
        async with get_async_db_context() as db:
            end_user = await EndUserRepository(db).get_end_user_by_id_async(end_user_uuid)
            if not end_user:
                return fail(BizCode.USER_NOT_FOUND, f"终端用户不存在: {end_user_id}", "end_user not found")

            repo = ReflectionLogRepository(db)
            stats = await repo.get_stats_async(end_user_id)

        return success(data=stats, msg="反思日志统计获取成功")
    except Exception as e:
        api_logger.error(f"查询反思日志统计失败: end_user_id={end_user_id}, error={e}")
        return fail(BizCode.INTERNAL_ERROR, "查询统计失败", str(e))


@router.get("/reflection/logs/{log_id}")
async def get_reflection_log_detail(
        log_id: str = Path(..., description="日志ID"),
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """获取反思日志详情（异步版本）

    返回单条日志的完整信息，包含 trigger_detail、solution_detail、execution_detail。
    前端根据 sub_problem 字段条件渲染 trigger_detail 区域。
    """
    try:
        uuid.UUID(log_id)
    except (ValueError, AttributeError):
        return fail(BizCode.INVALID_PARAMETER, "请求参数无效", "无效的日志ID格式")

    api_logger.info(f"用户 {current_user.username} 查询反思日志详情: log_id={log_id}")

    try:
        async with get_async_db_context() as db:
            repo = ReflectionLogRepository(db)
            log = await repo.get_by_id_async(log_id)
            if not log:
                return fail(BizCode.NOT_FOUND, "日志不存在")
            detail = ReflectionLogDetail.model_validate(log)
        return success(data=detail.model_dump(mode="json"), msg="反思日志详情获取成功")
    except Exception as e:
        api_logger.error(f"查询反思日志详情失败: log_id={log_id}, error={e}")
        return fail(BizCode.INTERNAL_ERROR, "查询详情失败", str(e))


@router.get("/reflection/logs")
async def get_reflection_logs(
        # HACK（end_user_id数据类型修改）临时end_user_id为str，后续再改为uuid.UUID，可能需要修改前端代码
        end_user_id: str = Query(..., description="终端用户ID"),
        sub_problem: Optional[SubProblemEnum] = Query(None, description="子问题类型筛选"),
        status: Optional[LogStatusEnum] = Query(None, description="状态筛选"),
        trigger_type: Optional[TriggerTypeEnum] = Query(None, description="触发方式筛选"),
        page: int = Query(1, ge=1, description="页码，从1开始"),
        pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """获取反思日志列表（异步分页版本）

    支持按 sub_problem、status、trigger_type 筛选。
    按 created_at 倒序排列。
    """
    try:
        end_user_uuid = uuid.UUID(end_user_id)
    except (ValueError, AttributeError):
        return fail(BizCode.INVALID_PARAMETER, "请求参数无效", "无效的终端用户ID格式")

    api_logger.info(
        f"用户 {current_user.username} 查询反思日志列表: "
        f"end_user_id={end_user_id}, sub_problem={sub_problem}, "
        f"status={status}, page={page}, pagesize={pagesize}"
    )

    try:
        async with get_async_db_context() as db:
            end_user = await EndUserRepository(db).get_end_user_by_id_async(end_user_uuid)
            if end_user is None:
                return fail(BizCode.USER_NOT_FOUND, f"终端用户不存在: {end_user_id}", "end_user not found")

            repo = ReflectionLogRepository(db)
            total, items = await repo.get_paginated_async(
                end_user_id=end_user_id,
                page=page,
                pagesize=pagesize,
                sub_problem=sub_problem.value if sub_problem else None,
                status=status.value if status else None,
                trigger_type=trigger_type.value if trigger_type else None,
            )

            data_items = [
                ReflectionLogListItem.model_validate(log).model_dump(mode="json")
                for log in items
            ]

        return success(data={
            "items": data_items,
            "page": {
                "page": page,
                "pagesize": pagesize,
                "total": total,
                "hasnext": (page * pagesize) < total,
            },
        }, msg="反思日志列表获取成功")
    except Exception as e:
        api_logger.error(f"查询反思日志列表失败: end_user_id={end_user_id}, error={e}")
        return fail(BizCode.INTERNAL_ERROR, "查询日志列表失败", str(e))

# save_reflection_config 已迁移至 memory_config_controller（/memory_config/update_config_reflection）


@router.get("/reflection")
async def start_workspace_reflection(
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
) -> dict:
    """
    启动 workspace 下所有匹配应用的反思流程（异步版本）。

    对当前用户 workspace 下所有具备有效记忆配置的应用触发反思。每个用户的反思
    使用独立事务隔离，单个用户失败不影响其他用户。

    Args:
        current_user: 已认证的用户快照
    """
    workspace_id = current_user.current_workspace_id

    try:
        api_logger.info(f"用户 {current_user.username} 启动workspace反思，workspace_id: {workspace_id}")

        async with get_async_db_context() as db:
            result = await get_workspace_apps_detailed_async(db=db, workspace_id=str(workspace_id))
            reflection_results = []
            first_user_done = False

            # 遍历 workspace 下每个应用
            for data in result['apps_detailed_info']:
                if not data['memory_configs']:
                    api_logger.debug(f"应用 {data['id']} 没有memory_configs，跳过")
                    continue

                releases = data['releases']
                memory_configs = data['memory_configs']
                end_users = data['end_users']

                for config in memory_configs:
                    config_id_str = str(config['config_id'])
                    matching_releases = [r for r in releases if str(r['config']) == config_id_str]

                    if not matching_releases:
                        api_logger.debug(f"配置 {config_id_str} 没有匹配的release")
                        continue

                    for user in end_users:
                        api_logger.info(f"为用户 {user['id']} 启动反思，config_id: {config_id_str}")
                        if not first_user_done:
                            first_user_done = True
                            # 与 app list 查询复用同一 session
                            try:
                                reflection_service = MemoryReflectionService(db)
                                reflection_result = await reflection_service.start_text_reflection_async(config_data=config, end_user_id=user['id'], db=db)
                                reflection_results.append({
                                    "app_id": data['id'],
                                    "config_id": config_id_str,
                                    "end_user_id": user['id'],
                                    "reflection_result": reflection_result
                                })
                            except Exception as e:
                                api_logger.error(f"用户 {user['id']} 反思失败: {str(e)}")
                                reflection_results.append({
                                    "app_id": data['id'],
                                    "config_id": config_id_str,
                                    "end_user_id": user['id'],
                                    "reflection_result": {
                                        "status": "错误",
                                        "message": f"反思失败: {str(e)}"
                                    }
                                })
                        else:
                            async with get_async_db_context() as inner_db:
                                try:
                                    reflection_service = MemoryReflectionService(inner_db)
                                    reflection_result = await reflection_service.start_text_reflection_async(config_data=config, end_user_id=user['id'], db=inner_db)
                                    reflection_results.append({
                                        "app_id": data['id'],
                                        "config_id": config_id_str,
                                        "end_user_id": user['id'],
                                        "reflection_result": reflection_result
                                    })
                                except Exception as e:
                                    api_logger.error(f"用户 {user['id']} 反思失败: {str(e)}")
                                    reflection_results.append({
                                        "app_id": data['id'],
                                        "config_id": config_id_str,
                                        "end_user_id": user['id'],
                                        "reflection_result": {
                                            "status": "错误",
                                            "message": f"反思失败: {str(e)}"
                                        }
                                    })

        return success(data=reflection_results, msg="反思配置成功")

    except Exception as e:
        api_logger.error(f"启动workspace反思失败: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"启动workspace反思失败: {str(e)}"
        )


# start_reflection_configs 已迁移至 memory_config_controller（/memory_config/read_config_reflection）


@router.get("/reflection/run")
async def reflection_run(
        config_id: UUID | int,
        language_type: str = Header(default=None, alias="X-Language-Type"),
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
) -> dict:
    """
    使用指定配置执行反思引擎（异步版本）。

    Args:
        config_id: 反思配置ID
        language_type: 语言偏好 header
        current_user: 已认证用户快照
    """
    # 统一的语言解析
    language = get_language_from_header(language_type) # 语言偏好可以删除吗？
    api_logger.info(f"用户 {current_user.username} 查询反思配置，config_id: {config_id}")

    async with get_async_db_context() as db:
        resolved_config_id = await resolve_config_id_async(config_id, db)

        # 通过异步 Repository 查询反思配置
        try:
            result = await MemoryConfigRepository(db).query_reflection_config_by_id_async(resolved_config_id)
        except RuntimeError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"未找到config_id为 {resolved_config_id} 的配置"
            )

        api_logger.info(f"成功查询反思配置，config_id: {resolved_config_id}")

        # 校验模型 ID 并构建 RedBearLLM 实例
        model_id = result.reflection_model_id
        llm_client = None
        if model_id:
            try:
                await ModelConfigService.get_model_by_id_async(
                    db=db,
                    model_id=uuid.UUID(model_id),
                    tenant_id=current_user.tenant_id,
                )
                api_logger.info(f"模型ID验证成功: {model_id}")
                # 构建 RedBearLLM 实例（ReflectionEngine 不再接受 model_id 字符串）
                api_config = await ModelApiKeyService.get_available_api_key_async(
                    db,
                    uuid.UUID(model_id),
                    tenant_id=current_user.tenant_id,
                )
                if api_config:
                    llm_client = RedBearLLM(
                        RedBearModelConfig(
                            model_name=api_config.model_name,
                            provider=api_config.provider,
                            capability=api_config.capability,
                            api_key=api_config.api_key,
                            base_url=api_config.api_base,
                            is_omni=api_config.is_omni,
                        )
                    )
            except Exception as e:
                api_logger.warning(f"模型ID '{model_id}' 不存在，将使用默认模型: {str(e)}")
                model_id = None

        # 构建反思配置
        config = ReflectionConfig(
            enabled=result.enable_self_reflexion,
            iteration_period=result.iteration_period,
            reflexion_range=ReflectionRange(result.reflexion_range),
            baseline=ReflectionBaseline(result.baseline),
            output_example='',
            memory_verify=result.memory_verify,
            quality_assessment=result.quality_assessment,
            violation_handling_strategy="block",
            model_id=model_id,
            language_type=language_type,
        )

    # 反思执行本身使用 Neo4j 异步连接器，无 PG DB 依赖
    connector = Neo4jConnector()
    engine = ReflectionEngine(
        config=config,
        neo4j_connector=connector,
        llm_client=llm_client,
        tenant_id=current_user.tenant_id,
    )

    run_result = await engine.reflection_run()
    return success(data=run_result, msg="反思试运行")
