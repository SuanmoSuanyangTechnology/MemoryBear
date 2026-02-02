"""本体提取API控制器

本模块提供本体提取系统的RESTful API端点。

Endpoints:
    POST /api/memory/ontology/extract - 提取本体类
    POST /api/memory/ontology/export - 按场景导出OWL文件
    POST /api/memory/ontology/import - 导入OWL文件到指定场景
    POST /api/memory/ontology/scene - 创建本体场景
    PUT /api/memory/ontology/scene/{scene_id} - 更新本体场景
    DELETE /api/memory/ontology/scene/{scene_id} - 删除本体场景
    GET /api/memory/ontology/scene/{scene_id} - 获取单个场景
    GET /api/memory/ontology/scenes - 获取场景列表
    POST /api/memory/ontology/class - 创建本体类型（支持批量）
    PUT /api/memory/ontology/class/{class_id} - 更新本体类型
    DELETE /api/memory/ontology/class/{class_id} - 删除本体类型
    GET /api/memory/ontology/class/{class_id} - 获取单个类型
    GET /api/memory/ontology/classes - 获取类型列表
"""

import logging
import tempfile
import io
from typing import Dict, Optional, List
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Form
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.language_utils import get_language_from_header
from app.core.logging_config import get_api_logger
from app.core.response_utils import fail, success
from app.db import get_db
from app.dependencies import get_current_user
from app.models.user_model import User
from app.core.memory.models.ontology_models import OntologyClass
from app.schemas.ontology_schemas import (
    ExportBySceneRequest,
    ExportBySceneResponse,
    ExtractionRequest,
    ExtractionResponse,
    SceneCreateRequest,
    SceneUpdateRequest,
    SceneResponse,
    SceneListResponse,
    ClassCreateRequest,
    ClassUpdateRequest,
    ClassResponse,
    ClassListResponse,
    ImportOwlResponse,
)
from app.schemas.response_schema import ApiResponse
from app.services.ontology_service import OntologyService
from app.core.memory.llm_tools.openai_client import OpenAIClient
from app.core.memory.utils.validation.owl_validator import OWLValidator
from app.services.model_service import ModelConfigService
from app.repositories.ontology_scene_repository import OntologySceneRepository


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
    
    指定的llm_id获取LLM配置,创建OpenAIClient和OntologyService实例。
    
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
        
        # 通过 Repository 获取可用的 API Key（负载均衡逻辑由 Repository 处理）
        from app.repositories.model_repository import ModelApiKeyRepository
        api_keys = ModelApiKeyRepository.get_by_model_config(db, model_config.id)
        if not api_keys:
            logger.error(f"Model {llm_id} has no active API key")
            raise HTTPException(
                status_code=400,
                detail="指定的LLM模型没有可用的API密钥"
            )
        api_key_config = api_keys[0]
        
        is_composite = getattr(model_config, 'is_composite', False)
        logger.info(
            f"Using specified model - user: {current_user.id}, "
            f"model_id: {llm_id}, model_name: {api_key_config.model_name}, "
            f"is_composite: {is_composite}, api_key_id: {api_key_config.id}"
        )
        
        # 创建模型配置对象
        from app.core.models.base import RedBearModelConfig
        
        # 对于组合模型，使用 API Key 的 provider；否则使用 model_config 的 provider
        actual_provider = api_key_config.provider if is_composite else (
            getattr(model_config, 'provider', None) or "openai"
        )
        
        llm_model_config = RedBearModelConfig(
            model_name=api_key_config.model_name,
            provider=actual_provider,
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
    language_type: str = Header(default=None, alias="X-Language-Type"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """提取本体类
    
    从场景描述中提取符合OWL规范的本体类。
    提取结果仅返回给前端，不会自动保存到数据库。
    前端可以从返回结果中选择需要的类型，然后调用 /class 接口创建类型。
    输出语言由环境变量 DEFAULT_LANGUAGE 控制（"zh" 或 "en"）。
    
    Args:
        request: 提取请求,包含scenario、domain、llm_id和scene_id
        db: 数据库会话
        current_user: 当前用户
        
    """
    api_logger.info(
        f"Ontology extraction requested by user {current_user.id}, "
        f"scenario_length={len(request.scenario)}, "
        f"domain={request.domain}, "
        f"llm_id={request.llm_id}, "
        f"scene_id={request.scene_id}, "
        f"language={settings.DEFAULT_LANGUAGE}"
    )
    
    try:
        # 使用集中化的语言校验
        language = get_language_from_header(language_type)
        
        # 获取当前工作空间ID
        workspace_id = current_user.current_workspace_id
        if not workspace_id:
            api_logger.warning(f"User {current_user.id} has no current workspace")
            return fail(BizCode.BAD_REQUEST, "请求参数无效", "当前用户没有工作空间")
        
        # 创建OntologyService实例,传入llm_id
        service = _get_ontology_service(
            db=db,
            current_user=current_user,
            llm_id=request.llm_id
        )
        
        # 调用服务层执行提取，传入scene_id和workspace_id
        # 语言由环境变量 DEFAULT_LANGUAGE 控制，在 OntologyService 中读取
        result = await service.extract_ontology(
            scenario=request.scenario,
            domain=request.domain,
            scene_id=request.scene_id,
            workspace_id=workspace_id,
            language=language
        )
        
        # 构建响应（语言已在提取时通过模板控制，无需二次翻译）
        response = ExtractionResponse(
            classes=result.classes,
            domain=result.domain,
            extracted_count=len(result.classes)
        )
        
        api_logger.info(
            f"Ontology extraction completed, extracted {len(result.classes)} classes, "
            f"scene_id={request.scene_id}, language={settings.DEFAULT_LANGUAGE}"
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




# ==================== 本体场景管理接口 ====================

@router.post("/scene", response_model=ApiResponse)
async def create_scene(
    request: SceneCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """创建本体场景
    
    在当前工作空间下创建新的本体场景。
    
    Args:
        request: 场景创建请求
        db: 数据库会话
        current_user: 当前用户
        
    Returns:
        ApiResponse: 包含创建的场景信息
    """
    api_logger.info(
        f"Scene creation requested by user {current_user.id}, "
        f"name={request.scene_name}"
    )
    
    try:
        # 获取当前工作空间ID
        workspace_id = current_user.current_workspace_id
        if not workspace_id:
            api_logger.warning(f"User {current_user.id} has no current workspace")
            return fail(BizCode.BAD_REQUEST, "请求参数无效", "当前用户没有工作空间")
        
        # 创建OntologyService实例（不需要LLM）
        from app.core.memory.llm_tools.openai_client import OpenAIClient
        from app.core.models.base import RedBearModelConfig
        
        # 创建一个空的LLM配置（场景管理不需要LLM）
        dummy_config = RedBearModelConfig(
            model_name="dummy",
            provider="openai",
            api_key="dummy",
            base_url="https://api.openai.com/v1"
        )
        llm_client = OpenAIClient(model_config=dummy_config)
        service = OntologyService(llm_client=llm_client, db=db)
        
        # 调用服务层创建场景
        scene = service.create_scene(
            scene_name=request.scene_name,
            scene_description=request.scene_description,
            workspace_id=workspace_id
        )
        
        # 构建响应
        # 动态计算 type_num
        type_num = len(scene.classes) if scene.classes else 0
        
        response = SceneResponse(
            scene_id=scene.scene_id,
            scene_name=scene.scene_name,
            scene_description=scene.scene_description,
            type_num=type_num,
            workspace_id=scene.workspace_id,
            created_at=scene.created_at,
            updated_at=scene.updated_at,
            classes_count=type_num
        )
        
        api_logger.info(f"Scene created successfully: {scene.scene_id}")
        
        return success(data=response.model_dump(), msg="场景创建成功")
        
    except ValueError as e:
        api_logger.warning(f"Validation error in scene creation: {str(e)}")
        return fail(BizCode.BAD_REQUEST, "请求参数无效", str(e))
        
    except RuntimeError as e:
        api_logger.error(f"Runtime error in scene creation: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "场景创建失败", str(e))
        
    except Exception as e:
        api_logger.error(f"Unexpected error in scene creation: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "场景创建失败", str(e))


@router.put("/scene/{scene_id}", response_model=ApiResponse)
async def update_scene(
    scene_id: str,
    request: SceneUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """更新本体场景
    
    更新指定场景的信息，只能更新当前工作空间下的场景。
    
    Args:
        scene_id: 场景ID
        request: 场景更新请求
        db: 数据库会话
        current_user: 当前用户
        
    Returns:
        ApiResponse: 包含更新后的场景信息
    """
    api_logger.info(
        f"Scene update requested by user {current_user.id}, "
        f"scene_id={scene_id}"
    )
    
    try:
        from uuid import UUID
        
        # 验证UUID格式
        try:
            scene_uuid = UUID(scene_id)
        except ValueError:
            api_logger.warning(f"Invalid scene_id format: {scene_id}")
            return fail(BizCode.BAD_REQUEST, "请求参数无效", "无效的场景ID格式")
        
        # 获取当前工作空间ID
        workspace_id = current_user.current_workspace_id
        if not workspace_id:
            api_logger.warning(f"User {current_user.id} has no current workspace")
            return fail(BizCode.BAD_REQUEST, "请求参数无效", "当前用户没有工作空间")
        
        # 创建OntologyService实例
        from app.core.memory.llm_tools.openai_client import OpenAIClient
        from app.core.models.base import RedBearModelConfig
        
        dummy_config = RedBearModelConfig(
            model_name="dummy",
            provider="openai",
            api_key="dummy",
            base_url="https://api.openai.com/v1"
        )
        llm_client = OpenAIClient(model_config=dummy_config)
        service = OntologyService(llm_client=llm_client, db=db)
        
        # 调用服务层更新场景
        scene = service.update_scene(
            scene_id=scene_uuid,
            scene_name=request.scene_name,
            scene_description=request.scene_description,
            workspace_id=workspace_id
        )
        
        # 构建响应
        # 动态计算 type_num
        type_num = len(scene.classes) if scene.classes else 0
        
        response = SceneResponse(
            scene_id=scene.scene_id,
            scene_name=scene.scene_name,
            scene_description=scene.scene_description,
            type_num=type_num,
            workspace_id=scene.workspace_id,
            created_at=scene.created_at,
            updated_at=scene.updated_at,
            classes_count=type_num
        )
        
        api_logger.info(f"Scene updated successfully: {scene_id}")
        
        return success(data=response.model_dump(), msg="场景更新成功")
        
    except ValueError as e:
        api_logger.warning(f"Validation error in scene update: {str(e)}")
        return fail(BizCode.BAD_REQUEST, "请求参数无效", str(e))
        
    except RuntimeError as e:
        api_logger.error(f"Runtime error in scene update: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "场景更新失败", str(e))
        
    except Exception as e:
        api_logger.error(f"Unexpected error in scene update: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "场景更新失败", str(e))


@router.delete("/scene/{scene_id}", response_model=ApiResponse)
async def delete_scene(
    scene_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """删除本体场景
    
    删除指定场景及其所有关联类型，只能删除当前工作空间下的场景。
    
    Args:
        scene_id: 场景ID
        db: 数据库会话
        current_user: 当前用户
        
    Returns:
        ApiResponse: 删除结果
    """
    api_logger.info(
        f"Scene deletion requested by user {current_user.id}, "
        f"scene_id={scene_id}"
    )
    
    try:
        from uuid import UUID
        
        # 验证UUID格式
        try:
            scene_uuid = UUID(scene_id)
        except ValueError:
            api_logger.warning(f"Invalid scene_id format: {scene_id}")
            return fail(BizCode.BAD_REQUEST, "请求参数无效", "无效的场景ID格式")
        
        # 获取当前工作空间ID
        workspace_id = current_user.current_workspace_id
        if not workspace_id:
            api_logger.warning(f"User {current_user.id} has no current workspace")
            return fail(BizCode.BAD_REQUEST, "请求参数无效", "当前用户没有工作空间")
        
        # 创建OntologyService实例
        from app.core.memory.llm_tools.openai_client import OpenAIClient
        from app.core.models.base import RedBearModelConfig
        
        dummy_config = RedBearModelConfig(
            model_name="dummy",
            provider="openai",
            api_key="dummy",
            base_url="https://api.openai.com/v1"
        )
        llm_client = OpenAIClient(model_config=dummy_config)
        service = OntologyService(llm_client=llm_client, db=db)
        
        # 调用服务层删除场景
        success_flag = service.delete_scene(
            scene_id=scene_uuid,
            workspace_id=workspace_id
        )
        
        api_logger.info(f"Scene deleted successfully: {scene_id}")
        
        return success(data={"deleted": success_flag}, msg="场景删除成功")
        
    except ValueError as e:
        api_logger.warning(f"Validation error in scene deletion: {str(e)}")
        return fail(BizCode.BAD_REQUEST, "请求参数无效", str(e))
        
    except RuntimeError as e:
        api_logger.error(f"Runtime error in scene deletion: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "场景删除失败", str(e))
        
    except Exception as e:
        api_logger.error(f"Unexpected error in scene deletion: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "场景删除失败", str(e))


@router.get("/scenes/simple", response_model=ApiResponse)
async def get_scenes_simple(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取场景简单列表（轻量级，用于下拉选择）
    
    仅返回 scene_id 和 scene_name，不加载关联数据，响应速度快。
    适用于前端下拉选择场景的场景。
    
    Args:
        db: 数据库会话
        current_user: 当前用户
        
    Returns:
        ApiResponse: 包含场景简单列表
        
    Examples:
        GET /scenes/simple
        返回: {"data": [{"scene_id": "xxx", "scene_name": "场景1"}, ...]}
    """
    api_logger.info(f"Simple scene list requested by user {current_user.id}")
    
    try:
        workspace_id = current_user.current_workspace_id
        if not workspace_id:
            api_logger.warning(f"User {current_user.id} has no current workspace")
            return fail(BizCode.BAD_REQUEST, "请求参数无效", "当前用户没有工作空间")
        
        repo = OntologySceneRepository(db)
        scenes = repo.get_simple_list(workspace_id)
        
        api_logger.info(f"Simple scene list retrieved: {len(scenes)} scenes")
        return success(data=scenes, msg="查询成功")
        
    except Exception as e:
        api_logger.error(f"Failed to get simple scene list: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "查询失败", str(e))


@router.get("/scenes", response_model=ApiResponse)
async def get_scenes(
    workspace_id: Optional[str] = None,
    scene_name: Optional[str] = None,
    page: Optional[int] = None,
    pagesize: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取场景列表（支持模糊搜索和全量查询，全量查询支持分页）
    
    根据是否提供 scene_name 参数，执行不同的查询：
    - 提供 scene_name：进行模糊搜索，返回匹配的场景列表（支持分页）
    - 不提供 scene_name：返回工作空间下的所有场景（支持分页）
    
    支持中文和英文的模糊匹配，不区分大小写。
    
    Args:
        workspace_id: 工作空间ID（可选，默认当前用户工作空间）
        scene_name: 场景名称关键词（可选，支持模糊匹配）
        page: 页码（可选，从1开始）
        pagesize: 每页数量（可选）
        db: 数据库会话
        current_user: 当前用户
        
    Returns:
        ApiResponse: 包含场景列表和分页信息
        
    Examples:
        - 模糊搜索（不分页）：GET /scenes?workspace_id=xxx&scene_name=医疗
          输入 "医疗" 可以匹配到 "医疗场景"、"智慧医疗"、"医疗管理系统" 等
        - 模糊搜索（分页）：GET /scenes?workspace_id=xxx&scene_name=医疗&page=1&pagesize=10
          返回匹配 "医疗" 的第1页，每页10条数据
        - 全量查询（不分页）：GET /scenes?workspace_id=xxx
          返回工作空间下的所有场景
        - 全量查询（分页）：GET /scenes?workspace_id=xxx&page=1&pagesize=10
          返回第1页，每页10条数据
          
    Notes:
        - 分页参数 page 和 pagesize 必须同时提供
        - page 从1开始，pagesize 必须大于0
        - 返回格式：{"items": [...], "page": {"page": 1, "pagesize": 10, "total": 100, "hasnext": true}}
        - 不分页时，page 字段为 null
    """
    from app.controllers.ontology_secondary_routes import scenes_handler
    return await scenes_handler(workspace_id, scene_name, page, pagesize, db, current_user)


# ==================== 本体类型管理接口 ====================

@router.post("/class", response_model=ApiResponse)
async def create_class(
    request: ClassCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """创建本体类型
    
    在指定场景下创建新的本体类型。
    
    Args:
        request: 类型创建请求
        db: 数据库会话
        current_user: 当前用户
        
    Returns:
        ApiResponse: 包含创建的类型信息
    """
    from app.controllers.ontology_secondary_routes import create_class_handler
    return await create_class_handler(request, db, current_user)


@router.put("/class/{class_id}", response_model=ApiResponse)
async def update_class(
    class_id: str,
    request: ClassUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """更新本体类型
    
    更新指定类型的信息，只能更新当前工作空间下场景的类型。
    
    Args:
        class_id: 类型ID
        request: 类型更新请求
        db: 数据库会话
        current_user: 当前用户
        
    Returns:
        ApiResponse: 包含更新后的类型信息
    """
    from app.controllers.ontology_secondary_routes import update_class_handler
    return await update_class_handler(class_id, request, db, current_user)


@router.delete("/class/{class_id}", response_model=ApiResponse)
async def delete_class(
    class_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """删除本体类型
    
    删除指定类型，只能删除当前工作空间下场景的类型。
    
    Args:
        class_id: 类型ID
        db: 数据库会话
        current_user: 当前用户
        
    Returns:
        ApiResponse: 删除结果
    """
    from app.controllers.ontology_secondary_routes import delete_class_handler
    return await delete_class_handler(class_id, db, current_user)


@router.get("/classes", response_model=ApiResponse)
async def get_classes(
    scene_id: str,
    class_name: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取类型列表（支持模糊搜索和全量查询）
    
    根据是否提供 class_name 参数，执行不同的查询：
    - 提供 class_name：进行模糊搜索，返回匹配的类型列表
    - 不提供 class_name：返回场景下的所有类型
    
    支持中文和英文的模糊匹配，不区分大小写。
    返回结果包含场景的基本信息（scene_name 和 scene_description）。
    
    Args:
        scene_id: 场景ID（必填）
        class_name: 类型名称关键词（可选，支持模糊匹配）
        db: 数据库会话
        current_user: 当前用户
        
    Returns:
        ApiResponse: 包含类型列表和场景信息
        
    Examples:
        - 模糊搜索：GET /classes?scene_id=xxx&class_name=患者
          输入 "患者" 可以匹配到 "患者"、"患者信息"、"门诊患者" 等
        - 全量查询：GET /classes?scene_id=xxx
          返回场景下的所有类型
          
    Response Format:
        {
            "total": 3,
            "scene_id": "xxx",
            "scene_name": "医疗场景",
            "scene_description": "用于医疗领域的本体建模",
            "items": [...]
        }
    """
    from app.controllers.ontology_secondary_routes import classes_handler
    return await classes_handler(scene_id, class_name, db, current_user)


@router.get("/class/{class_id}", response_model=ApiResponse)
async def get_class(
    class_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取单个本体类型
    
    根据类型ID获取类型的详细信息，只能查询当前工作空间下场景的类型。
    
    Args:
        class_id: 类型ID
        db: 数据库会话
        current_user: 当前用户
        
    Returns:
        ApiResponse: 包含类型详细信息
        
    Response Format:
        {
            "code": 0,
            "msg": "查询成功",
            "data": {
                "class_id": "xxx",
                "class_name": "患者",
                "class_description": "在医疗机构中接受诊疗的个体",
                "scene_id": "xxx",
                "created_at": "2026-01-29T10:00:00",
                "updated_at": "2026-01-29T10:00:00"
            }
        }
    """
    from app.controllers.ontology_secondary_routes import get_class_handler
    return await get_class_handler(class_id, db, current_user)


# ==================== OWL 导入接口 ====================

@router.post("/import", response_model=ApiResponse)
async def import_owl_file(
    scene_id: str = Form(..., description="目标场景ID"),
    file: UploadFile = File(..., description="OWL/TTL文件"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """导入 OWL/TTL 文件到指定场景
    
    上传 OWL 或 TTL 文件，解析其中定义的本体类型（owl:Class），
    并直接保存到指定场景的 ontology_class 表中。
    
    文件格式根据文件扩展名自动识别：
    - .owl, .rdf, .xml -> rdfxml 格式
    - .ttl -> turtle 格式
    
    Args:
        scene_id: 目标场景ID（表单字段）
        file: 上传的文件（支持 .owl, .ttl, .rdf, .xml）
        db: 数据库会话
        current_user: 当前用户
        
    Returns:
        ApiResponse: 包含导入结果
    """
    from app.repositories.ontology_scene_repository import OntologySceneRepository
    from app.repositories.ontology_class_repository import OntologyClassRepository
    from uuid import UUID
    
    # 根据文件扩展名确定格式
    filename = file.filename.lower() if file.filename else ""
    if filename.endswith('.ttl'):
        owl_format = "turtle"
        file_type = "ttl"
    elif filename.endswith(('.owl', '.rdf', '.xml')):
        owl_format = "rdfxml"
        file_type = "owl"
    else:
        return fail(
            BizCode.BAD_REQUEST,
            "文件格式不支持",
            f"不支持的文件格式: {filename}，支持的格式: .owl, .ttl, .rdf, .xml"
        )
    
    api_logger.info(
        f"OWL import requested by user {current_user.id}, "
        f"scene_id={scene_id}, "
        f"filename={file.filename}, "
        f"format={owl_format}"
    )
    
    try:
        # 验证 scene_id 格式
        try:
            scene_uuid = UUID(scene_id)
        except ValueError:
            return fail(BizCode.BAD_REQUEST, "请求参数无效", "无效的场景ID格式")
        
        # 获取当前工作空间ID
        workspace_id = current_user.current_workspace_id
        if not workspace_id:
            api_logger.warning(f"User {current_user.id} has no current workspace")
            return fail(BizCode.BAD_REQUEST, "请求参数无效", "当前用户没有工作空间")
        
        # 1. 验证场景存在且属于当前工作空间
        scene_repo = OntologySceneRepository(db)
        scene = scene_repo.get_by_id(scene_uuid)
        
        if not scene:
            api_logger.warning(f"Scene not found: {scene_id}")
            return fail(BizCode.NOT_FOUND, "场景不存在", f"找不到场景: {scene_id}")
        
        if scene.workspace_id != workspace_id:
            api_logger.warning(
                f"Scene {scene_id} does not belong to workspace {workspace_id}"
            )
            return fail(BizCode.FORBIDDEN, "无权访问", "该场景不属于当前工作空间")
        
        # 2. 读取文件内容
        try:
            content = await file.read()
            owl_content = content.decode('utf-8')
        except UnicodeDecodeError:
            return fail(
                BizCode.BAD_REQUEST,
                f"{file_type}文件导入失败",
                "文件编码错误，请确保文件使用 UTF-8 编码"
            )
        
        # 3. 解析 OWL 内容
        owl_validator = OWLValidator()
        parsed_classes = owl_validator.parse_owl_content(
            owl_content=owl_content,
            format=owl_format
        )
        
        if not parsed_classes:
            api_logger.warning("No classes found in OWL content")
            return fail(
                BizCode.BAD_REQUEST,
                "未找到本体类型",
                "文件中没有定义任何本体类型（owl:Class）"
            )
        
        # 4. 获取场景下已有的类型名称（用于去重）
        class_repo = OntologyClassRepository(db)
        existing_classes = class_repo.get_by_scene(scene_uuid)
        existing_names = {cls.class_name for cls in existing_classes}
        
        # 5. 批量创建类型
        created_items = []
        skipped_count = 0
        
        for cls in parsed_classes:
            class_name = cls["name"]
            class_description = cls.get("description")
            
            # 检查是否重复
            if class_name in existing_names:
                skipped_count += 1
                api_logger.debug(f"Skipping duplicate class: {class_name}")
                continue
            
            # 创建类型
            ontology_class = class_repo.create(
                class_data={
                    "class_name": class_name,
                    "class_description": class_description
                },
                scene_id=scene_uuid
            )
            
            # 添加到已存在集合，防止同一批次内重复
            existing_names.add(class_name)
            
            created_items.append(ClassResponse(
                class_id=ontology_class.class_id,
                class_name=ontology_class.class_name,
                class_description=ontology_class.class_description,
                scene_id=ontology_class.scene_id,
                created_at=ontology_class.created_at,
                updated_at=ontology_class.updated_at
            ))
        
        # 6. 提交事务
        db.commit()
        
        # 7. 检查是否所有类型都被跳过（重复导入）
        if len(created_items) == 0 and skipped_count > 0:
            api_logger.warning(
                f"{file_type} import failed - all classes already exist, "
                f"scene_id={scene_id}, "
                f"skipped={skipped_count}"
            )
            return fail(
                BizCode.BAD_REQUEST,
                f"{file_type}文件导入失败",
                f"所有本体类型已存在，跳过 {skipped_count} 个重复类型"
            )
        
        # 8. 构建响应
        response = ImportOwlResponse(
            scene_id=scene_uuid,
            scene_name=scene.scene_name,
            imported_count=len(created_items),
            skipped_count=skipped_count,
            items=created_items
        )
        
        api_logger.info(
            f"{file_type} import completed, "
            f"scene_id={scene_id}, "
            f"format={owl_format}, "
            f"imported={len(created_items)}, "
            f"skipped={skipped_count}"
        )
        
        return success(data=response.model_dump(), msg=f"{file_type}文件导入成功")
        
    except ValueError as e:
        db.rollback()
        api_logger.warning(f"Validation error in import: {str(e)}")
        return fail(BizCode.BAD_REQUEST, f"{file_type}文件导入失败", str(e))
        
    except Exception as e:
        db.rollback()
        api_logger.error(f"Unexpected error in import: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, f"{file_type}文件导入失败", str(e))

# ==================== OWL 导出接口 ====================        
@router.post("/export")
async def export_owl_by_scene(
    request: ExportBySceneRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """按场景导出OWL/TTL文件
    
    根据scene_id从数据库查询该场景下的所有本体类型，并导出为文件下载。
    
    Args:
        request: 导出请求，包含 scene_id 和 format
        db: 数据库会话
        current_user: 当前用户
        
    Returns:
        StreamingResponse: 文件流响应，浏览器会直接下载文件
    """
    from uuid import UUID
    from app.repositories.ontology_scene_repository import OntologySceneRepository
    from app.repositories.ontology_class_repository import OntologyClassRepository
    
    api_logger.info(
        f"OWL export by scene requested by user {current_user.id}, "
        f"scene_id={request.scene_id}, "
        f"format={request.format}"
    )
    
    try:
        # 验证格式参数
        valid_formats = ["rdfxml", "turtle"]
        owl_format = request.format.lower() if request.format else "rdfxml"
        if owl_format not in valid_formats:
            api_logger.warning(f"Invalid format: {request.format}")
            return fail(
                BizCode.BAD_REQUEST,
                "格式参数无效",
                f"不支持的格式: {request.format}，支持的格式: rdfxml, turtle"
            )
        
        # 获取当前工作空间ID
        workspace_id = current_user.current_workspace_id
        if not workspace_id:
            api_logger.warning(f"User {current_user.id} has no current workspace")
            return fail(BizCode.BAD_REQUEST, "请求参数无效", "当前用户没有工作空间")
        
        # 1. 查询场景信息
        scene_repo = OntologySceneRepository(db)
        scene = scene_repo.get_by_id(request.scene_id)
        
        if not scene:
            api_logger.warning(f"Scene not found: {request.scene_id}")
            return fail(BizCode.NOT_FOUND, "场景不存在", f"找不到场景: {request.scene_id}")
        
        # 验证场景属于当前工作空间
        if scene.workspace_id != workspace_id:
            api_logger.warning(
                f"Scene {request.scene_id} does not belong to workspace {workspace_id}"
            )
            return fail(BizCode.FORBIDDEN, "无权访问", "该场景不属于当前工作空间")
        
        # 2. 查询场景下的所有本体类型
        class_repo = OntologyClassRepository(db)
        ontology_classes_db = class_repo.get_by_scene(request.scene_id)
        
        if not ontology_classes_db:
            api_logger.warning(f"No classes found in scene: {request.scene_id}")
            return fail(BizCode.BAD_REQUEST, "场景为空", "该场景下没有定义任何本体类型")
        
        # 3. 将数据库模型转换为OWL导出所需的OntologyClass格式
        ontology_classes: List[OntologyClass] = []
        for db_class in ontology_classes_db:
            owl_class = OntologyClass(
                id=str(db_class.class_id),
                name=db_class.class_name,
                name_chinese=db_class.class_name if _is_chinese(db_class.class_name) else None,
                description=db_class.class_description or "",
                examples=[],
                parent_class=None,
                entity_type="Concept",
                domain=scene.scene_name
            )
            ontology_classes.append(owl_class)
        
        # 4. 确定文件名、扩展名和 MIME 类型
        file_ext = ".ttl" if owl_format == "turtle" else ".owl"
        filename = _sanitize_filename(scene.scene_name) + file_ext
        media_type = "text/turtle" if owl_format == "turtle" else "application/rdf+xml"
        file_type = "ttl" if owl_format == "turtle" else "owl"
        
        # 5. 导出OWL文件
        with tempfile.NamedTemporaryFile(
            mode='w',
            suffix='.owl',
            delete=False
        ) as tmp_file:
            output_path = tmp_file.name
        
        owl_validator = OWLValidator()
        
        # 验证本体类
        is_valid, errors, world = owl_validator.validate_ontology_classes(
            classes=ontology_classes,
        )
        
        if not is_valid:
            logger.warning(
                f"OWL validation found {len(errors)} issues during export: {errors}"
            )
        
        if not world:
            error_msg = "Failed to create OWL world for export"
            logger.error(error_msg)
            return fail(BizCode.INTERNAL_ERROR, "创建OWL世界失败", error_msg)
        
        # 导出OWL文件（使用请求指定的格式）
        owl_content = owl_validator.export_to_owl(
            world=world,
            output_path=output_path,
            format=owl_format,
            classes=ontology_classes
        )
        
        api_logger.info(
            f"{file_type} export by scene completed, "
            f"scene={scene.scene_name}, "
            f"filename={filename}, "
            f"format={owl_format}, "
            f"classes_count={len(ontology_classes)}"
        )
        
        # 6. 返回文件流响应
        # filename 使用 ASCII 安全的默认名，filename* 使用 UTF-8 编码的实际名称
        ascii_filename = f"ontology{file_ext}"
        encoded_filename = quote(filename)
        return StreamingResponse(
            io.BytesIO(owl_content.encode('utf-8')),
            media_type=media_type,
            headers={
                "Content-Disposition": f"attachment; filename=\"{ascii_filename}\"; filename*=UTF-8''{encoded_filename}"
            }
        )
        
    except ValueError as e:
        api_logger.warning(f"Validation error in export by scene: {str(e)}")
        file_type = "ttl" if (request.format and request.format.lower() == "turtle") else "owl"
        return fail(BizCode.BAD_REQUEST, "请求参数无效", str(e))
        
    except RuntimeError as e:
        api_logger.error(f"Runtime error in export by scene: {str(e)}", exc_info=True)
        file_type = "ttl" if (request.format and request.format.lower() == "turtle") else "owl"
        return fail(BizCode.INTERNAL_ERROR, f"{file_type}文件导出失败", str(e))
        
    except Exception as e:
        api_logger.error(f"Unexpected error in export by scene: {str(e)}", exc_info=True)
        file_type = "ttl" if (request.format and request.format.lower() == "turtle") else "owl"
        return fail(BizCode.INTERNAL_ERROR, f"{file_type}文件导出失败", str(e))


def _is_chinese(text: str) -> bool:
    """检查文本是否包含中文字符"""
    for char in text:
        if '\u4e00' <= char <= '\u9fff':
            return True
    return False


def _sanitize_filename(name: str) -> str:
    """清理文件名，移除不合法字符"""
    import re
    # 移除或替换不合法的文件名字符
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', name)
    # 移除前后空格
    sanitized = sanitized.strip()
    # 如果为空，使用默认名称
    if not sanitized:
        sanitized = "ontology_export"
    return sanitized
