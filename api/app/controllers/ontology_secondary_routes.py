# -*- coding: utf-8 -*-
"""本体场景和类型路由（续）

由于主Controller文件较大，将剩余路由放在此文件中。
"""

from uuid import UUID
from typing import Optional

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.logging_config import get_api_logger
from app.core.response_utils import fail, success
from app.db import get_db
from app.dependencies import get_current_user
from app.models.user_model import User
from app.schemas.ontology_schemas import (
    SceneResponse,
    SceneListResponse,
    ClassCreateRequest,
    ClassUpdateRequest,
    ClassResponse,
    ClassListResponse,
)
from app.schemas.response_schema import ApiResponse
from app.services.ontology_service import OntologyService
from app.core.memory.llm_tools.openai_client import OpenAIClient
from app.core.models.base import RedBearModelConfig


api_logger = get_api_logger()


def _get_dummy_ontology_service(db: Session) -> OntologyService:
    """获取OntologyService实例（不需要LLM）
    
    场景和类型管理不需要LLM，创建一个dummy配置。
    """
    dummy_config = RedBearModelConfig(
        model_name="dummy",
        provider="openai",
        api_key="dummy",
        base_url="https://api.openai.com/v1"
    )
    llm_client = OpenAIClient(model_config=dummy_config)
    return OntologyService(llm_client=llm_client, db=db)


# 这些函数将被导入到主Controller中

