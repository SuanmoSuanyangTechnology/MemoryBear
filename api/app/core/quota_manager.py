"""
统一配额管理器 - 社区版和 SaaS 版共用

配额来源策略：
1. 优先从 premium 模块的 tenant_subscriptions 表读取（SaaS 版）
2. 降级到 default_free_plan.py 配置文件（社区版兜底）
"""
import asyncio
from functools import wraps
from typing import Optional, Callable, Dict, Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.logging_config import get_auth_logger
from app.i18n.exceptions import QuotaExceededError, InternalServerError
from app.utils.redis_cache import redis_cache

logger = get_auth_logger()

# Redis key 格式常量，与 RateLimiterService.check_qps 保持一致（per api_key 独立计数）
API_KEY_QPS_REDIS_KEY = "rate_limit:qps:{api_key_id}"


def _get_user_from_kwargs(kwargs: dict):
    """从 kwargs 中获取 user 对象"""
    for key in ["user", "current_user"]:
        if key in kwargs:
            return kwargs[key]
    return None


def _get_workspace_id_from_kwargs(kwargs: dict):
    """从 kwargs 中获取 workspace_id"""
    # 优先从 kwargs['workspace_id'] 获取
    workspace_id = kwargs.get("workspace_id")
    if workspace_id:
        return workspace_id

    # 从 api_key_auth.workspace_id 获取（API Key 认证场景）
    api_key_auth = kwargs.get("api_key_auth")
    if api_key_auth and hasattr(api_key_auth, 'workspace_id'):
        return api_key_auth.workspace_id

    # 从 user.current_workspace_id 获取
    user = _get_user_from_kwargs(kwargs)
    if user:
        ws_id = getattr(user, 'current_workspace_id', None)
        if ws_id:
            return ws_id

    logger.warning(f"无法获取 workspace_id, kwargs keys: {list(kwargs.keys())}")
    return None


def _get_tenant_id_from_kwargs(db: Session, kwargs: dict):
    """从 kwargs 中获取 tenant_id"""
    user = _get_user_from_kwargs(kwargs)
    if user and hasattr(user, 'tenant_id'):
        return user.tenant_id

    workspace_id = kwargs.get("workspace_id")
    if workspace_id:
        from app.models.workspace_model import Workspace
        workspace = db.query(Workspace).filter(Workspace.id == workspace_id).first()
        if workspace:
            return workspace.tenant_id

    api_key_auth = kwargs.get("api_key_auth")
    if api_key_auth and hasattr(api_key_auth, 'workspace_id'):
        from app.models.workspace_model import Workspace
        workspace = db.query(Workspace).filter(Workspace.id == api_key_auth.workspace_id).first()
        if workspace:
            return workspace.tenant_id

    data = kwargs.get("data") or kwargs.get("body") or kwargs.get("payload")
    if data and hasattr(data, "workspace_id"):
        from app.models.workspace_model import Workspace
        workspace = db.query(Workspace).filter(Workspace.id == data.workspace_id).first()
        if workspace:
            return workspace.tenant_id

    share_data = kwargs.get("share_data")
    if share_data and hasattr(share_data, 'share_token'):
        from app.models.workspace_model import Workspace
        from app.models.app_model import App
        share_token = share_data.share_token
        from app.models.release_share_model import ReleaseShare
        share_record = db.query(ReleaseShare).filter(ReleaseShare.share_token == share_token).first()
        if share_record:
            app = db.query(App).filter(App.id == share_record.app_id, App.is_active.is_(True)).first()
            if app:
                workspace = db.query(Workspace).filter(Workspace.id == app.workspace_id).first()
                if workspace:
                    return workspace.tenant_id

    return None


async def _get_tenant_id_from_kwargs_async(db: AsyncSession, kwargs: dict):
    """Resolve tenant context through AsyncSession without sync DB access in the event loop."""
    return await db.run_sync(lambda sync_db: _get_tenant_id_from_kwargs(sync_db, kwargs))


# 按工作空间计量的配额：套餐额度与资源包额度都是「每个工作空间」的上限
# （与 :func:`_check_quota` 的口径一致），租户总额度需要乘以活跃空间数。
PER_WORKSPACE_QUOTA_KEYS = frozenset({
    "app_quota",
    "knowledge_capacity_quota",
    "memory_engine_quota",
    "end_user_quota",
    "ontology_project_quota",
})


