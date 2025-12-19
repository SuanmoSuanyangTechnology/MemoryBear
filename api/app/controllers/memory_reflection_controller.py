import asyncio

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.logging_config import get_api_logger
from app.core.memory.storage_services.reflection_engine.self_reflexion import ReflectionConfig, ReflectionEngine
from app.dependencies import get_current_user
from app.db import get_db
from app.models.user_model import User
from app.repositories.data_config_repository import DataConfigRepository
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

from app.services.memory_reflection_service import WorkspaceAppService, MemoryReflectionService

from app.schemas.memory_reflection_schemas import Memory_Reflection

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
        if not config_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="缺少必需参数: config_id"
            )

        api_logger.info(f"用户 {current_user.username} 保存反思配置，config_id: {config_id}")

        update_params = {
            "enable_self_reflexion": request.reflectionenabled,
            "iteration_period": request.reflection_period_in_hours,
            "reflexion_range": request.reflexion_range,
            "baseline": request.baseline,
            "reflection_model_id": request.reflection_model_id,
            "memory_verify": request.memory_verify,
            "quality_assessment": request.quality_assessment,
        }



        query, params = DataConfigRepository.build_update_reflection(config_id, **update_params)

        result = db.execute(text(query), params)
        if result.rowcount == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"未找到config_id为 {config_id} 的配置"
            )
        
        db.commit()
        
        # 查询更新后的配置
        select_query, select_params = DataConfigRepository.build_select_reflection(config_id)
        result = db.execute(text(select_query), select_params).fetchone()
        
        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"更新后未找到config_id为 {config_id} 的配置"
            )
        
        api_logger.info(f"成功保存反思配置到数据库，config_id: {config_id}")
        
        # 返回结果
        return {
            "status": "成功",
            "message": "反思配置已保存",
            "config_id": config_id,
            "database_record": {
                "config_id": result.config_id,
                "enable_self_reflexion": result.enable_self_reflexion,
                "iteration_period": result.iteration_period,
                "reflexion_range": result.reflexion_range,
                "baseline": result.baseline,
                "reflection_model_id": result.reflection_model_id,
                "memory_verify": result.memory_verify,
                "quality_assessment": result.quality_assessment,
                "user_id": result.user_id
            }
        }
        
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


@router.post("/reflection")
async def start_workspace_reflection(
    request: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Activate the reflection function for all matching applications in the workspace"""
    workspace_id = current_user.current_workspace_id
    reflection_service = MemoryReflectionService(db)

    try:
        api_logger.info(f"用户 {current_user.username} 启动workspace反思，workspace_id: {workspace_id}")

        service = WorkspaceAppService(db)
        result = service.get_workspace_apps_detailed(workspace_id)
        
        reflection_results = []
        
        for data in result['apps_detailed_info']:
            if data['data_configs'] == []: 
                continue
                
            releases = data['releases']
            data_configs = data['data_configs']
            end_users = data['end_users']
            
            for base, config, user in zip(releases, data_configs, end_users):
                if int(base['config']) == int(config['config_id']) and base['app_id'] == user['app_id']:
                    # 调用反思服务
                    api_logger.info(f"为用户 {user['id']} 启动反思，config_id: {config['config_id']}")
                    
                    reflection_result = await reflection_service.start_reflection_from_data(
                        config_data=config,
                        end_user_id=user['id']
                    )
                    
                    reflection_results.append({
                        "app_id": base['app_id'],
                        "config_id": config['config_id'],
                        "end_user_id": user['id'],
                        "reflection_result": reflection_result
                    })

        return {
            "status": "完成",
            "message": f"成功处理 {len(reflection_results)} 个反思任务",
            "workspace_id": str(workspace_id),
            "reflection_count": len(reflection_results),
            "reflection_results": reflection_results
        }

    except Exception as e:
        api_logger.error(f"启动workspace反思失败: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"启动workspace反思失败: {str(e)}"
        )

@router.post("/reflection/run")
async def reflection_run(
    reflection: Memory_Reflection,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Activate the reflection function for all matching applications in the workspace"""
    config = ReflectionConfig(
        enabled=reflection.reflectionenabled,
        iteration_period=reflection.reflection_period_in_hours,
        reflexion_range=reflection.reflexion_range,
        baseline=reflection.baseline,
        output_example='',
        memory_verify=reflection.memory_verify,
        quality_assessment=reflection.quality_assessment,
        violation_handling_strategy="block",
        model_id=reflection.reflection_model_id
    )
    connector = Neo4jConnector()
    engine = ReflectionEngine(
        config=config,
        neo4j_connector=connector,
        llm_client=reflection.reflection_model_id  # 传入 model_id
    )

    result=await (engine.reflection_run())
    return result
