"""模型配额检查（D-M7-4：老单体 `app/core/quota_manager.py` 模型段 + premium 配额解析的
服务化迁移）。

配额口径（与宿主逐键一致）：
- 套餐额度：生效订阅（`tenant_subscriptions` status='active' ⋈ `package_plans`，取
  `tier_level` 最高）锁定的 `package_plan_versions.version_snapshot`；快照缺失回落
  `package_plans.quotas`；无订阅降级免费套餐（`QUOTA_<KEY>` 环境变量可覆盖）。
- 资源包额度：租户 active 且未过期实例 ⋈ 锁定版本快照，按实例 `tier_id` 命中的 tier
  `quota_grants[key] × quantity` 求和；与套餐额度逐键相加。
- 用量口径：启用中的组合模型数（组合是唯一占 model_quota 的形态）。

premium 表缺失（社区部署）时只回滚 SAVEPOINT 并降级免费套餐，不回滚调用方事务。
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import Callable
from functools import wraps
from typing import Any

from sqlalchemy import or_
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from ..errors import InternalServerError, QuotaExceededError
from ..models.models_model import ModelConfig, ModelProvider
from ..models.references.package_plan_model import PackagePlan, PackagePlanVersion
from ..models.references.resource_pack_model import ResourcePackVersion, TenantResourcePack
from ..models.references.subscription_model import ACTIVE_STATUS, TenantSubscription
from ..utils.datetime_utils import utcnow_naive

logger = logging.getLogger(__name__)

# 旧格式快照（配额字段平铺顶层）的取值键，与宿主 premium 侧 _QUOTA_KEYS 同序。
_QUOTA_KEYS = (
    "workspace_quota",
    "skill_quota",
    "app_quota",
    "knowledge_capacity_quota",
    "memory_engine_quota",
    "end_user_quota",
    "ontology_project_quota",
    "model_quota",
    "api_ops_rate_limit",
)

# 免费套餐兜底（口径同宿主 app/config/default_free_plan.py 的 quotas）。
_FREE_PLAN_QUOTAS: dict[str, Any] = {
    "workspace_quota": 1,
    "skill_quota": 5,
    "app_quota": 2,
    "knowledge_capacity_quota": 0.3,
    "memory_engine_quota": 1,
    "end_user_quota": 10,
    "ontology_project_quota": 3,
    "model_quota": 1,
    "api_ops_rate_limit": 50,
    "end_user_memory_limit": 300,
    "pre_user_memory_write_qps_limit": 50,
}

_ENV_OVERRIDABLE_KEYS = (
    *_QUOTA_KEYS,
    "end_user_memory_limit",
    "pre_user_memory_write_qps_limit",
)


def _env_quota_overrides() -> dict[str, Any]:
    """QUOTA_<KEY> 环境变量覆盖免费套餐（非法值静默忽略，与宿主同口径）。"""
    quotas: dict[str, Any] = {}
    for key in _ENV_OVERRIDABLE_KEYS:
        raw = os.getenv(f"QUOTA_{key.upper()}")
        if raw is None:
            continue
        try:
            quotas[key] = float(raw) if "." in raw else int(raw)
        except ValueError:
            continue
    return quotas


def _free_plan_quotas() -> dict[str, Any]:
    return {**_FREE_PLAN_QUOTAS, **_env_quota_overrides()}


def _latest_active_subscription(db: Session, tenant_id: uuid.UUID) -> TenantSubscription | None:
    """生效订阅：active 且套餐等级最高（多订阅时取最高档）。"""
    return (
        db.query(TenantSubscription)
        .join(PackagePlan, TenantSubscription.package_plan_id == PackagePlan.id)
        .filter(
            TenantSubscription.tenant_id == tenant_id,
            TenantSubscription.status == ACTIVE_STATUS,
        )
        .order_by(PackagePlan.tier_level.desc())
        .first()
    )


def _quota_from_snapshot(db: Session, subscription: TenantSubscription) -> dict[str, Any]:
    """订阅锁定版本的配额；快照缺失回落当前套餐配置。"""
    version = (
        db.query(PackagePlanVersion)
        .filter(
            PackagePlanVersion.package_plan_id == subscription.package_plan_id,
            PackagePlanVersion.version == subscription.package_version,
        )
        .first()
    )
    if version is not None and version.version_snapshot:
        snapshot = version.version_snapshot
        if "quotas" in snapshot:
            quotas = snapshot["quotas"]
        else:
            quotas = {key: snapshot.get(key) for key in _QUOTA_KEYS}
    else:
        plan = (
            db.query(PackagePlan)
            .filter(PackagePlan.id == subscription.package_plan_id)
            .first()
        )
        quotas = dict(plan.quotas or {}) if plan else {}
        capacity = quotas.get("knowledge_capacity_quota")
        if capacity is not None:
            quotas["knowledge_capacity_quota"] = float(capacity)
    quotas.setdefault(
        "pre_user_memory_write_qps_limit",
        int(os.getenv("QUOTA_PRE_USER_MEMORY_WRITE_QPS_LIMIT", "100")),
    )
    quotas.setdefault(
        "end_user_memory_limit",
        int(os.getenv("QUOTA_END_USER_MEMORY_LIMIT", "600")),
    )
    return quotas


def _effective_subscription_quota(db: Session, tenant_id: uuid.UUID) -> dict[str, Any]:
    subscription = _latest_active_subscription(db, tenant_id)
    if subscription is None:
        return {}
    return _quota_from_snapshot(db, subscription)


def _resource_pack_overlay(db: Session, tenant_id: uuid.UUID) -> dict[str, Any]:
    """活跃资源包叠加：实例 tier 的 quota_grants × quantity 逐键求和。"""
    now = utcnow_naive()
    rows = (
        db.query(
            ResourcePackVersion.version_snapshot,
            TenantResourcePack.tier_id,
            TenantResourcePack.quantity,
        )
        .join(
            TenantResourcePack,
            TenantResourcePack.resource_pack_version_id == ResourcePackVersion.id,
        )
        .filter(
            TenantResourcePack.tenant_id == tenant_id,
            TenantResourcePack.status == ACTIVE_STATUS,
            or_(
                TenantResourcePack.expired_at.is_(None),
                TenantResourcePack.expired_at > now,
            ),
        )
        .all()
    )
    overlay: dict[str, Any] = {}
    for version_snapshot, tier_id, quantity in rows:
        selected = next(
            (
                tier
                for tier in ((version_snapshot or {}).get("tiers") or [])
                if str(tier.get("tier_id")) == str(tier_id)
            ),
            None,
        )
        if not selected:
            continue
        for key, value in (selected.get("quota_grants") or {}).items():
            overlay[key] = overlay.get(key, 0) + (value or 0) * quantity
    return overlay


def _merge_quota_overlay(base: dict[str, Any] | None, overlay: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base or {})
    for key, value in overlay.items():
        merged[key] = (merged.get(key) or 0) + (value or 0)
    return merged


def _missing_premium_schema(error: ProgrammingError) -> bool:
    return getattr(error.orig, "pgcode", None) == "42P01"


def _quota_breakdown(db: Session, tenant_id: uuid.UUID) -> tuple[dict[str, Any], dict[str, Any]]:
    """(套餐额度, 资源包额度)；premium 表缺失时回滚 SAVEPOINT 并降级免费套餐。"""
    try:
        with db.begin_nested():
            base = _effective_subscription_quota(db, tenant_id)
            overlay = _resource_pack_overlay(db, tenant_id)
    except ProgrammingError as error:
        if not _missing_premium_schema(error):
            raise
        logger.warning("premium 配额表不存在，已回滚 SAVEPOINT 并使用免费套餐配额")
        return _free_plan_quotas(), {}
    if not base:
        logger.debug("租户 %s 无有效订阅，降级到免费套餐", tenant_id)
        base = _free_plan_quotas()
    return base, overlay


def get_quota_config(db: Session, tenant_id: uuid.UUID) -> dict[str, Any]:
    """生效配额 = 套餐额度（或免费套餐）+ 活跃资源包额度。"""
    base, overlay = _quota_breakdown(db, tenant_id)
    if not overlay:
        return dict(base) if base else base
    return _merge_quota_overlay(base, overlay)


def count_models(db: Session, tenant_id: uuid.UUID) -> int:
    """model_quota 用量：启用中的组合模型数。"""
    return (
        db.query(ModelConfig)
        .filter(
            ModelConfig.tenant_id == tenant_id,
            ModelConfig.is_active.is_(True),
            ModelConfig.provider == ModelProvider.COMPOSITE,
        )
        .count()
    )


def _check_quota(db: Session, tenant_id: uuid.UUID, quota_type: str, resource_name: str) -> None:
    """核心配额检查：配额缺失/未含该键时放行（warning），超限抛 402。"""
    try:
        quota_config = get_quota_config(db, tenant_id)
        if not quota_config:
            logger.warning("租户 %s 无有效配额配置，跳过配额检查", tenant_id)
            return

        quota_limit = quota_config.get(quota_type)
        if quota_limit is None:
            logger.warning("配额配置未包含 %s，跳过配额检查", quota_type)
            return

        current_usage = count_models(db, tenant_id)
        if current_usage >= quota_limit:
            logger.warning(
                "配额不足: tenant=%s, type=%s, usage=%s, limit=%s",
                tenant_id,
                quota_type,
                current_usage,
                quota_limit,
            )
            raise QuotaExceededError(
                resource=resource_name,
                current_usage=current_usage,
                quota_limit=quota_limit,
            )

        logger.debug(
            "配额检查通过: tenant=%s, type=%s, usage=%s, limit=%s",
            tenant_id,
            quota_type,
            current_usage,
            quota_limit,
        )
    except QuotaExceededError:
        raise
    except Exception as exc:
        logger.error(
            "配额检查异常: tenant=%s, type=%s, error_type=%s, error=%s",
            tenant_id,
            quota_type,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        raise


def _decorator_context(kwargs: dict) -> tuple[Session | None, uuid.UUID | None]:
    db: Session | None = kwargs.get("db")
    principal = kwargs.get("principal")
    tenant_id = getattr(principal, "tenant_id", None)
    return db, tenant_id


def check_model_quota(func: Callable) -> Callable:
    """创建组合模型前的 model_quota 准入（装饰器签名同宿主：db/principal 走 kwargs）。"""

    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        db, tenant_id = _decorator_context(kwargs)
        if db is None or tenant_id is None:
            logger.error("配额检查失败：%s 缺少 db 或 principal 参数，拒绝请求", func.__name__)
            raise InternalServerError()
        _check_quota(db, tenant_id, "model_quota", "model")
        return await func(*args, **kwargs)

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        db, tenant_id = _decorator_context(kwargs)
        if db is None or tenant_id is None:
            logger.error("配额检查失败：%s 缺少 db 或 principal 参数，拒绝请求", func.__name__)
            raise InternalServerError()
        _check_quota(db, tenant_id, "model_quota", "model")
        return func(*args, **kwargs)

    return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper


def check_model_activation_quota(func: Callable) -> Callable:
    """模型由禁用转启用时才计入 model_quota（更新组合模型用）。"""

    def _check(args: tuple, kwargs: dict) -> None:
        db, tenant_id = _decorator_context(kwargs)
        if db is None or tenant_id is None:
            logger.error("配额检查失败：%s 缺少 db 或 principal 参数，拒绝请求", func.__name__)
            raise InternalServerError()

        # 位置参数兜底：路由以关键字传 model_id/model_data，此处兼容直调
        model_id = kwargs.get("model_id") or (args[1] if len(args) > 1 else None)
        model_data = kwargs.get("model_data")
        if not model_id or not model_data:
            logger.warning("模型激活配额检查失败：缺少 model_id 或 model_data 参数")
            return
        if not model_data.is_active:
            return

        from .model_service import ModelConfigService

        try:
            existing_model = ModelConfigService.get_model_by_id(
                db=db, model_id=model_id, tenant_id=tenant_id
            )
            if not existing_model.is_active:
                logger.info(
                    "模型激活操作，检查配额: model_id=%s, tenant_id=%s", model_id, tenant_id
                )
                _check_quota(db, tenant_id, "model_quota", "model")
        except Exception as exc:
            logger.error("模型激活配额检查异常: model_id=%s, error=%s", model_id, exc)
            raise

    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        _check(args, kwargs)
        return await func(*args, **kwargs)

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        _check(args, kwargs)
        return func(*args, **kwargs)

    return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper


__all__ = [
    "check_model_activation_quota",
    "check_model_quota",
    "count_models",
    "get_quota_config",
]
