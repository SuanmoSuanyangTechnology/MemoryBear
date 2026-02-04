import asyncio
import time
import uuid
from uuid import UUID

from app.core.language_utils import get_language_from_header
from app.core.logging_config import get_api_logger
from app.core.memory.storage_services.reflection_engine.self_reflexion import (
    ReflectionConfig,
    ReflectionEngine, ReflectionRange, ReflectionBaseline,
)
from app.core.response_utils import success
from app.db import get_db
from app.dependencies import get_current_user
from app.models.user_model import User
from app.repositories.memory_config_repository import MemoryConfigRepository
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.schemas.memory_reflection_schemas import Memory_Reflection
from app.services.memory_reflection_service import (
    MemoryReflectionService,
    WorkspaceAppService,
)
from app.services.model_service import ModelConfigService
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, status,Header
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.utils.config_utils import resolve_config_id

load_dotenv()
api_logger = get_api_logger()

router = APIRouter(
    prefix="/memory",
    tags=["Memory"],
)


@router.post("/reflection/save")
async def save_reflection_config(
    request: Memory_Reflection,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Save reflection configuration to data_comfig table"""
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

        reflection_result={
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


@router.get("/reflection")
async def start_workspace_reflection(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """启动工作空间中所有匹配应用的反思功能"""
    workspace_id = current_user.current_workspace_id
    reflection_service = MemoryReflectionService(db)

    try:
        api_logger.info(f"用户 {current_user.username} 启动workspace反思，workspace_id: {workspace_id}")

        service = WorkspaceAppService(db)
        result = service.get_workspace_apps_detailed(workspace_id)
        reflection_results = []
        for data in result['apps_detailed_info']:
            # 跳过没有配置的应用
            if not data['memory_configs']:
                api_logger.debug(f"应用 {data['id']} 没有memory_configs，跳过")
                continue

            releases = data['releases']
            memory_configs = data['memory_configs']
            end_users = data['end_users']

            # 为每个配置和用户组合执行反思
            for config in memory_configs:
                config_id_str = str(config['config_id'])

                # 找到匹配此配置的所有release
                matching_releases = [r for r in releases if str(r['config']) == config_id_str]

                if not matching_releases:
                    api_logger.debug(f"配置 {config_id_str} 没有匹配的release")
                    continue

                # 为每个用户执行反思
                for user in end_users:
                    api_logger.info(f"为用户 {user['id']} 启动反思，config_id: {config_id_str}")

                    try:
                        reflection_result = await reflection_service.start_text_reflection(
                            config_data=config,
                            end_user_id=user['id']
                        )

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


@router.get("/reflection/configs")
async def start_reflection_configs(
        config_id: uuid.UUID|int,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
) -> dict:
    """通过config_id查询memory_config表中的反思配置信息"""
    config_id = resolve_config_id(config_id, db)
    try:
        config_id=resolve_config_id(config_id,db)
        api_logger.info(f"用户 {current_user.username} 查询反思配置，config_id: {config_id}")
        result = MemoryConfigRepository.query_reflection_config_by_id(db, config_id)
        memory_config_id = resolve_config_id(result.config_id, db)
        # 构建返回数据
        reflection_config = {
            "config_id": memory_config_id,
            "reflection_enabled": result.enable_self_reflexion,
            "reflection_period_in_hours": result.iteration_period,
            "reflexion_range": result.reflexion_range,
            "baseline": result.baseline,
            "reflection_model_id": result.reflection_model_id,
            "memory_verify": result.memory_verify,
            "quality_assessment": result.quality_assessment
        }
        api_logger.info(f"成功查询反思配置，config_id: {config_id}")
        return success(data=reflection_config, msg="反思配置查询成功")
        

    except HTTPException:
        # 重新抛出HTTP异常
        raise
    except Exception as e:
        api_logger.error(f"查询反思配置失败: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询反思配置失败: {str(e)}"
        )

@router.get("/reflection/run")
async def reflection_run(
    config_id: UUID|int,
    language_type: str = Header(default=None, alias="X-Language-Type"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Activate the reflection function for all matching applications in the workspace"""
    # 使用集中化的语言校验
    language = get_language_from_header(language_type)

    api_logger.info(f"用户 {current_user.username} 查询反思配置，config_id: {config_id}")
    config_id = resolve_config_id(config_id, db)
    # 使用MemoryConfigRepository查询反思配置
    result = MemoryConfigRepository.query_reflection_config_by_id(db, config_id)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"未找到config_id为 {config_id} 的配置"
        )

    api_logger.info(f"成功查询反思配置，config_id: {config_id}")

    # 验证模型ID是否存在
    model_id = result.reflection_model_id
    if model_id:
        try:
            ModelConfigService.get_model_by_id(db=db, model_id=uuid.UUID(model_id))
            api_logger.info(f"模型ID验证成功: {model_id}")
        except Exception as e:
            api_logger.warning(f"模型ID '{model_id}' 不存在，将使用默认模型: {str(e)}")
            # 可以设置为None，让反思引擎使用默认模型
            model_id = None

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
        language_type=language_type
    )
    connector = Neo4jConnector()
    engine = ReflectionEngine(
        config=config,
        neo4j_connector=connector,
        llm_client=model_id  # 传入验证后的 model_id
    )

    result=await (engine.reflection_run())
    return success(data=result, msg="反思试运行")




