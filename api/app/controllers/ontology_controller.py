"""本体提取API控制器

本模块提供本体提取系统的RESTful API端点。

Endpoints:
    POST /api/ontology/extract - 提取本体类
    POST /api/ontology/export - 导出OWL文件
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
    ExportRequest,
    ExportResponse,
    ExtractionRequest,
    ExtractionResponse,
)
from app.schemas.response_schema import ApiResponse
from app.services.ontology_service import OntologyService
from app.core.memory.llm_tools.openai_client import OpenAIClient
from app.core.memory.utils.validation.owl_validator import OWLValidator
from app.services.model_service import ModelConfigService


api_logger = get_api_logger()
logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/memory/ontology",
    tags=["Ontology"],
)


def _get_ontology_service(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    llm_id: str = None
) -> OntologyService:
    """获取OntologyService实例的依赖注入函数
    
    从当前工作空间或指定的llm_id获取LLM配置,创建OpenAIClient和OntologyService实例。
    
    Args:
        db: 数据库会话
        current_user: 当前用户
        llm_id: 可选的LLM模型ID,如果提供则使用指定模型,否则使用工作空间默认模型
        
    Returns:
        OntologyService: 本体提取服务实例
        
    Raises:
        HTTPException: 如果无法获取LLM配置
    """
    try:
        import uuid
        
        # 必须提供llm_id
        if not llm_id:
            logger.error(f"llm_id is required but not provided - user: {current_user.id}")
            raise HTTPException(
                status_code=400,
                detail="必须提供llm_id参数"
            )
        
        logger.info(f"Using specified LLM model: {llm_id}")
        
        # 验证llm_id格式
        try:
            model_id = uuid.UUID(llm_id)
        except ValueError:
            logger.error(f"Invalid llm_id format: {llm_id}")
            raise HTTPException(
                status_code=400,
                detail="无效的LLM模型ID格式"
            )
        
        # 获取指定的模型配置
        try:
            model_config = ModelConfigService.get_model_by_id(db=db, model_id=model_id)
        except Exception as e:
            logger.error(f"Model {llm_id} not found: {str(e)}")
            raise HTTPException(
                status_code=400,
                detail=f"找不到指定的LLM模型: {llm_id}"
            )
        
        # 验证模型配置了API密钥
        if not model_config.api_keys:
            logger.error(f"Model {llm_id} has no API key configuration")
            raise HTTPException(
                status_code=400,
                detail="指定的LLM模型没有配置API密钥"
            )
        
        api_key_config = model_config.api_keys[0]
        
        logger.info(
            f"Using specified model - user: {current_user.id}, "
            f"model_id: {llm_id}, model_name: {api_key_config.model_name}"
        )
        
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
            f"OntologyService created successfully - "
            f"user: {current_user.id}, model: {api_key_config.model_name}"
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """提取本体类
    
    从场景描述中提取符合OWL规范的本体类。
    
    Args:
        request: 提取请求,包含scenario、domain和可选的llm_id
        db: 数据库会话
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
                "extracted_count": 7
            }
        }
    """
    api_logger.info(
        f"Ontology extraction requested by user {current_user.id}, "
        f"scenario_length={len(request.scenario)}, "
        f"domain={request.domain}, "
        f"llm_id={request.llm_id}"
    )
    
    try:
        # 创建OntologyService实例,传入llm_id
        service = _get_ontology_service(
            db=db,
            current_user=current_user,
            llm_id=request.llm_id
        )
        
        # 调用服务层执行提取
        result = await service.extract_ontology(
            scenario=request.scenario,
            domain=request.domain
        )
        
        # 构建响应
        response = ExtractionResponse(
            classes=result.classes,
            domain=result.domain,
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """导出OWL文件
    
    将提取的本体类导出为OWL文件,支持多种格式。
    导出操作不需要LLM,只使用OWL验证器和Owlready2库。
    
    Args:
        request: 导出请求,包含classes、format和include_metadata
        db: 数据库会话
        current_user: 当前用户
        
    Returns:
        ApiResponse: 包含OWL文件内容的响应
        
    Supported formats:
        - rdfxml: 标准OWL RDF/XML格式(完整)
        - turtle: Turtle格式(可读性好)
        - ntriples: N-Triples格式(简单)
        - json: JSON格式(简化,只包含类信息)
        
    Response format:
        {
            "code": 200,
            "msg": "OWL文件导出成功",
            "data": {
                "owl_content": "...",
                "format": "rdfxml",
                "classes_count": 7
            }
        }
    """
    api_logger.info(
        f"OWL export requested by user {current_user.id}, "
        f"classes_count={len(request.classes)}, "
        f"format={request.format}, "
        f"include_metadata={request.include_metadata}"
    )
    
    try:
        # 验证格式
        valid_formats = ["rdfxml", "turtle", "ntriples", "json"]
        if request.format not in valid_formats:
            api_logger.warning(f"Invalid export format: {request.format}")
            return fail(
                BizCode.BAD_REQUEST,
                "不支持的导出格式",
                f"format必须是以下之一: {', '.join(valid_formats)}"
            )
        
        # JSON格式直接导出,不需要OWL验证
        if request.format == "json":
            owl_validator = OWLValidator()
            owl_content = owl_validator.export_to_owl(
                world=None,
                format="json",
                classes=request.classes
            )
            
            response = ExportResponse(
                owl_content=owl_content,
                format=request.format,
                classes_count=len(request.classes)
            )
            
            api_logger.info(
                f"JSON export completed, content_length={len(owl_content)}"
            )
            
            return success(data=response.model_dump(), msg="OWL文件导出成功")
        
        # 创建临时文件路径
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.owl',
            delete=False
        ) as tmp_file:
            output_path = tmp_file.name
        
        # 导出操作不需要LLM,直接使用OWL验证器
        owl_validator = OWLValidator()
        
        # 验证本体类
        logger.debug("Validating ontology classes")
        is_valid, errors, world = owl_validator.validate_ontology_classes(
            classes=request.classes,
        )
        
        if not is_valid:
            logger.warning(
                f"OWL validation found {len(errors)} issues during export: {errors}"
            )
            # 继续导出,但记录警告
        
        if not world:
            error_msg = "Failed to create OWL world for export"
            logger.error(error_msg)
            return fail(BizCode.INTERNAL_ERROR, "创建OWL世界失败", error_msg)
        
        # 导出OWL文件
        logger.info(f"Exporting to {request.format} format")
        owl_content = owl_validator.export_to_owl(
            world=world,
            output_path=output_path,
            format=request.format,
            classes=request.classes
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