def _merge_quota_overlay(base: Optional[Dict[str, Any]], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Return a new quota mapping with every numeric resource-pack grant added."""
    merged: Dict[str, Any] = dict(base or {})
    for key, value in overlay.items():
        merged[key] = (merged.get(key) or 0) + (value or 0)
    return merged


def _free_quota_config() -> Optional[Dict[str, Any]]:
    try:
        from app.config.default_free_plan import DEFAULT_FREE_PLAN

        return DEFAULT_FREE_PLAN.get("quotas")
    except Exception as e:
        logger.error(f"无法从配置文件获取配额: {e}")
        return None


def _get_quota_breakdown(
        db: Session, tenant_id: UUID,
) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Return (套餐额度, 资源包额度) so callers can aggregate them separately."""
    try:
        from premium.platform_admin.package_plan_service import TenantSubscriptionService
        from premium.platform_admin.resource_pack_service import ResourcePackService

        base = TenantSubscriptionService(db).get_effective_quota(tenant_id)
        if not base:
            logger.debug(f"租户 {tenant_id} 无 premium 订阅，降级到免费套餐")
            base = _free_quota_config()
        return base, ResourcePackService(db).get_overlay(tenant_id)
    except (ModuleNotFoundError, ImportError):
        logger.debug("premium 模块不存在，使用社区版免费套餐配额")
        return _free_quota_config(), {}


async def _get_quota_breakdown_async(
        db: AsyncSession, tenant_id: UUID,
) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Async equivalent of :func:`_get_quota_breakdown`."""
    try:
        from premium.platform_admin.package_plan_service import TenantSubscriptionService
        from premium.platform_admin.resource_pack_service import ResourcePackService

        base = await TenantSubscriptionService(db).get_effective_quota_async(tenant_id)
        if not base:
            logger.debug(f"租户 {tenant_id} 无 premium 订阅，降级到免费套餐")
            base = _free_quota_config()
        return base, await ResourcePackService.get_overlay_async(db, tenant_id)
    except (ModuleNotFoundError, ImportError):
        logger.debug("premium 模块不存在，使用社区版免费套餐配额")
        return _free_quota_config(), {}


def _get_quota_config(db: Session, tenant_id: UUID) -> Optional[Dict[str, Any]]:
    """Get final quota = effective package (or free fallback) + active packs."""
    base, overlay = _get_quota_breakdown(db, tenant_id)
    if not overlay:
        return dict(base) if base else base
    return _merge_quota_overlay(base, overlay)


async def _get_quota_config_async(db: AsyncSession, tenant_id: UUID) -> Optional[Dict[str, Any]]:
    """Async equivalent of :func:`_get_quota_config`."""
    base, overlay = await _get_quota_breakdown_async(db, tenant_id)
    if not overlay:
        return dict(base) if base else base
    return _merge_quota_overlay(base, overlay)


def get_api_ops_rate_limit(db: Session, tenant_id: UUID) -> Optional[int]:
    """
    获取租户套餐的 API 操作速率限制（QPS 上限）

    该函数兼容社区版和 SaaS 版：
    - SaaS 版：从 premium 模块的套餐配额读取
    - 社区版：从 default_free_plan.py 配置文件读取

    Returns:
        int: api_ops_rate_limit 值，如果未配置则返回 None
    """
    quota_config = _get_quota_config(db, tenant_id)
    if quota_config:
        return quota_config.get("api_ops_rate_limit")
    return None


async def get_api_ops_rate_limit_async(db, tenant_id: UUID) -> Optional[int]:
    """Async version of get_api_ops_rate_limit."""
    quota_config = await _get_quota_config_async(db, tenant_id)
    if quota_config:
        return quota_config.get("api_ops_rate_limit")
    return None


@redis_cache(ttl=300, prefix='quota', skip_args=['db'], id_arg='tenant_id')
def get_end_user_memory_limit(db: Session, tenant_id: UUID) -> Optional[int]:
    quota_config = _get_quota_config(db, tenant_id)
    if quota_config:
        return quota_config.get("end_user_memory_limit")
    return None


@redis_cache(ttl=300, prefix='quota', skip_args=['db'], id_arg='tenant_id')
async def get_end_user_memory_limit_async(db: AsyncSession, tenant_id: UUID) -> Optional[int]:
    quota_config = await _get_quota_config_async(db, tenant_id)
    if quota_config:
        return quota_config.get("end_user_memory_limit")
    return None


@redis_cache(ttl=300, prefix='quota', skip_args=['db'], id_arg='tenant_id')
async def get_pre_user_memory_write_ops_limit(db: AsyncSession, tenant_id: UUID) -> Optional[int]:
    quota_config = await _get_quota_config_async(db, tenant_id)
    if quota_config:
        return quota_config.get("pre_user_memory_write_qps_limit")
    return None


class QuotaUsageRepository:
    """配额使用量数据访问层"""

    def __init__(self, db: Session):
        self.db = db

    def count_workspaces(self, tenant_id: UUID) -> int:
        from app.models.workspace_model import Workspace
        return self.db.query(Workspace).filter(
            Workspace.tenant_id == tenant_id,
            Workspace.is_active.is_(True)
        ).count()

    def count_apps(self, tenant_id: UUID, workspace_id: Optional[UUID] = None) -> int:
        from app.models.app_model import App
        from app.models.workspace_model import Workspace
        query = self.db.query(App).join(
            Workspace, App.workspace_id == Workspace.id
        ).filter(
            App.is_active.is_(True)
        )
        if workspace_id:
            query = query.filter(App.workspace_id == workspace_id)
        else:
            query = query.filter(Workspace.tenant_id == tenant_id)
        return query.count()

    def count_skills(self, tenant_id: UUID) -> int:
        from app.models.skill_model import Skill
        return self.db.query(Skill).filter(
            Skill.tenant_id == tenant_id,
            Skill.is_active.is_(True)
        ).count()

    def sum_knowledge_capacity_gb(self, tenant_id: UUID, workspace_id: Optional[UUID] = None) -> float:
        from app.models.document_model import Document
        from app.models.knowledge_model import Knowledge
        from app.models.workspace_model import Workspace
        query = self.db.query(func.coalesce(func.sum(Document.file_size), 0)).join(
            Knowledge, Document.kb_id == Knowledge.id
        ).join(
            Workspace, Knowledge.workspace_id == Workspace.id
        ).filter(
            Document.status == 1,
        )
        if workspace_id:
            query = query.filter(Knowledge.workspace_id == workspace_id)
        else:
            query = query.filter(Workspace.tenant_id == tenant_id)
        result = query.scalar()
        return float(result) / (1024 ** 3) if result else 0.0

    def count_memory_engines(self, tenant_id: UUID, workspace_id: Optional[UUID] = None) -> int:
        from app.models.memory_config_model import MemoryConfig
        from app.models.workspace_model import Workspace
        query = self.db.query(MemoryConfig).join(
            Workspace, MemoryConfig.workspace_id == Workspace.id
        )
        if workspace_id:
            query = query.filter(MemoryConfig.workspace_id == workspace_id)
        else:
            query = query.filter(Workspace.tenant_id == tenant_id)
        return query.count()

    def count_end_users(self, tenant_id: UUID, workspace_id: Optional[UUID] = None) -> int:
        from app.models.end_user_model import EndUser
        from app.models.workspace_model import Workspace
        from app.models.user_model import User
        query = self.db.query(EndUser).join(
            Workspace, EndUser.workspace_id == Workspace.id
        ).filter(EndUser.is_active == True)
        if workspace_id:
            query = query.filter(EndUser.workspace_id == workspace_id)
        else:
            query = query.filter(Workspace.tenant_id == tenant_id)
        trial_user_ids = [
            str(u.id) for u in self.db.query(User.id).filter(User.tenant_id == tenant_id).all()
        ]
        if trial_user_ids:
            query = query.filter(~EndUser.other_id.in_(trial_user_ids))
        return query.count()

    async def count_end_users_async(self, tenant_id: UUID, workspace_id: Optional[UUID] = None) -> int:
        from app.models.end_user_model import EndUser
        from app.models.workspace_model import Workspace
        from app.models.user_model import User

        trial_user_ids = [
            str(user_id)
            for user_id in (
                await self.db.scalars(
                    select(User.id).where(User.tenant_id == tenant_id)
                )
            ).all()
        ]

        stmt = (
            select(func.count())
            .select_from(EndUser)
            .join(Workspace, EndUser.workspace_id == Workspace.id)
            .where(EndUser.is_active == True)
        )
        if workspace_id:
            stmt = stmt.where(EndUser.workspace_id == workspace_id)
        else:
            stmt = stmt.where(Workspace.tenant_id == tenant_id)
        if trial_user_ids:
            stmt = stmt.where(~EndUser.other_id.in_(trial_user_ids))

        return int((await self.db.scalar(stmt)) or 0)

    def count_models(self, tenant_id: UUID) -> int:
        from app.models.models_model import ModelConfig
        return self.db.query(ModelConfig).filter(
            ModelConfig.tenant_id == tenant_id,
            ModelConfig.is_active == True,
            ModelConfig.is_composite == True
        ).count()

    def count_ontology_projects(self, tenant_id: UUID, workspace_id: Optional[UUID] = None) -> int:
        from app.models.ontology_scene import OntologyScene
        from app.models.workspace_model import Workspace
        if workspace_id:
            return self.db.query(OntologyScene).filter(
                OntologyScene.workspace_id == workspace_id
            ).count()
        return self.db.query(OntologyScene).join(
            Workspace, OntologyScene.workspace_id == Workspace.id
        ).filter(
            Workspace.tenant_id == tenant_id
        ).count()

    def get_usage_by_quota_type(self, tenant_id: UUID, quota_type: str, workspace_id: Optional[UUID] = None):
        """按配额类型分发，返回当前使用量"""
        dispatch = {
            "workspace_quota": self.count_workspaces,
            "app_quota": self.count_apps,
            "skill_quota": self.count_skills,
            "knowledge_capacity_quota": self.sum_knowledge_capacity_gb,
            "memory_engine_quota": self.count_memory_engines,
            "end_user_quota": self.count_end_users,
            "model_quota": self.count_models,
            "ontology_project_quota": self.count_ontology_projects,
        }
        fn = dispatch.get(quota_type)
        if workspace_id:
            return fn(tenant_id, workspace_id) if fn else 0
        return fn(tenant_id) if fn else 0


def _check_quota(
        db: Session,
        tenant_id: UUID,
        quota_type: str,
        resource_name: str,
        usage_func: Optional[Callable] = None,
        workspace_id: Optional[UUID] = None,
) -> None:
    """核心配额检查逻辑：对比使用量和配额限制"""
    try:
        quota_config = _get_quota_config(db, tenant_id)
        if not quota_config:
            logger.warning(f"租户 {tenant_id} 无有效配额配置，跳过配额检查")
            return

        quota_limit = quota_config.get(quota_type)
        if quota_limit is None:
            logger.warning(f"配额配置未包含 {quota_type}，跳过配额检查")
            return

        if usage_func:
            current_usage = usage_func(db, tenant_id, workspace_id) if workspace_id else usage_func(db, tenant_id)
        else:
            current_usage = QuotaUsageRepository(db).get_usage_by_quota_type(tenant_id, quota_type, workspace_id)

        if current_usage >= quota_limit:
            logger.warning(
                f"配额不足: tenant={tenant_id}, workspace={workspace_id}, type={quota_type}, "
                f"usage={current_usage}, limit={quota_limit}"
            )
            raise QuotaExceededError(
                resource=resource_name,
                current_usage=current_usage,
                quota_limit=quota_limit,
            )

        logger.debug(
            f"配额检查通过: tenant={tenant_id}, workspace={workspace_id}, type={quota_type}, "
            f"usage={current_usage}, limit={quota_limit}"
        )

    except QuotaExceededError:
        raise
    except Exception as e:
        logger.error(
            f"配额检查异常: tenant={tenant_id}, workspace={workspace_id}, type={quota_type}, "
            f"error_type={type(e).__name__}, error={str(e)}",
            exc_info=True,
        )
        raise


async def _check_quota_async(
        db: AsyncSession,
        tenant_id: UUID,
        quota_type: str,
        resource_name: str,
        usage_func: Optional[Callable] = None,
        workspace_id: Optional[UUID] = None,
) -> None:
    """Run the existing quota semantics through an AsyncSession greenlet context."""
    await db.run_sync(
        lambda sync_db: _check_quota(
            sync_db,
            tenant_id,
            quota_type,
            resource_name,
            usage_func,
            workspace_id,
        )
    )


async def check_end_user_quota_async(
        db: AsyncSession,
        tenant_id: UUID,
        workspace_id: Optional[UUID] = None,
) -> None:
    """Async 版终端用户配额检查，供 chat 入口等 AsyncSession 主链复用。"""
    quota_config = await _get_quota_config_async(db, tenant_id)
    if not quota_config:
        logger.warning(f"租户 {tenant_id} 无有效配额配置，跳过配额检查")
        return

    quota_limit = quota_config.get("end_user_quota")
    if quota_limit is None:
        logger.warning("配额配置未包含 end_user_quota，跳过配额检查")
        return

    current_usage = await QuotaUsageRepository(db).count_end_users_async(
        tenant_id,
        workspace_id,
    )
    if current_usage >= quota_limit:
        logger.warning(
            f"配额不足: tenant={tenant_id}, workspace={workspace_id}, type=end_user_quota, "
            f"usage={current_usage}, limit={quota_limit}"
        )
        raise QuotaExceededError(
            resource="end_user",
            current_usage=current_usage,
            quota_limit=quota_limit,
        )


# ─── 具名装饰器 ────────────────────────────────────────────────────────────

def check_workspace_quota(func: Callable) -> Callable:
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        db: Session = kwargs.get("db")
        user = _get_user_from_kwargs(kwargs)
        if not db or not user:
            logger.error(f"配额检查失败：{func.__name__} 缺少 db 或 user 参数，拒绝请求")
            raise InternalServerError()
        _check_quota(db, user.tenant_id, "workspace_quota", "workspace")
        return await func(*args, **kwargs)

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        db: Session = kwargs.get("db")
        user = _get_user_from_kwargs(kwargs)
        if not db or not user:
            logger.error(f"配额检查失败：{func.__name__} 缺少 db 或 user 参数，拒绝请求")
            raise InternalServerError()
        _check_quota(db, user.tenant_id, "workspace_quota", "workspace")
        return func(*args, **kwargs)

    return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper


def check_skill_quota(func: Callable) -> Callable:
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        db: Session = kwargs.get("db")
        user = _get_user_from_kwargs(kwargs)
        if not db or not user:
            logger.error(f"配额检查失败：{func.__name__} 缺少 db 或 user 参数，拒绝请求")
            raise InternalServerError()
        _check_quota(db, user.tenant_id, "skill_quota", "skill")
        return await func(*args, **kwargs)

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        db: Session = kwargs.get("db")
        user = _get_user_from_kwargs(kwargs)
        if not db or not user:
            logger.error(f"配额检查失败：{func.__name__} 缺少 db 或 user 参数，拒绝请求")
            raise InternalServerError()
        _check_quota(db, user.tenant_id, "skill_quota", "skill")
        return func(*args, **kwargs)

    return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper


def check_app_quota(func: Callable) -> Callable:
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        db: Session = kwargs.get("db")
        user = _get_user_from_kwargs(kwargs)
        if not db or not user:
            logger.error(f"配额检查失败：{func.__name__} 缺少 db 或 user 参数，拒绝请求")
            raise InternalServerError()
        workspace_id = _get_workspace_id_from_kwargs(kwargs)
        if not workspace_id:
            logger.error(f"配额检查失败：{func.__name__} 无法获取 workspace_id，拒绝请求")
            raise InternalServerError()
        _check_quota(db, user.tenant_id, "app_quota", "app", workspace_id=workspace_id)
        return await func(*args, **kwargs)

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        db: Session = kwargs.get("db")
        user = _get_user_from_kwargs(kwargs)
        if not db or not user:
            logger.error(f"配额检查失败：{func.__name__} 缺少 db 或 user 参数，拒绝请求")
            raise InternalServerError()
        workspace_id = _get_workspace_id_from_kwargs(kwargs)
        if not workspace_id:
            logger.error(f"配额检查失败：{func.__name__} 无法获取 workspace_id，拒绝请求")
            raise InternalServerError()
        _check_quota(db, user.tenant_id, "app_quota", "app", workspace_id=workspace_id)
        return func(*args, **kwargs)

    return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper


def check_knowledge_capacity_quota(func: Callable) -> Callable:
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        db: Session | AsyncSession = kwargs.get("db")
        if not db:
            logger.error(f"配额检查失败：{func.__name__} 缺少 db 参数，拒绝请求")
            raise InternalServerError()
        if isinstance(db, AsyncSession):
            tenant_id = await _get_tenant_id_from_kwargs_async(db, kwargs)
        else:
            tenant_id = _get_tenant_id_from_kwargs(db, kwargs)
        if not tenant_id:
            logger.error(f"配额检查失败：{func.__name__} 无法获取 tenant_id，拒绝请求")
            raise InternalServerError()
        workspace_id = _get_workspace_id_from_kwargs(kwargs)
        if not workspace_id:
            logger.error(f"配额检查失败：{func.__name__} 无法获取 workspace_id，拒绝请求")
            raise InternalServerError()
        if isinstance(db, AsyncSession):
            await _check_quota_async(
                db,
                tenant_id,
                "knowledge_capacity_quota",
                "knowledge_capacity",
                workspace_id=workspace_id,
            )
        else:
            _check_quota(
                db,
                tenant_id,
                "knowledge_capacity_quota",
                "knowledge_capacity",
                workspace_id=workspace_id,
            )
        return await func(*args, **kwargs)

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        db: Session = kwargs.get("db")
        user = _get_user_from_kwargs(kwargs)
        if not db or not user:
            logger.error(f"配额检查失败：{func.__name__} 缺少 db 或 user 参数，拒绝请求")
            raise InternalServerError()
        workspace_id = _get_workspace_id_from_kwargs(kwargs)
        if not workspace_id:
            logger.error(f"配额检查失败：{func.__name__} 无法获取 workspace_id，拒绝请求")
            raise InternalServerError()
        _check_quota(db, user.tenant_id, "knowledge_capacity_quota", "knowledge_capacity", workspace_id=workspace_id)
        return func(*args, **kwargs)

    return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper


def check_memory_engine_quota(func: Callable) -> Callable:
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        db: Session = kwargs.get("db")
        user = _get_user_from_kwargs(kwargs)
        logger.debug(
            f"check_memory_engine_quota async_wrapper: db={db is not None}, user={user}, kwargs_keys={list(kwargs.keys())}")
        if not db or not user:
            logger.error(f"配额检查失败：{func.__name__} 缺少 db 或 user 参数，拒绝请求")
            raise InternalServerError()
        workspace_id = _get_workspace_id_from_kwargs(kwargs)
        if not workspace_id:
            logger.error(f"配额检查失败：{func.__name__} 无法获取 workspace_id，拒绝请求")
            raise InternalServerError()
        _check_quota(db, user.tenant_id, "memory_engine_quota", "memory_engine", workspace_id=workspace_id)
        return await func(*args, **kwargs)

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        db: Session = kwargs.get("db")
        user = _get_user_from_kwargs(kwargs)
        logger.debug(
            f"check_memory_engine_quota sync_wrapper: db={db is not None}, user={user}, kwargs_keys={list(kwargs.keys())}")
        if not db or not user:
            logger.error(f"配额检查失败：{func.__name__} 缺少 db 或 user 参数，拒绝请求")
            raise InternalServerError()
        workspace_id = _get_workspace_id_from_kwargs(kwargs)
        if not workspace_id:
            logger.error(f"配额检查失败：{func.__name__} 无法获取 workspace_id，拒绝请求")
            raise InternalServerError()
        _check_quota(db, user.tenant_id, "memory_engine_quota", "memory_engine", workspace_id=workspace_id)
        return func(*args, **kwargs)

    return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper


def check_end_user_quota(func: Callable) -> Callable:
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        db: Session = kwargs.get("db")
        if not db:
            logger.error(f"配额检查失败：{func.__name__} 缺少 db 参数，拒绝请求")
            raise InternalServerError()
        tenant_id = _get_tenant_id_from_kwargs(db, kwargs)
        if not tenant_id:
            logger.error(f"配额检查失败：{func.__name__} 无法获取 tenant_id，拒绝请求")
            raise InternalServerError()
        workspace_id = _get_workspace_id_from_kwargs(kwargs)
        if not workspace_id:
            logger.error(f"配额检查失败：{func.__name__} 无法获取 workspace_id，拒绝请求")
            raise InternalServerError()
        _check_quota(db, tenant_id, "end_user_quota", "end_user", workspace_id=workspace_id)
        return await func(*args, **kwargs)

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        db: Session = kwargs.get("db")
        if not db:
            logger.error(f"配额检查失败：{func.__name__} 缺少 db 参数，拒绝请求")
            raise InternalServerError()
        tenant_id = _get_tenant_id_from_kwargs(db, kwargs)
        if not tenant_id:
            logger.error(f"配额检查失败：{func.__name__} 无法获取 tenant_id，拒绝请求")
            raise InternalServerError()
        workspace_id = _get_workspace_id_from_kwargs(kwargs)
        if not workspace_id:
            logger.error(f"配额检查失败：{func.__name__} 无法获取 workspace_id，拒绝请求")
            raise InternalServerError()
        _check_quota(db, tenant_id, "end_user_quota", "end_user", workspace_id=workspace_id)
        return func(*args, **kwargs)

    return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper


def check_ontology_project_quota(func: Callable) -> Callable:
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        db: Session = kwargs.get("db")
        user = _get_user_from_kwargs(kwargs)
        if not db or not user:
            logger.error(f"配额检查失败：{func.__name__} 缺少 db 或 user 参数，拒绝请求")
            raise InternalServerError()
        workspace_id = _get_workspace_id_from_kwargs(kwargs)
        if not workspace_id:
            logger.error(f"配额检查失败：{func.__name__} 无法获取 workspace_id，拒绝请求")
            raise InternalServerError()
        _check_quota(db, user.tenant_id, "ontology_project_quota", "ontology_project", workspace_id=workspace_id)
        return await func(*args, **kwargs)

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        db: Session = kwargs.get("db")
        user = _get_user_from_kwargs(kwargs)
        if not db or not user:
            logger.error(f"配额检查失败：{func.__name__} 缺少 db 或 user 参数，拒绝请求")
            raise InternalServerError()
        workspace_id = _get_workspace_id_from_kwargs(kwargs)
        if not workspace_id:
            logger.error(f"配额检查失败：{func.__name__} 无法获取 workspace_id，拒绝请求")
            raise InternalServerError()
        _check_quota(db, user.tenant_id, "ontology_project_quota", "ontology_project", workspace_id=workspace_id)
        return func(*args, **kwargs)

    return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper


def check_model_quota(func: Callable) -> Callable:
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        db: Session = kwargs.get("db")
        user = _get_user_from_kwargs(kwargs)
        if not db or not user:
            logger.error(f"配额检查失败：{func.__name__} 缺少 db 或 user 参数，拒绝请求")
            raise InternalServerError()
        _check_quota(db, user.tenant_id, "model_quota", "model")
        return await func(*args, **kwargs)

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        db: Session = kwargs.get("db")
        user = _get_user_from_kwargs(kwargs)
        if not db or not user:
            logger.error(f"配额检查失败：{func.__name__} 缺少 db 或 user 参数，拒绝请求")
            raise InternalServerError()
        _check_quota(db, user.tenant_id, "model_quota", "model")
        return func(*args, **kwargs)

    return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper


def check_model_activation_quota(func: Callable) -> Callable:
    """模型激活时的配额检查装饰器"""

    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        db: Session = kwargs.get("db")
        user = _get_user_from_kwargs(kwargs)
        if not db or not user:
            logger.error(f"配额检查失败：{func.__name__} 缺少 db 或 user 参数，拒绝请求")
            raise InternalServerError()

        model_id = kwargs.get("model_id") or (args[1] if len(args) > 1 else None)
        model_data = kwargs.get("model_data")

        if not model_id or not model_data:
            logger.warning("模型激活配额检查失败：缺少 model_id 或 model_data 参数")
            return await func(*args, **kwargs)

        if model_data.is_active:
            try:
                from app.services.model_service import ModelConfigService

                existing_model = ModelConfigService.get_model_by_id(
                    db=db,
                    model_id=model_id,
                    tenant_id=user.tenant_id
                )

                if not existing_model.is_active:
                    logger.info(f"模型激活操作，检查配额: model_id={model_id}, tenant_id={user.tenant_id}")
                    _check_quota(db, user.tenant_id, "model_quota", "model")
            except Exception as e:
                logger.error(f"模型激活配额检查异常: model_id={model_id}, error={str(e)}")
                raise

        return await func(*args, **kwargs)

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        db: Session = kwargs.get("db")
        user = _get_user_from_kwargs(kwargs)
        if not db or not user:
            logger.error(f"配额检查失败：{func.__name__} 缺少 db 或 user 参数，拒绝请求")
            raise InternalServerError()

        model_id = kwargs.get("model_id") or (args[1] if len(args) > 1 else None)
        model_data = kwargs.get("model_data")

        if not model_id or not model_data:
            logger.warning("模型激活配额检查失败：缺少 model_id 或 model_data 参数")
            return func(*args, **kwargs)

        if model_data.is_active:
            try:
                from app.services.model_service import ModelConfigService

                existing_model = ModelConfigService.get_model_by_id(
                    db=db,
                    model_id=model_id,
                    tenant_id=user.tenant_id
                )

                if not existing_model.is_active:
                    logger.info(f"模型激活操作，检查配额: model_id={model_id}, tenant_id={user.tenant_id}")
                    _check_quota(db, user.tenant_id, "model_quota", "model")
            except Exception as e:
                logger.error(f"模型激活配额检查异常: model_id={model_id}, error={str(e)}")
                raise

        return func(*args, **kwargs)

    return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper


def check_quota(quota_type: str, resource_name: str, usage_func: Optional[Callable] = None):
    """通用配额检查装饰器，支持自定义使用量获取函数"""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            db: Session = kwargs.get("db")
            user = _get_user_from_kwargs(kwargs)
            if not db or not user:
                logger.error(f"配额检查失败：{func.__name__} 缺少 db 或 user 参数，拒绝请求")
                raise InternalServerError()
            _check_quota(db, user.tenant_id, quota_type, resource_name, usage_func)
            return await func(*args, **kwargs)

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            db: Session = kwargs.get("db")
            user = _get_user_from_kwargs(kwargs)
            if not db or not user:
                logger.error(f"配额检查失败：{func.__name__} 缺少 db 或 user 参数，拒绝请求")
                raise InternalServerError()
            _check_quota(db, user.tenant_id, quota_type, resource_name, usage_func)
            return func(*args, **kwargs)

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator


# ─── 配额告警触发 ────────────────────────────────────────────────────────────


def get_quota_snapshot(
        db: Session,
        tenant_id: UUID,
        quota_type: str,
        workspace_id: Optional[UUID] = None,
) -> Optional[Dict[str, Any]]:
    """按 :func:`_check_quota` 的同一口径返回单个配额的用量快照。

    这与 :func:`get_quota_usage` 的租户汇总口径不同：按空间计量的配额必须用
    「单空间用量 / 单空间额度」评估，否则把额度乘以活跃空间数后，填满某一个
    空间也推不高租户百分比，告警永远不会触发。
    """
    plan_quota, pack_quota = _get_quota_breakdown(db, tenant_id)
    if not plan_quota and not pack_quota:
        return None

    per_workspace = quota_type in PER_WORKSPACE_QUOTA_KEYS
    scoped = per_workspace and workspace_id is not None

    plan_limit = (plan_quota or {}).get(quota_type)
    pack_limit = (pack_quota or {}).get(quota_type) or 0
    if plan_limit is None and not pack_limit:
        return None
    limit = (plan_limit or 0) + pack_limit

    repo = QuotaUsageRepository(db)
    if scoped:
        used = repo.get_usage_by_quota_type(tenant_id, quota_type, workspace_id)
    else:
        used = repo.get_usage_by_quota_type(tenant_id, quota_type)
        if per_workspace:
            # 无空间上下文时退回租户汇总：额度按活跃空间数折算，与 get_quota_usage 一致。
            workspace_count = repo.count_workspaces(tenant_id)
            if workspace_count > 0:
                limit = limit * workspace_count

    percentage = round(used / limit * 100, 1) if limit else None
    return {
        "used": round(used, 2) if isinstance(used, float) else used,
        "limit": round(limit, 4) if isinstance(limit, float) else limit,
        "percentage": percentage,
        "unit": "GB" if quota_type == "knowledge_capacity_quota" else None,
        "scope": "workspace" if scoped else "tenant",
        "workspace_id": str(workspace_id) if scoped else None,
        "limit_source": {"plan": plan_limit, "resource_pack": pack_limit},
    }


async def report_quota_change(
        tenant_id: UUID,
        quota_type: str,
        workspace_id: Optional[UUID] = None,
) -> None:
    """用量变更提交后调用的告警入口（async）。

    任何真正改变配额用量的代码路径都应在主事务提交成功后调用本函数，
    不要依赖准入检查装饰器——检查点和消费点并不总是同一个接口。
    告警基础设施故障不会影响主业务。
    """
    await _evaluate_quota_alert_async(tenant_id, quota_type, workspace_id)


def report_quota_change_sync(
        tenant_id: UUID,
        quota_type: str,
        workspace_id: Optional[UUID] = None,
) -> None:
    """:func:`report_quota_change` 的同步版本，供同步接口与后台任务使用。"""
    _evaluate_quota_alert_sync(tenant_id, quota_type, workspace_id)


def _quota_operation_succeeded(result: Any) -> bool:
    """仅在业务成功后评估告警，兼容统一响应字典与 Response。"""
    status_code = getattr(result, "status_code", None)
    if status_code is not None and status_code >= 400:
        return False
    if isinstance(result, dict) and "code" in result:
        return result.get("code") == 0
    return True


async def _evaluate_quota_alert_async(
        tenant_id: UUID,
        quota_type: str,
        workspace_id: Optional[UUID] = None,
) -> None:
    """异步触发提交后的配额告警；告警基础设施故障不影响主业务。"""
    try:
        from app.plugins import get_plugin

        reporter = get_plugin("quota_usage_alert_reporter")
        if reporter is not None:
            await reporter.evaluate(
                tenant_id=tenant_id,
                quota_type=quota_type,
                workspace_id=workspace_id,
            )
    except Exception as exc:
        logger.error(
            "配额告警评估失败: tenant=%s, workspace=%s, type=%s, error=%s",
            tenant_id,
            workspace_id,
            quota_type,
            exc,
            exc_info=True,
        )


def _evaluate_quota_alert_sync(
        tenant_id: UUID,
        quota_type: str,
        workspace_id: Optional[UUID] = None,
) -> None:
    """同步入口通常运行在线程池中；必要时复用当前事件循环。"""
    try:
        from app.plugins import get_plugin

        reporter = get_plugin("quota_usage_alert_reporter")
        if reporter is None:
            return
        evaluation = reporter.evaluate(
            tenant_id=tenant_id,
            quota_type=quota_type,
            workspace_id=workspace_id,
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(evaluation)
        else:
            # 防御直接从事件循环调用同步控制器的场景，避免 asyncio.run 嵌套。
            loop.create_task(evaluation)
    except Exception as exc:
        logger.error(
            "配额告警评估失败: tenant=%s, workspace=%s, type=%s, error=%s",
            tenant_id,
            workspace_id,
            quota_type,
            exc,
            exc_info=True,
        )


def _with_post_success_quota_alert(check_decorator: Callable, quota_type: str) -> Callable:
    """为既有配额检查装饰器增加统一的业务成功后告警评估。"""
    def decorator(func: Callable) -> Callable:
        checked_func = check_decorator(func)
        # 按空间计量的配额必须带上空间上下文，评估口径才与准入校验一致。
        needs_workspace = quota_type in PER_WORKSPACE_QUOTA_KEYS

        if asyncio.iscoroutinefunction(checked_func):
            @wraps(checked_func)
            async def async_wrapper(*args, **kwargs):
                result = await checked_func(*args, **kwargs)
                db = kwargs.get("db")
                if isinstance(db, AsyncSession):
                    tenant_id = await _get_tenant_id_from_kwargs_async(db, kwargs)
                else:
                    tenant_id = _get_tenant_id_from_kwargs(db, kwargs)
                if tenant_id and _quota_operation_succeeded(result):
                    workspace_id = _get_workspace_id_from_kwargs(kwargs) if needs_workspace else None
                    await _evaluate_quota_alert_async(tenant_id, quota_type, workspace_id)
                return result

            return async_wrapper

        @wraps(checked_func)
        def sync_wrapper(*args, **kwargs):
            result = checked_func(*args, **kwargs)
            tenant_id = _get_tenant_id_from_kwargs(kwargs.get("db"), kwargs)
            if tenant_id and _quota_operation_succeeded(result):
                workspace_id = _get_workspace_id_from_kwargs(kwargs) if needs_workspace else None
                _evaluate_quota_alert_sync(tenant_id, quota_type, workspace_id)
            return result

        return sync_wrapper

    return decorator


# 对所有可聚合出「已用量 / 总额度」的套餐配额启用同一套触发逻辑。
# 三类速率/单用户限制没有租户级累计用量，不使用 quota_usage 百分比告警。
#
# end_user_quota 不在此列：它的检查装饰器挂在 write_memory 等热路径上，
# 而绝大多数请求并不新建终端用户。改由各创建点显式调用 report_quota_change，
# 避免每次记忆写入都做一轮配额聚合。
check_workspace_quota = _with_post_success_quota_alert(check_workspace_quota, "workspace_quota")
check_skill_quota = _with_post_success_quota_alert(check_skill_quota, "skill_quota")
check_app_quota = _with_post_success_quota_alert(check_app_quota, "app_quota")
check_knowledge_capacity_quota = _with_post_success_quota_alert(
    check_knowledge_capacity_quota,
    "knowledge_capacity_quota",
)
check_memory_engine_quota = _with_post_success_quota_alert(
    check_memory_engine_quota,
    "memory_engine_quota",
)
check_ontology_project_quota = _with_post_success_quota_alert(
    check_ontology_project_quota,
    "ontology_project_quota",
)
check_model_quota = _with_post_success_quota_alert(check_model_quota, "model_quota")
check_model_activation_quota = _with_post_success_quota_alert(
    check_model_activation_quota,
    "model_quota",
)


# ─── 配额使用统计 ────────────────────────────────────────────────────────────

async def get_quota_usage(db: Session, tenant_id: UUID) -> dict:
    """获取租户全部配额的使用情况。

    workspace 级配额只返回租户维度的汇总值，不再逐空间展开明细。

    额度口径：workspace 级配额的租户总额度 = （套餐每空间额度 + 资源包每空间额度）
    × 活跃空间数，与 :func:`_check_quota` 的单空间校验上限保持一致；
    ``limit_source`` 给出套餐与资源包的构成明细。
    """
    plan_quota, pack_quota = _get_quota_breakdown(db, tenant_id)
    quota_config = _merge_quota_overlay(plan_quota, pack_quota) if pack_quota else dict(plan_quota or {})
    if not quota_config:
        return {}

    repo = QuotaUsageRepository(db)

    def pct(used, limit):
        return round(used / limit * 100, 1) if limit else None

    workspace_count = repo.count_workspaces(tenant_id)
    skill_count = repo.count_skills(tenant_id)
    app_count = repo.count_apps(tenant_id)
    knowledge_gb = repo.sum_knowledge_capacity_gb(tenant_id)
    memory_count = repo.count_memory_engines(tenant_id)
    end_user_count = repo.count_end_users(tenant_id)
    model_count = repo.count_models(tenant_id)
    ontology_count = repo.count_ontology_projects(tenant_id)

    def effective_workspace_limit(quota_type: str):
        """租户总额度 =（套餐每空间额度 + 资源包每空间额度）× 活跃空间数。

        套餐和资源包额度都作用在单个工作空间上（见 :func:`_check_quota`），
        因此先合并成单空间上限，再按活跃空间数折算成租户总额度。
        """
        plan_per_workspace = (plan_quota or {}).get(quota_type)
        pack_per_workspace = (pack_quota or {}).get(quota_type) or 0
        if plan_per_workspace is None and not pack_per_workspace:
            return None
        per_workspace_limit = (plan_per_workspace or 0) + pack_per_workspace
        total = per_workspace_limit * workspace_count if workspace_count > 0 else per_workspace_limit
        return round(total, 4) if isinstance(total, float) else total

    app_effective_limit = effective_workspace_limit("app_quota")
    knowledge_effective_limit = effective_workspace_limit("knowledge_capacity_quota")
    memory_effective_limit = effective_workspace_limit("memory_engine_quota")
    end_user_effective_limit = effective_workspace_limit("end_user_quota")
    ontology_effective_limit = effective_workspace_limit("ontology_project_quota")

    def limit_source(quota_type: str) -> dict:
        """返回额度构成，便于前端区分套餐与资源包贡献。"""
        plan_value = (plan_quota or {}).get(quota_type)
        pack_value = (pack_quota or {}).get(quota_type) or 0
        source = {"plan": plan_value, "resource_pack": pack_value}
        if quota_type in PER_WORKSPACE_QUOTA_KEYS:
            source["plan_per_workspace"] = plan_value
            source["resource_pack_per_workspace"] = pack_value
            source["workspace_count"] = workspace_count
        return source

    return {
        "workspace_quota": {
            "used": workspace_count,
            "limit": quota_config.get("workspace_quota"),
            "percentage": pct(workspace_count, quota_config.get("workspace_quota")),
            "limit_source": limit_source("workspace_quota"),
        },
        "skill_quota": {
            "used": skill_count,
            "limit": quota_config.get("skill_quota"),
            "percentage": pct(skill_count, quota_config.get("skill_quota")),
            "limit_source": limit_source("skill_quota"),
        },
        "app_quota": {
            "used": app_count,
            "limit": app_effective_limit,
            "percentage": pct(app_count, app_effective_limit),
            "limit_source": limit_source("app_quota"),
        },
        "knowledge_capacity_quota": {
            "used": round(knowledge_gb, 2),
            "limit": knowledge_effective_limit,
            "percentage": pct(knowledge_gb, knowledge_effective_limit),
            "unit": "GB",
            "limit_source": limit_source("knowledge_capacity_quota"),
        },
        "memory_engine_quota": {
            "used": memory_count,
            "limit": memory_effective_limit,
            "percentage": pct(memory_count, memory_effective_limit),
            "limit_source": limit_source("memory_engine_quota"),
        },
        "end_user_quota": {
            "used": end_user_count,
            "limit": end_user_effective_limit,
            "percentage": pct(end_user_count, end_user_effective_limit),
            "limit_source": limit_source("end_user_quota"),
        },
        "ontology_project_quota": {
            "used": ontology_count,
            "limit": ontology_effective_limit,
            "percentage": pct(ontology_count, ontology_effective_limit),
            "limit_source": limit_source("ontology_project_quota"),
        },
        "model_quota": {
            "used": model_count,
            "limit": quota_config.get("model_quota"),
            "percentage": pct(model_count, quota_config.get("model_quota")),
            "limit_source": limit_source("model_quota"),
        },
    }
