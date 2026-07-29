"""当前租户配额使用情况接口。"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.quota_manager import get_quota_usage
from app.core.response_utils import success
from app.db import get_db
from app.dependencies import get_current_user
from app.models.user_model import User
from app.schemas.response_schema import ApiResponse

router = APIRouter(prefix="/tenants", tags=["Tenant"])


@router.get(
    "/quota/usage",
    response_model=ApiResponse,
    summary="获取当前租户的配额使用情况",
)
async def get_current_tenant_quota_usage(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """根据当前登录用户所属租户返回各类配额的使用量和上限。"""
    usage = await get_quota_usage(db, current_user.tenant_id)
    return success(data=usage)
