"""终端用户信息（End User）服务接口 - 基于 JWT 认证

收口终端用户信息的查询与更新（原分散在 memory_analytics_controller 与
user_memory_controllers），使对内 /api/end_user/* 与对外 /v1/end_user/* 路径一致。
函数体整体迁入，逻辑不变，仅装饰器路径调整为 /end_user/*。

路由前缀: /end_user
认证方式: JWT Token
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.logging_config import get_api_logger
from app.core.response_utils import fail, success
from app.db import get_db
from app.dependencies import get_current_user
from app.models.user_model import User
from app.repositories.end_user_repository import EndUserRepository
from app.schemas.end_user_info_schema import EndUserInfoUpdate
from app.schemas.response_schema import ApiResponse
from app.services.user_memory_service import UserMemoryService

api_logger = get_api_logger()

user_memory_service = UserMemoryService()

router = APIRouter(
    prefix="/end_user",
    tags=["End User"],
)


@router.get("/info", response_model=ApiResponse)
async def get_end_user_info(
    end_user_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    查询终端用户信息记录

    根据 end_user_id 查询单条终端用户信息记录。
    """
    workspace_id = current_user.current_workspace_id

    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试查询终端用户信息但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    api_logger.info(
        f"查询终端用户信息请求: end_user_id={end_user_id}, user={current_user.username}, "
        f"workspace={workspace_id}"
    )

    # 校验 end_user 是否属于当前工作空间
    end_user_repo = EndUserRepository(db)
    end_user = end_user_repo.get_end_user_by_id(end_user_id)
    if end_user is None:
        return fail(BizCode.USER_NOT_FOUND, "终端用户不存在", "end_user not found")
    if str(end_user.workspace_id) != str(workspace_id):
        api_logger.warning(
            f"用户 {current_user.username} 尝试查询不属于工作空间 {workspace_id} 的终端用户 {end_user_id}"
        )
        return fail(BizCode.PERMISSION_DENIED, "该终端用户不属于当前工作空间", "end_user workspace mismatch")

    result = user_memory_service.get_end_user_info(db, end_user_id)

    if result["success"]:
        api_logger.info(f"成功查询终端用户信息: end_user_id={end_user_id}")
        return success(data=result["data"], msg="查询成功")
    else:
        error_msg = result["error"]
        api_logger.error(f"查询终端用户信息失败: end_user_id={end_user_id}, error={error_msg}")

        if error_msg == "终端用户信息记录不存在":
            return fail(BizCode.USER_NOT_FOUND, "终端用户信息记录不存在", error_msg)
        elif error_msg == "无效的终端用户ID格式":
            return fail(BizCode.INVALID_USER_ID, "无效的终端用户ID格式", error_msg)
        else:
            return fail(BizCode.INTERNAL_ERROR, "查询终端用户信息失败", error_msg)


@router.post("/info/update", response_model=ApiResponse)
async def update_end_user_info(
    info_update: EndUserInfoUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    更新终端用户信息记录

    根据 end_user_id 更新终端用户信息记录，支持批量更新多个别名。
    """
    workspace_id = current_user.current_workspace_id
    end_user_id = info_update.end_user_id

    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试更新终端用户信息但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    api_logger.info(
        f"更新终端用户信息请求: end_user_id={end_user_id}, user={current_user.username}, "
        f"workspace={workspace_id}"
    )

    # 校验 end_user 是否属于当前工作空间
    end_user_repo = EndUserRepository(db)
    end_user = end_user_repo.get_end_user_by_id(end_user_id)
    if end_user is None:
        return fail(BizCode.USER_NOT_FOUND, "终端用户不存在", "end_user not found")
    if str(end_user.workspace_id) != str(workspace_id):
        api_logger.warning(
            f"用户 {current_user.username} 尝试更新不属于工作空间 {workspace_id} 的终端用户 {end_user_id}"
        )
        return fail(BizCode.PERMISSION_DENIED, "该终端用户不属于当前工作空间", "end_user workspace mismatch")

    # 获取更新数据（排除 end_user_id）
    update_data = info_update.model_dump(exclude_unset=True, exclude={'end_user_id'})

    result = user_memory_service.update_end_user_info(db, end_user_id, update_data)

    if result["success"]:
        api_logger.info(f"成功更新终端用户信息: end_user_id={end_user_id}")
        return success(data=result["data"], msg="更新成功")
    else:
        error_msg = result["error"]
        api_logger.error(f"终端用户信息更新失败: end_user_id={end_user_id}, error={error_msg}")

        if error_msg == "终端用户信息记录不存在":
            return fail(BizCode.USER_NOT_FOUND, "终端用户信息记录不存在", error_msg)
        elif error_msg == "无效的终端用户ID格式":
            return fail(BizCode.INVALID_USER_ID, "无效的终端用户ID格式", error_msg)
        else:
            return fail(BizCode.INTERNAL_ERROR, "终端用户信息更新失败", error_msg)
