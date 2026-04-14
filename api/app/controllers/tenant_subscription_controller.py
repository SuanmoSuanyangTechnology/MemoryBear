"""
租户套餐查询接口（普通用户可访问）
"""
from typing import Callable

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.logging_config import get_api_logger
from app.core.response_utils import success, fail
from app.db import get_db
from app.dependencies import get_current_user
from app.i18n.dependencies import get_translator
from app.models.user_model import User
from app.schemas.response_schema import ApiResponse

logger = get_api_logger()

router = APIRouter(prefix="/tenant", tags=["Tenant"])


@router.get("/subscription", response_model=ApiResponse, summary="获取当前用户所属租户的套餐信息")
async def get_my_tenant_subscription(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    t: Callable = Depends(get_translator),
):
    """
    获取当前登录用户所属租户的有效套餐订阅信息。
    包含套餐名称、版本、配额、到期时间等。
    """
    try:
        from premium.platform_admin.package_plan_service import TenantSubscriptionService

        if not current_user.tenant:
            return JSONResponse(status_code=404, content=fail(code=404, msg="用户未关联租户"))

        tenant_id = current_user.tenant.id
        svc = TenantSubscriptionService(db)
        sub = svc.get_subscription(tenant_id)

        if not sub:
            return success(data=None, msg="暂无有效套餐")

        return success(data=svc.build_response(sub))

    except ModuleNotFoundError:
        # 社区版无 premium 模块，返回空
        return success(data=None, msg="套餐功能未启用")
    except Exception as e:
        logger.error(f"获取租户套餐信息失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content=fail(code=500, msg="获取套餐信息失败"))