async def get_scene_handler(
    workspace_id: str,
    scene_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取场景列表（模糊搜索）
    
    根据工作空间ID和场景名称关键词进行模糊搜索，返回匹配的场景列表。
    """
    api_logger.info(
        f"Scene search requested by user {current_user.id}, "
        f"workspace_id={workspace_id}, keyword={scene_name}"
    )
    
    try:
        # 验证UUID格式
        try:
            ws_uuid = UUID(workspace_id)
        except ValueError:
            api_logger.warning(f"Invalid workspace_id format: {workspace_id}")
            return fail(BizCode.BAD_REQUEST, "请求参数无效", "无效的工作空间ID格式")
        
        # 验证场景名称关键词
        if not scene_name or not scene_name.strip():
            api_logger.warning("Empty scene_name provided")
            return fail(BizCode.BAD_REQUEST, "请求参数无效", "场景名称关键词不能为空")
        
        # 创建Service
        service = _get_dummy_ontology_service(db)
        
        # 模糊搜索场景
        scenes = service.search_scenes_by_name(scene_name.strip(), ws_uuid)
        
        # 构建响应
        items = []
        for scene in scenes:
            # 获取前3个class_name作为entity_type
            entity_type = [cls.class_name for cls in scene.classes[:3]] if scene.classes else None
            # 动态计算 type_num
            type_num = len(scene.classes) if scene.classes else 0
            
            items.append(SceneResponse(
                scene_id=scene.scene_id,
                scene_name=scene.scene_name,
                scene_description=scene.scene_description,
                type_num=type_num,
                entity_type=entity_type,
                workspace_id=scene.workspace_id,
                created_at=scene.created_at,
                updated_at=scene.updated_at,
                classes_count=type_num
            ))
        
        response = SceneListResponse(
            total=len(items),
            items=items
        )
        
        api_logger.info(
            f"Scene search completed: found {len(items)} scenes matching '{scene_name}' "
            f"in workspace {workspace_id}"
        )
        
        return success(data=response.model_dump(mode='json'), msg="查询成功")
        
    except ValueError as e:
        api_logger.warning(f"Validation error in scene search: {str(e)}")
        return fail(BizCode.BAD_REQUEST, "请求参数无效", str(e))
        
    except RuntimeError as e:
        api_logger.error(f"Runtime error in scene search: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "查询失败", str(e))
        
    except Exception as e:
        api_logger.error(f"Unexpected error in scene search: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "查询失败", str(e))


async def list_scenes_handler(
    workspace_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取工作空间下的所有场景"""
    api_logger.info(
        f"Scene list requested by user {current_user.id}, "
        f"workspace_id={workspace_id}"
    )
    
    try:
        # 确定工作空间ID
        if workspace_id:
            try:
                ws_uuid = UUID(workspace_id)
            except ValueError:
                api_logger.warning(f"Invalid workspace_id format: {workspace_id}")
                return fail(BizCode.BAD_REQUEST, "请求参数无效", "无效的工作空间ID格式")
        else:
            ws_uuid = current_user.current_workspace_id
            if not ws_uuid:
                api_logger.warning(f"User {current_user.id} has no current workspace")
                return fail(BizCode.BAD_REQUEST, "请求参数无效", "当前用户没有工作空间")
        
        # 创建Service
        service = _get_dummy_ontology_service(db)
        
        # 获取场景列表
        scenes = service.list_scenes(ws_uuid)
        
        # 构建响应
        items = []
        for scene in scenes:
            # 获取前3个class_name作为entity_type
            entity_type = [cls.class_name for cls in scene.classes[:3]] if scene.classes else None
            # 动态计算 type_num
            type_num = len(scene.classes) if scene.classes else 0
            
            items.append(SceneResponse(
                scene_id=scene.scene_id,
                scene_name=scene.scene_name,
                scene_description=scene.scene_description,
                type_num=type_num,
                entity_type=entity_type,
                workspace_id=scene.workspace_id,
                created_at=scene.created_at,
                updated_at=scene.updated_at,
                classes_count=type_num
            ))
        
        response = SceneListResponse(
            total=len(items),
            items=items
        )
        
        api_logger.info(f"Scene list retrieved successfully, count={len(items)}")
        
        return success(data=response.model_dump(mode='json'), msg="查询成功")
        
    except ValueError as e:
        api_logger.warning(f"Validation error in scene list: {str(e)}")
        return fail(BizCode.BAD_REQUEST, "请求参数无效", str(e))
        
    except RuntimeError as e:
        api_logger.error(f"Runtime error in scene list: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "查询失败", str(e))
        
    except Exception as e:
        api_logger.error(f"Unexpected error in scene list: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "查询失败", str(e))


# ==================== 本体类型管理接口 ====================

async def create_class_handler(
    request: ClassCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """创建本体类型"""
    api_logger.info(
        f"Class creation requested by user {current_user.id}, "
        f"name={request.class_name}, scene_id={request.scene_id}"
    )
    
    try:
        # 获取当前工作空间ID
        workspace_id = current_user.current_workspace_id
        if not workspace_id:
            api_logger.warning(f"User {current_user.id} has no current workspace")
            return fail(BizCode.BAD_REQUEST, "请求参数无效", "当前用户没有工作空间")
        
        # 创建Service
        service = _get_dummy_ontology_service(db)
        
        # 创建类型
        ontology_class = service.create_class(
            scene_id=request.scene_id,
            class_name=request.class_name,
            class_description=request.class_description,
            workspace_id=workspace_id
        )
        
        # 构建响应
        response = ClassResponse(
            class_id=ontology_class.class_id,
            class_name=ontology_class.class_name,
            class_description=ontology_class.class_description,
            scene_id=ontology_class.scene_id,
            created_at=ontology_class.created_at,
            updated_at=ontology_class.updated_at
        )
        
        api_logger.info(f"Class created successfully: {ontology_class.class_id}")
        
        return success(data=response.model_dump(mode='json'), msg="类型创建成功")
        
    except ValueError as e:
        api_logger.warning(f"Validation error in class creation: {str(e)}")
        return fail(BizCode.BAD_REQUEST, "请求参数无效", str(e))
        
    except RuntimeError as e:
        api_logger.error(f"Runtime error in class creation: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "类型创建失败", str(e))
        
    except Exception as e:
        api_logger.error(f"Unexpected error in class creation: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "类型创建失败", str(e))


async def update_class_handler(
    class_id: str,
    request: ClassUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """更新本体类型"""
    api_logger.info(
        f"Class update requested by user {current_user.id}, "
        f"class_id={class_id}"
    )
    
    try:
        # 验证UUID格式
        try:
            class_uuid = UUID(class_id)
        except ValueError:
            api_logger.warning(f"Invalid class_id format: {class_id}")
            return fail(BizCode.BAD_REQUEST, "请求参数无效", "无效的类型ID格式")
        
        # 获取当前工作空间ID
        workspace_id = current_user.current_workspace_id
        if not workspace_id:
            api_logger.warning(f"User {current_user.id} has no current workspace")
            return fail(BizCode.BAD_REQUEST, "请求参数无效", "当前用户没有工作空间")
        
        # 创建Service
        service = _get_dummy_ontology_service(db)
        
        # 更新类型
        ontology_class = service.update_class(
            class_id=class_uuid,
            class_name=request.class_name,
            class_description=request.class_description,
            workspace_id=workspace_id
        )
        
        # 构建响应
        response = ClassResponse(
            class_id=ontology_class.class_id,
            class_name=ontology_class.class_name,
            class_description=ontology_class.class_description,
            scene_id=ontology_class.scene_id,
            created_at=ontology_class.created_at,
            updated_at=ontology_class.updated_at
        )
        
        api_logger.info(f"Class updated successfully: {class_id}")
        
        return success(data=response.model_dump(mode='json'), msg="类型更新成功")
        
    except ValueError as e:
        api_logger.warning(f"Validation error in class update: {str(e)}")
        return fail(BizCode.BAD_REQUEST, "请求参数无效", str(e))
        
    except RuntimeError as e:
        api_logger.error(f"Runtime error in class update: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "类型更新失败", str(e))
        
    except Exception as e:
        api_logger.error(f"Unexpected error in class update: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "类型更新失败", str(e))


async def delete_class_handler(
    class_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """删除本体类型"""
    api_logger.info(
        f"Class deletion requested by user {current_user.id}, "
        f"class_id={class_id}"
    )
    
    try:
        # 验证UUID格式
        try:
            class_uuid = UUID(class_id)
        except ValueError:
            api_logger.warning(f"Invalid class_id format: {class_id}")
            return fail(BizCode.BAD_REQUEST, "请求参数无效", "无效的类型ID格式")
        
        # 获取当前工作空间ID
        workspace_id = current_user.current_workspace_id
        if not workspace_id:
            api_logger.warning(f"User {current_user.id} has no current workspace")
            return fail(BizCode.BAD_REQUEST, "请求参数无效", "当前用户没有工作空间")
        
        # 创建Service
        service = _get_dummy_ontology_service(db)
        
        # 删除类型
        success_flag = service.delete_class(
            class_id=class_uuid,
            workspace_id=workspace_id
        )
        
        api_logger.info(f"Class deleted successfully: {class_id}")
        
        return success(data={"deleted": success_flag}, msg="类型删除成功")
        
    except ValueError as e:
        api_logger.warning(f"Validation error in class deletion: {str(e)}")
        return fail(BizCode.BAD_REQUEST, "请求参数无效", str(e))
        
    except RuntimeError as e:
        api_logger.error(f"Runtime error in class deletion: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "类型删除失败", str(e))
        
    except Exception as e:
        api_logger.error(f"Unexpected error in class deletion: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "类型删除失败", str(e))


async def get_class_handler(
    class_name: str,
    scene_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取类型列表（模糊搜索）
    
    根据类型名称关键词和场景ID进行模糊搜索，返回匹配的类型列表。
    """
    api_logger.info(
        f"Class search requested by user {current_user.id}, "
        f"keyword={class_name}, scene_id={scene_id}"
    )
    
    try:
        # 验证UUID格式
        try:
            scene_uuid = UUID(scene_id)
        except ValueError:
            api_logger.warning(f"Invalid scene_id format: {scene_id}")
            return fail(BizCode.BAD_REQUEST, "请求参数无效", "无效的场景ID格式")
        
        # 验证类型名称关键词
        if not class_name or not class_name.strip():
            api_logger.warning("Empty class_name provided")
            return fail(BizCode.BAD_REQUEST, "请求参数无效", "类型名称关键词不能为空")
        
        # 获取当前工作空间ID
        workspace_id = current_user.current_workspace_id
        if not workspace_id:
            api_logger.warning(f"User {current_user.id} has no current workspace")
            return fail(BizCode.BAD_REQUEST, "请求参数无效", "当前用户没有工作空间")
        
        # 创建Service
        service = _get_dummy_ontology_service(db)
        
        # 模糊搜索类型
        classes = service.search_classes_by_name(class_name.strip(), scene_uuid, workspace_id)
        
        # 构建响应
        items = []
        for ontology_class in classes:
            items.append(ClassResponse(
                class_id=ontology_class.class_id,
                class_name=ontology_class.class_name,
                class_description=ontology_class.class_description,
                scene_id=ontology_class.scene_id,
                created_at=ontology_class.created_at,
                updated_at=ontology_class.updated_at
            ))
        
        response = ClassListResponse(
            total=len(items),
            scene_id=scene_uuid,
            items=items
        )
        
        api_logger.info(
            f"Class search completed: found {len(items)} classes matching '{class_name}' "
            f"in scene {scene_id}"
        )
        
        return success(data=response.model_dump(mode='json'), msg="查询成功")
        
    except ValueError as e:
        api_logger.warning(f"Validation error in class search: {str(e)}")
        return fail(BizCode.BAD_REQUEST, "请求参数无效", str(e))
        
    except RuntimeError as e:
        api_logger.error(f"Runtime error in class search: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "查询失败", str(e))
        
    except Exception as e:
        api_logger.error(f"Unexpected error in class search: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "查询失败", str(e))


async def list_classes_handler(
    scene_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取场景下的所有类型"""
    api_logger.info(
        f"Class list requested by user {current_user.id}, "
        f"scene_id={scene_id}"
    )
    
    try:
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
        
        # 创建Service
        service = _get_dummy_ontology_service(db)
        
        # 获取类型列表
        classes = service.list_classes_by_scene(scene_uuid, workspace_id)
        
        # 构建响应
        items = []
        for ontology_class in classes:
            items.append(ClassResponse(
                class_id=ontology_class.class_id,
                class_name=ontology_class.class_name,
                class_description=ontology_class.class_description,
                scene_id=ontology_class.scene_id,
                created_at=ontology_class.created_at,
                updated_at=ontology_class.updated_at
            ))
        
        response = ClassListResponse(
            total=len(items),
            scene_id=scene_uuid,
            items=items
        )
        
        api_logger.info(f"Class list retrieved successfully, count={len(items)}")
        
        return success(data=response.model_dump(mode='json'), msg="查询成功")
        
    except ValueError as e:
        api_logger.warning(f"Validation error in class list: {str(e)}")
        return fail(BizCode.BAD_REQUEST, "请求参数无效", str(e))
        
    except RuntimeError as e:
        api_logger.error(f"Runtime error in class list: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "查询失败", str(e))
        
    except Exception as e:
        api_logger.error(f"Unexpected error in class list: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "查询失败", str(e))
