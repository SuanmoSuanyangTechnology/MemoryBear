"""
租户套餐查询接口（普通用户可访问）
"""
import datetime
from typing import Callable, Optional

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
public_router = APIRouter(tags=["Tenant"])


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
            # 无订阅记录时，兜底返回免费套餐信息
            free_plan = svc.plan_repo.get_free_plan()
            if not free_plan:
                return success(data=None, msg="暂无有效套餐")
            return success(data={
                "subscription_id": None,
                "tenant_id": str(tenant_id),
                "package_plan_id": str(free_plan.id),
                "package_version": free_plan.version,
                "package_plan": {
                    "id": str(free_plan.id),
                    "name": free_plan.name,
                    "name_en": free_plan.name_en,
                    "version": free_plan.version,
                    "category": free_plan.category,
                    "tier_level": free_plan.tier_level,
                    "price": float(free_plan.price) if free_plan.price is not None else 0.0,
                    "billing_cycle": free_plan.billing_cycle,
                    "core_value": free_plan.core_value,
                    "core_value_en": free_plan.core_value_en,
                    "tech_support": free_plan.tech_support,
                    "tech_support_en": free_plan.tech_support_en,
                    "sla_compliance": free_plan.sla_compliance,
                    "sla_compliance_en": free_plan.sla_compliance_en,
                    "page_customization": free_plan.page_customization,
                    "page_customization_en": free_plan.page_customization_en,
                    "theme_color": free_plan.theme_color,
                },
                "started_at": None,
                "expired_at": None,
                "status": "active",
                "quotas": free_plan.quotas or {},
                "created_at": int(datetime.datetime.utcnow().timestamp() * 1000),
                "updated_at": int(datetime.datetime.utcnow().timestamp() * 1000),
            }, msg="免费套餐")

        return success(data=svc.build_response(sub))

    except ModuleNotFoundError:
        # 社区版无 premium 模块，从配置文件读取免费套餐
        if not current_user.tenant:
            return JSONResponse(status_code=404, content=fail(code=404, msg="用户未关联租户"))

        from app.config.default_free_plan import DEFAULT_FREE_PLAN

        plan = DEFAULT_FREE_PLAN
        response_data = {
            "subscription_id": None,
            "tenant_id": str(current_user.tenant.id),
            "package_plan_id": None,
            "package_version": plan["version"],
            "package_plan": {
                "id": None,
                "name": plan["name"],
                "name_en": plan.get("name_en"),
                "version": plan["version"],
                "category": plan["category"],
                "tier_level": plan["tier_level"],
                "price": float(plan["price"]),
                "billing_cycle": plan["billing_cycle"],
                "core_value": plan.get("core_value"),
                "core_value_en": plan.get("core_value_en"),
                "tech_support": plan.get("tech_support"),
                "tech_support_en": plan.get("tech_support_en"),
                "sla_compliance": plan.get("sla_compliance"),
                "sla_compliance_en": plan.get("sla_compliance_en"),
                "page_customization": plan.get("page_customization"),
                "page_customization_en": plan.get("page_customization_en"),
                "theme_color": plan.get("theme_color"),
            },
            "started_at": None,
            "expired_at": None,
            "status": "active",
            "quotas": plan["quotas"],
            "created_at": int(datetime.datetime.utcnow().timestamp() * 1000),
            "updated_at": int(datetime.datetime.utcnow().timestamp() * 1000),
        }
        return success(data=response_data, msg="社区版免费套餐")

    except Exception as e:
        logger.error(f"获取租户套餐信息失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content=fail(code=500, msg="获取套餐信息失败"))


@public_router.get("/package-plans", response_model=ApiResponse, summary="获取套餐列表（公开）")
async def list_package_plans_public(
    category: Optional[str] = None,
    status: Optional[bool] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    公开接口，无需鉴权。
    SaaS 版从数据库读取套餐列表；社区版降级返回 default_free_plan.py 中的免费套餐。
    """
    try:
        from premium.platform_admin.package_plan_service import PackagePlanService
        from premium.platform_admin.package_plan_schema import PackagePlanResponse
        svc = PackagePlanService(db)
        result = svc.get_list(page=1, size=9999, category=category, status=status, search=search)
        return success(data=[PackagePlanResponse.model_validate(p).model_dump(mode="json") for p in result["items"]])
    except ModuleNotFoundError:
        from app.config.default_free_plan import DEFAULT_FREE_PLAN
        plan = DEFAULT_FREE_PLAN
        return success(data=[{
            "id": None,
            "name": plan["name"],
            "name_en": plan.get("name_en"),
            "version": plan["version"],
            "category": plan["category"],
            "tier_level": plan["tier_level"],
            "price": float(plan["price"]),
            "billing_cycle": plan["billing_cycle"],
            "core_value": plan.get("core_value"),
            "core_value_en": plan.get("core_value_en"),
            "tech_support": plan.get("tech_support"),
            "tech_support_en": plan.get("tech_support_en"),
            "sla_compliance": plan.get("sla_compliance"),
            "sla_compliance_en": plan.get("sla_compliance_en"),
            "page_customization": plan.get("page_customization"),
            "page_customization_en": plan.get("page_customization_en"),
            "theme_color": plan.get("theme_color"),
            "status": plan.get("status", True),
            "quotas": plan["quotas"],
        }])
    except Exception as e:
        logger.error(f"获取套餐列表失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content=fail(code=500, msg="获取套餐列表失败"))
