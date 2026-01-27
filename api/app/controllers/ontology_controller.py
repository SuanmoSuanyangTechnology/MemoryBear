"""本体提取API控制器

本模块提供本体提取系统的RESTful API端点。

Endpoints:
    POST /api/ontology/extract - 提取本体类
    POST /api/ontology/export - 导出OWL文件
    GET /api/ontology/config/{config_name} - 获取配置
    PUT /api/ontology/config/{config_name} - 更新配置
"""

import logging
import tempfile
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.logging_config import get_api_logger
from app.core.response_utils import fail, success
from app.db import get_db
from app.dependencies import get_current_user
from app.models.user_model import User
from app.schemas.ontology_schemas import (
    ConfigResponse,
    ConfigUpdateRequest,
    ExportRequest,
    ExportResponse,
    ExtractionRequest,
    ExtractionResponse,
)
from app.schemas.response_schema import ApiResponse
from app.services.ontology_service import OntologyService
from app.core.memory.llm_tools.openai_client import OpenAIClient
from app.services.model_service import ModelConfigService


api_logger = get_api_logger()
logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/ontology",
    tags=["Ontology"],
)


def _get_ontology_service(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> OntologyService:
    """获取OntologyService实例的依赖注入函数
    
    从当前工作空间获取LLM配置,创建OpenAIClient和OntologyService实例。
    
    Args:
        db: 数据库会话
        current_user: 当前用户
        
    Returns:
        OntologyService: 本体提取服务实例
        
    Raises:
        HTTPException: 如果无法获取LLM配置
    """
    try:
        workspace_id = current_user.current_workspace_id
        
        if not workspace_id:
            logger.error(f"User {current_user.id} has no current workspace")
            raise HTTPException(
                status_code=400,
                detail="当前用户没有活动的工作空间"
            )
        
        # 获取工作空间的LLM配置
        from app.repositories import WorkspaceRepository
        workspace_repo = WorkspaceRepository(db)
        workspace_models = workspace_repo.get_workspace_models_configs(workspace_id)
        
        if not workspace_models or not workspace_models.get("llm"):
            logger.error(f"Workspace {workspace_id} has no LLM configuration")
            raise HTTPException(
                status_code=400,
                detail="当前工作空间没有配置LLM模型"
            )
        
        model_id = workspace_models.get("llm")
        
        # 获取模型配置
        model_config = ModelConfigService.get_model_by_id(db=db, model_id=model_id)
        
        if not model_config or not model_config.api_keys:
            logger.error(f"Model {model_id} has no API key configuration")
            raise HTTPException(
                status_code=400,
                detail="LLM模型没有配置API密钥"
            )
        
        api_key_config = model_config.api_keys[0]
        
        # 创建模型配置对象
        from app.core.models.base import RedBearModelConfig
        
        llm_model_config = RedBearModelConfig(
            model_name=api_key_config.model_name,
            provider=model_config.provider if hasattr(model_config, 'provider') else "openai",
            api_key=api_key_config.api_key,
            base_url=api_key_config.api_base,
            max_retries=3,
            timeout=60.0
        )
        
        # 创建OpenAI客户端
        llm_client = OpenAIClient(model_config=llm_model_config)
        
        # 创建OntologyService
        service = OntologyService(llm_client=llm_client, db=db)
        
        logger.debug(
            f"OntologyService created for user {current_user.id}, "
            f"workspace {workspace_id}, model {model_id}"
        )
        
        return service
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create OntologyService: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"创建本体提取服务失败: {str(e)}"
        )


