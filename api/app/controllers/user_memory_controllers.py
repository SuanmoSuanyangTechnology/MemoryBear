"""
用户记忆相关的控制器
包含用户摘要、记忆洞察、节点统计、图数据和用户档案等接口
"""
from typing import Optional
import datetime
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends

from app.db import get_db
from app.core.logging_config import get_api_logger
from app.core.response_utils import success, fail
from app.core.error_codes import BizCode
from app.services.user_memory_service import (
    UserMemoryService,
    analytics_node_statistics,
    analytics_memory_types,
    analytics_graph_data,
)
from app.schemas.response_schema import ApiResponse
from app.schemas.memory_storage_schema import GenerateCacheRequest
from app.schemas.end_user_schema import (
    EndUserProfileResponse,
    EndUserProfileUpdate,
)
from app.models.end_user_model import EndUser
from app.dependencies import get_current_user
from app.models.user_model import User

# Get API logger
api_logger = get_api_logger()

# Initialize service
user_memory_service = UserMemoryService()

router = APIRouter(
    prefix="/memory-storage",
    tags=["User Memory"],
)


@router.get("/analytics/memory_insight/report", response_model=ApiResponse)
async def get_memory_insight_report_api(
    end_user_id: str,  # 使用 end_user_id
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    ) -> dict:
    """获取缓存的记忆洞察报告"""
    api_logger.info(f"记忆洞察报告请求: end_user_id={end_user_id}, user={current_user.username}")
    try:
        # 调用服务层获取缓存数据
        result = await user_memory_service.get_cached_memory_insight(db, end_user_id)
        
        if result["is_cached"]:
            # 缓存存在，返回缓存数据
            api_logger.info(f"成功返回缓存的记忆洞察报告: end_user_id={end_user_id}")
            return success(data=result, msg="查询成功")
        else:
            # 缓存不存在，返回提示消息
            api_logger.info(f"记忆洞察报告缓存不存在: end_user_id={end_user_id}")
            return success(data=result, msg="查询成功")
    except Exception as e:
        api_logger.error(f"记忆洞察报告查询失败: end_user_id={end_user_id}, error={str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "记忆洞察报告查询失败", str(e))


@router.get("/analytics/user_summary", response_model=ApiResponse)
async def get_user_summary_api(
    end_user_id: str,  # 使用 end_user_id
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    ) -> dict:
    """获取缓存的用户摘要"""
    api_logger.info(f"用户摘要请求: end_user_id={end_user_id}, user={current_user.username}")
    try:
        # 调用服务层获取缓存数据
        result = await user_memory_service.get_cached_user_summary(db, end_user_id)
        
        if result["is_cached"]:
            # 缓存存在，返回缓存数据
            api_logger.info(f"成功返回缓存的用户摘要: end_user_id={end_user_id}")
            return success(data=result, msg="查询成功")
        else:
            # 缓存不存在，返回提示消息
            api_logger.info(f"用户摘要缓存不存在: end_user_id={end_user_id}")
            return success(data=result, msg="查询成功")
    except Exception as e:
        api_logger.error(f"用户摘要查询失败: end_user_id={end_user_id}, error={str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "用户摘要查询失败", str(e))


@router.post("/analytics/generate_cache", response_model=ApiResponse)
async def generate_cache_api(
    request: GenerateCacheRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    手动触发缓存生成
    
    - 如果提供 end_user_id，只为该用户生成
    - 如果不提供，为当前工作空间的所有用户生成
    """
    workspace_id = current_user.current_workspace_id
    
    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试生成缓存但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")
    
    group_id = request.end_user_id
    
    api_logger.info(
        f"缓存生成请求: user={current_user.username}, workspace={workspace_id}, "
        f"end_user_id={group_id if group_id else '全部用户'}"
    )
    
    try:
        if group_id:
            # 为单个用户生成
            api_logger.info(f"开始为单个用户生成缓存: end_user_id={group_id}")
            
            # 生成记忆洞察
            insight_result = await user_memory_service.generate_and_cache_insight(db, group_id, workspace_id)
            
            # 生成用户摘要
            summary_result = await user_memory_service.generate_and_cache_summary(db, group_id, workspace_id)
            
            # 构建响应
            result = {
                "end_user_id": group_id,
                "insight_success": insight_result["success"],
                "summary_success": summary_result["success"],
                "errors": []
            }
            
            # 收集错误信息
            if not insight_result["success"]:
                result["errors"].append({
                    "type": "insight",
                    "error": insight_result.get("error")
                })
            if not summary_result["success"]:
                result["errors"].append({
                    "type": "summary",
                    "error": summary_result.get("error")
                })
            
            # 记录结果
            if result["insight_success"] and result["summary_success"]:
                api_logger.info(f"成功为用户 {group_id} 生成缓存")
            else:
                api_logger.warning(f"用户 {group_id} 的缓存生成部分失败: {result['errors']}")
            
            return success(data=result, msg="生成完成")
            
        else:
            # 为整个工作空间生成
            api_logger.info(f"开始为工作空间 {workspace_id} 批量生成缓存")
            
            result = await user_memory_service.generate_cache_for_workspace(db, workspace_id)
            
            # 记录统计信息
            api_logger.info(
                f"工作空间 {workspace_id} 批量生成完成: "
                f"总数={result['total_users']}, 成功={result['successful']}, 失败={result['failed']}"
            )
            
            return success(data=result, msg="批量生成完成")
            
    except Exception as e:
        api_logger.error(f"缓存生成失败: user={current_user.username}, error={str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "缓存生成失败", str(e))


@router.get("/analytics/node_statistics", response_model=ApiResponse)
async def get_node_statistics_api(
    end_user_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    workspace_id = current_user.current_workspace_id
    
    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试查询节点统计但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")
    
    api_logger.info(f"记忆类型统计请求: end_user_id={end_user_id}, user={current_user.username}, workspace={workspace_id}")
    
    try:
        # 调用新的记忆类型统计函数
        result = await analytics_memory_types(db, end_user_id)
        
        # 计算总数用于日志
        total_count = sum(item["count"] for item in result)
        api_logger.info(f"成功获取记忆类型统计: end_user_id={end_user_id}, 总记忆数={total_count}, 类型数={len(result)}")
        return success(data=result, msg="查询成功")
    except Exception as e:
        api_logger.error(f"记忆类型查询失败: end_user_id={end_user_id}, error={str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "记忆类型查询失败", str(e))

@router.get("/analytics/graph_data", response_model=ApiResponse)
async def get_graph_data_api(
    end_user_id: str,
    node_types: Optional[str] = None,
    limit: int = 100,
    depth: int = 1,
    center_node_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    workspace_id = current_user.current_workspace_id
    
    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试查询图数据但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")
    
    # 参数验证
    if limit > 1000:
        limit = 1000
        api_logger.warning("limit 参数超过最大值，已调整为 1000")
    
    if depth > 3:
        depth = 3
        api_logger.warning("depth 参数超过最大值，已调整为 3")
    
    # 解析 node_types 参数
    node_types_list = None
    if node_types:
        node_types_list = [t.strip() for t in node_types.split(",") if t.strip()]
    
    api_logger.info(
        f"图数据查询请求: end_user_id={end_user_id}, user={current_user.username}, "
        f"workspace={workspace_id}, node_types={node_types_list}, limit={limit}, depth={depth}"
    )
    
    try:
        result = await analytics_graph_data(
            db=db,
            end_user_id=end_user_id,
            node_types=node_types_list,
            limit=limit,
            depth=depth,
            center_node_id=center_node_id
        )
        
        # 检查是否有错误消息
        if "message" in result and result["statistics"]["total_nodes"] == 0:
            api_logger.warning(f"图数据查询返回空结果: {result.get('message')}")
            return success(data=result, msg=result.get("message", "查询成功"))
        
        api_logger.info(
            f"成功获取图数据: end_user_id={end_user_id}, "
            f"nodes={result['statistics']['total_nodes']}, "
            f"edges={result['statistics']['total_edges']}"
        )
        return success(data=result, msg="查询成功")
        
    except Exception as e:
        api_logger.error(f"图数据查询失败: end_user_id={end_user_id}, error={str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "图数据查询失败", str(e))


@router.get("/read_end_user/profile", response_model=ApiResponse)
async def get_end_user_profile(
    end_user_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    workspace_id = current_user.current_workspace_id
    
    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试查询用户信息但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")
    
    api_logger.info(
        f"用户信息查询请求: end_user_id={end_user_id}, user={current_user.username}, "
        f"workspace={workspace_id}"
    )
    
    try:
        # 查询终端用户
        end_user = db.query(EndUser).filter(EndUser.id == end_user_id).first()
        
        if not end_user:
            api_logger.warning(f"终端用户不存在: end_user_id={end_user_id}")
            return fail(BizCode.INVALID_PARAMETER, "终端用户不存在", f"end_user_id={end_user_id}")
        
        # 构建响应数据
        profile_data = EndUserProfileResponse(
            id=end_user.id,
            other_name=end_user.other_name,
            position=end_user.position,
            department=end_user.department,
            contact=end_user.contact,
            phone=end_user.phone,
            hire_date=end_user.hire_date,
            updatetime_profile=end_user.updatetime_profile
        )
        
        api_logger.info(f"成功获取用户信息: end_user_id={end_user_id}")
        return success(data=profile_data.model_dump(), msg="查询成功")
        
    except Exception as e:
        api_logger.error(f"用户信息查询失败: end_user_id={end_user_id}, error={str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "用户信息查询失败", str(e))


@router.post("/updated_end_user/profile", response_model=ApiResponse)
async def update_end_user_profile(
    profile_update: EndUserProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    更新终端用户的基本信息
    
    该接口可以更新用户的姓名、职位、部门、联系方式、电话和入职日期等信息。
    所有字段都是可选的，只更新提供的字段。
    
    """
    workspace_id = current_user.current_workspace_id
    end_user_id = profile_update.end_user_id
    
    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试更新用户信息但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")
    
    api_logger.info(
        f"用户信息更新请求: end_user_id={end_user_id}, user={current_user.username}, "
        f"workspace={workspace_id}"
    )
    
    try:
        # 查询终端用户
        end_user = db.query(EndUser).filter(EndUser.id == end_user_id).first()
        
        if not end_user:
            api_logger.warning(f"终端用户不存在: end_user_id={end_user_id}")
            return fail(BizCode.INVALID_PARAMETER, "终端用户不存在", f"end_user_id={end_user_id}")
        
        # 更新字段（只更新提供的字段，排除 end_user_id）
        # 允许 None 值来重置字段（如 hire_date）
        update_data = profile_update.model_dump(exclude_unset=True, exclude={'end_user_id'})
        for field, value in update_data.items():
            setattr(end_user, field, value)
        
        # 更新 updated_at 时间戳
        end_user.updated_at = datetime.datetime.now()
        
        # 更新 updatetime_profile 为当前时间戳（毫秒）
        current_timestamp = int(datetime.datetime.now().timestamp() * 1000)
        end_user.updatetime_profile = current_timestamp
        
        # 提交更改
        db.commit()
        db.refresh(end_user)
        
        # 构建响应数据
        profile_data = EndUserProfileResponse(
            id=end_user.id,
            other_name=end_user.other_name,
            position=end_user.position,
            department=end_user.department,
            contact=end_user.contact,
            phone=end_user.phone,
            hire_date=end_user.hire_date,
            updatetime_profile=end_user.updatetime_profile
        )
        
        api_logger.info(f"成功更新用户信息: end_user_id={end_user_id}, updated_fields={list(update_data.keys())}, updatetime_profile={current_timestamp}")
        return success(data=profile_data.model_dump(), msg="更新成功")
        
    except Exception as e:
        db.rollback()
        api_logger.error(f"用户信息更新失败: end_user_id={end_user_id}, error={str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "用户信息更新失败", str(e))
