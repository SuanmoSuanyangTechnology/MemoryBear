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

async def scenes_handler(
    workspace_id: Optional[str] = None,
    scene_name: Optional[str] = None,
    page: Optional[int] = None,
    page_size: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取场景列表（支持模糊搜索和全量查询，全量查询支持分页）
    
    当提供 scene_name 参数时，进行模糊搜索（不分页）；
    当不提供 scene_name 参数时，返回所有场景（支持分页）。
    
    Args:
        workspace_id: 工作空间ID（可选，默认当前用户工作空间）
        scene_name: 场景名称关键词（可选，支持模糊匹配）
        page: 页码（可选，从1开始，仅在全量查询时有效）
        page_size: 每页数量（可选，仅在全量查询时有效）
        db: 数据库会话
        current_user: 当前用户
    """
    operation = "search" if scene_name else "list"
    api_logger.info(
        f"Scene {operation} requested by user {current_user.id}, "
        f"workspace_id={workspace_id}, keyword={scene_name}, page={page}, page_size={page_size}"
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
        
        # 根据是否提供 scene_name 决定查询方式
        if scene_name and scene_name.strip():
            # 模糊搜索场景（不分页）
            scenes = service.search_scenes_by_name(scene_name.strip(), ws_uuid)
            total = len(scenes)
            
            # 构建响应（不包含分页信息）
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
                total=total,
                items=items
            )
            
            api_logger.info(
                f"Scene search completed: found {len(items)} scenes matching '{scene_name}' "
                f"in workspace {ws_uuid}"
            )
        else:
            # 获取所有场景（支持分页）
            # 验证分页参数
            if page is not None and page < 1:
                api_logger.warning(f"Invalid page number: {page}")
                return fail(BizCode.BAD_REQUEST, "请求参数无效", "页码必须大于0")
            
            if page_size is not None and page_size < 1:
                api_logger.warning(f"Invalid page_size: {page_size}")
                return fail(BizCode.BAD_REQUEST, "请求参数无效", "每页数量必须大于0")
            
            # 如果只提供了page或page_size中的一个，返回错误
            if (page is not None and page_size is None) or (page is None and page_size is not None):
                api_logger.warning(f"Incomplete pagination params: page={page}, page_size={page_size}")
                return fail(BizCode.BAD_REQUEST, "请求参数无效", "分页参数page和page_size必须同时提供")
            
            scenes, total = service.list_scenes(ws_uuid, page, page_size)
            
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
            
            # 构建响应（包含分页信息）
            if page is not None and page_size is not None:
                import math
                total_pages = math.ceil(total / page_size) if page_size > 0 else 0
                response = SceneListResponse(
                    total=total,
                    items=items,
                    page=page,
                    page_size=page_size,
                    total_pages=total_pages
                )
            else:
                response = SceneListResponse(
                    total=total,
                    items=items
                )
            
            api_logger.info(f"Scene list retrieved successfully, count={len(items)}, total={total}")
        
        return success(data=response.model_dump(mode='json'), msg="查询成功")
        
    except ValueError as e:
        api_logger.warning(f"Validation error in scene {operation}: {str(e)}")
        return fail(BizCode.BAD_REQUEST, "请求参数无效", str(e))
        
    except RuntimeError as e:
        api_logger.error(f"Runtime error in scene {operation}: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "查询失败", str(e))
        
    except Exception as e:
        api_logger.error(f"Unexpected error in scene {operation}: {str(e)}", exc_info=True)
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


async def classes_handler(
    scene_id: str,
    class_name: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取类型列表（支持模糊搜索和全量查询）
    
    当提供 class_name 参数时，进行模糊搜索；
    当不提供 class_name 参数时，返回场景下的所有类型。
    
    Args:
        scene_id: 场景ID（必填）
        class_name: 类型名称关键词（可选，支持模糊匹配）
        db: 数据库会话
        current_user: 当前用户
    """
    operation = "search" if class_name else "list"
    api_logger.info(
        f"Class {operation} requested by user {current_user.id}, "
        f"keyword={class_name}, scene_id={scene_id}"
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
        
        # 根据是否提供 class_name 决定查询方式
        if class_name and class_name.strip():
            # 模糊搜索类型
            classes = service.search_classes_by_name(class_name.strip(), scene_uuid, workspace_id)
        else:
            # 获取所有类型
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
        
        if class_name:
            api_logger.info(
                f"Class search completed: found {len(items)} classes matching '{class_name}' "
                f"in scene {scene_id}"
            )
        else:
            api_logger.info(f"Class list retrieved successfully, count={len(items)}")
        
        return success(data=response.model_dump(mode='json'), msg="查询成功")
        
    except ValueError as e:
        api_logger.warning(f"Validation error in class {operation}: {str(e)}")
        return fail(BizCode.BAD_REQUEST, "请求参数无效", str(e))
        
    except RuntimeError as e:
        api_logger.error(f"Runtime error in class {operation}: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "查询失败", str(e))
        
    except Exception as e:
        api_logger.error(f"Unexpected error in class {operation}: {str(e)}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, "查询失败", str(e))