@router.post("/extract", response_model=ApiResponse)
async def extract_ontology(
    request: ExtractionRequest,
    service: OntologyService = Depends(_get_ontology_service),
    current_user: User = Depends(get_current_user)
):
    """提取本体类
    
    从场景描述中提取符合OWL规范的本体类。
    
    Args:
        request: 提取请求,包含scenario、domain和config_name
        service: 本体提取服务实例
        current_user: 当前用户
        
    Returns:
        ApiResponse: 包含提取结果的响应
        
    Response format:
        {
            "code": 200,
            "msg": "本体提取成功",
            "data": {
                "classes": [...],
                "domain": "Healthcare",
                "namespace": "http://example.org/ontology#",
                "extracted_count": 7
            }
        }
    """
    api_logger.info(
        f"Ontology extraction requested by user {current_user.id}, "
        f"scenario_length={len(request.scenario)}, "
        f"domain={request.domain}, "
        f"config_name={request.config_name}"
    )
    
    try:
        # 调用服务层执行提取
        result = await service.extract_ontology(
            scenario=request.scenario,
            domain=request.domain,
            config_name=request.config_name
        )
        
        # 构建响应
        response = ExtractionResponse(
            classes=result.classes,
            domain=result.domain,
            namespace=result.namespace,
            extracted_count=len(result.classes)
        )
        
        api_logger.info(
            f"Ontology extraction completed, extracted {len(result.classes)} classes"
        )
        
        return success(data=response.model_dump(), msg="本体提取成功")
        
    except ValueError as e:
        # 验证错误 (400)
        api_logger.warning(f"Validation error in extraction: {str(e)}")
        return fail(BizCode.BAD_REQUEST, "请求参数无效", str(e))
        
    except RuntimeError as e:
        # 运行时错误 (500)
        api_logger.error(f"Runtime error in extraction: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "本体提取失败", str(e))
        
    except Exception as e:
        # 未知错误 (500)
        api_logger.error(f"Unexpected error in extraction: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "本体提取失败", str(e))


@router.post("/export", response_model=ApiResponse)
async def export_owl(
    request: ExportRequest,
    service: OntologyService = Depends(_get_ontology_service),
    current_user: User = Depends(get_current_user)
):
    """导出OWL文件
    
    将提取的本体类导出为OWL文件,支持多种格式。
    
    Args:
        request: 导出请求,包含classes、format和namespace
        service: 本体提取服务实例
        current_user: 当前用户
        
    Returns:
        ApiResponse: 包含OWL文件内容的响应
        
    Response format:
        {
            "code": 200,
            "msg": "OWL文件导出成功",
            "data": {
                "owl_content": "<?xml version='1.0'?>...",
                "format": "rdfxml",
                "classes_count": 7
            }
        }
    """
    api_logger.info(
        f"OWL export requested by user {current_user.id}, "
        f"classes_count={len(request.classes)}, "
        f"format={request.format}"
    )
    
    try:
        # 验证格式
        valid_formats = ["rdfxml", "turtle", "ntriples"]
        if request.format not in valid_formats:
            api_logger.warning(f"Invalid export format: {request.format}")
            return fail(
                BizCode.BAD_REQUEST,
                "不支持的导出格式",
                f"format必须是以下之一: {', '.join(valid_formats)}"
            )
        
        # 创建临时文件路径
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.owl',
            delete=False
        ) as tmp_file:
            output_path = tmp_file.name
        
        # 调用服务层执行导出
        owl_content = await service.export_owl_file(
            classes=request.classes,
            output_path=output_path,
            format=request.format,
            namespace=request.namespace
        )
        
        # 构建响应
        response = ExportResponse(
            owl_content=owl_content,
            format=request.format,
            classes_count=len(request.classes)
        )
        
        api_logger.info(
            f"OWL export completed, format={request.format}, "
            f"content_length={len(owl_content)}"
        )
        
        return success(data=response.model_dump(), msg="OWL文件导出成功")
        
    except ValueError as e:
        # 验证错误 (400)
        api_logger.warning(f"Validation error in export: {str(e)}")
        return fail(BizCode.BAD_REQUEST, "请求参数无效", str(e))
        
    except RuntimeError as e:
        # 运行时错误 (500)
        api_logger.error(f"Runtime error in export: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "OWL文件导出失败", str(e))
        
    except Exception as e:
        # 未知错误 (500)
        api_logger.error(f"Unexpected error in export: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "OWL文件导出失败", str(e))


@router.get("/config/{config_name}", response_model=ApiResponse)
async def get_config(
    config_name: str,
    service: OntologyService = Depends(_get_ontology_service),
    current_user: User = Depends(get_current_user)
):
    """获取配置
    
    获取指定名称的本体提取配置参数。
    
    Args:
        config_name: 配置名称
        service: 本体提取服务实例
        current_user: 当前用户
        
    Returns:
        ApiResponse: 包含配置参数的响应
        
    Response format:
        {
            "code": 200,
            "msg": "获取配置成功",
            "data": {
                "config_name": "default",
                "max_classes": 15,
                "min_classes": 5,
                ...
            }
        }
    """
    api_logger.info(
        f"Config retrieval requested by user {current_user.id}, "
        f"config_name={config_name}"
    )
    
    try:
        # 调用服务层获取配置
        config = service.get_config(config_name)
        
        # 构建响应
        response = ConfigResponse(**config)
        
        api_logger.info(f"Config retrieved successfully: {config_name}")
        
        return success(data=response.model_dump(), msg="获取配置成功")
        
    except ValueError as e:
        # 配置不存在 (404)
        api_logger.warning(f"Config not found: {config_name}")
        return fail(BizCode.NOT_FOUND, "配置不存在", str(e))
        
    except RuntimeError as e:
        # 运行时错误 (500)
        api_logger.error(f"Runtime error in get_config: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "获取配置失败", str(e))
        
    except Exception as e:
        # 未知错误 (500)
        api_logger.error(f"Unexpected error in get_config: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "获取配置失败", str(e))


@router.put("/config/{config_name}", response_model=ApiResponse)
async def update_config(
    config_name: str,
    request: ConfigUpdateRequest,
    service: OntologyService = Depends(_get_ontology_service),
    current_user: User = Depends(get_current_user)
):
    """更新配置
    
    更新指定配置的参数。只更新请求中提供的字段。
    
    Args:
        config_name: 配置名称
        request: 配置更新请求,包含要更新的字段
        service: 本体提取服务实例
        current_user: 当前用户
        
    Returns:
        ApiResponse: 包含更新后配置的响应
        
    Response format:
        {
            "code": 200,
            "msg": "更新配置成功",
            "data": {
                "config_name": "default",
                "max_classes": 20,
                ...
            }
        }
    """
    api_logger.info(
        f"Config update requested by user {current_user.id}, "
        f"config_name={config_name}"
    )
    
    try:
        # 构建更新字典(只包含非None的字段)
        updates: Dict = {}
        if request.max_classes is not None:
            updates["max_classes"] = request.max_classes
        if request.min_classes is not None:
            updates["min_classes"] = request.min_classes
        if request.max_description_length is not None:
            updates["max_description_length"] = request.max_description_length
        if request.llm_temperature is not None:
            updates["llm_temperature"] = request.llm_temperature
        if request.llm_max_tokens is not None:
            updates["llm_max_tokens"] = request.llm_max_tokens
        if request.enable_owl_validation is not None:
            updates["enable_owl_validation"] = request.enable_owl_validation
        
        if not updates:
            api_logger.warning("No fields to update")
            return fail(BizCode.BAD_REQUEST, "没有提供要更新的字段", "")
        
        api_logger.debug(f"Updating fields: {list(updates.keys())}")
        
        # 调用服务层更新配置
        updated_config = service.update_config(config_name, updates)
        
        # 构建响应
        response = ConfigResponse(**updated_config)
        
        api_logger.info(f"Config updated successfully: {config_name}")
        
        return success(data=response.model_dump(), msg="更新配置成功")
        
    except ValueError as e:
        # 配置不存在或验证错误 (404/400)
        if "not found" in str(e).lower():
            api_logger.warning(f"Config not found: {config_name}")
            return fail(BizCode.NOT_FOUND, "配置不存在", str(e))
        else:
            api_logger.warning(f"Validation error in update_config: {str(e)}")
            return fail(BizCode.BAD_REQUEST, "请求参数无效", str(e))
        
    except RuntimeError as e:
        # 运行时错误 (500)
        api_logger.error(f"Runtime error in update_config: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "更新配置失败", str(e))
        
    except Exception as e:
        # 未知错误 (500)
        api_logger.error(f"Unexpected error in update_config: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "更新配置失败", str(e))
