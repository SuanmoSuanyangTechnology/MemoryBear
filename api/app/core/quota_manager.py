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


def _get_quota_config(db: Session, tenant_id: UUID) -> Optional[Dict[str, Any]]:
    """Get final quota = effective package (or free fallback) + active packs."""
    try:
        from premium.platform_admin.package_plan_service import TenantSubscriptionService
        from premium.platform_admin.resource_pack_service import ResourcePackService

        base = TenantSubscriptionService(db).get_effective_quota(tenant_id)
        if not base:
            logger.debug(f"租户 {tenant_id} 无 premium 订阅，降级到免费套餐")
            base = _free_quota_config()
        overlay = ResourcePackService(db).get_overlay(tenant_id)
        return _merge_quota_overlay(base, overlay)
    except (ModuleNotFoundError, ImportError):
        logger.debug("premium 模块不存在，使用社区版免费套餐配额")
        return _free_quota_config()


async def _get_quota_config_async(db: AsyncSession, tenant_id: UUID) -> Optional[Dict[str, Any]]:
    """Async equivalent of :func:`_get_quota_config`."""
    try:
        from premium.platform_admin.package_plan_service import TenantSubscriptionService
        from premium.platform_admin.resource_pack_service import ResourcePackService

        base = await TenantSubscriptionService(db).get_effective_quota_async(tenant_id)
        if not base:
            logger.debug(f"租户 {tenant_id} 无 premium 订阅，降级到免费套餐")
            base = _free_quota_config()
        overlay = await ResourcePackService.get_overlay_async(db, tenant_id)
        return _merge_quota_overlay(base, overlay)
    except (ModuleNotFoundError, ImportError):
        logger.debug("premium 模块不存在，使用社区版免费套餐配额")
        return _free_quota_config()


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

    def max_end_user_memory_count(
        self,
        tenant_id: UUID,
        workspace_id: Optional[UUID] = None,
    ) -> int:
        """返回单个活跃终端用户的最大已同步记忆节点数。"""
        from app.models.end_user_model import EndUser
        from app.models.workspace_model import Workspace

        query = self.db.query(func.coalesce(func.max(EndUser.memory_count), 0)).join(
            Workspace, EndUser.workspace_id == Workspace.id
        ).filter(
            Workspace.tenant_id == tenant_id,
            EndUser.is_active == True,
        )
        if workspace_id:
            query = query.filter(EndUser.workspace_id == workspace_id)
        return int(query.scalar() or 0)

    def list_active_end_user_ids(self, tenant_id: UUID) -> list[UUID]:
        """返回租户下活跃终端用户 ID，用于读取每用户实时限流窗口。"""
        from app.models.end_user_model import EndUser
        from app.models.workspace_model import Workspace

        rows = self.db.query(EndUser.id).join(
            Workspace, EndUser.workspace_id == Workspace.id
        ).filter(
            Workspace.tenant_id == tenant_id,
            EndUser.is_active == True,
        ).all()
        return [end_user_id for (end_user_id,) in rows]

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


# ─── 配额使用统计 ────────────────────────────────────────────────────────────

async def get_quota_usage(db: Session, tenant_id: UUID) -> dict:
    """获取租户全部配额的使用情况。

    workspace 级配额汇总所有活跃空间，并提供逐空间明细；每用户级配额
    使用租户内单个用户的最大值，便于直接判断最接近限额的资源。
    """
    quota_config = _get_quota_config(db, tenant_id)
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
    end_user_memory_count = repo.max_end_user_memory_count(tenant_id)
    model_count = repo.count_models(tenant_id)
    ontology_count = repo.count_ontology_projects(tenant_id)

    from app.models.workspace_model import Workspace

    active_workspaces = db.query(Workspace).filter(
        Workspace.tenant_id == tenant_id,
        Workspace.is_active.is_(True),
    ).all()

    def _build_per_workspace_detail(usage_func, per_unit_limit):
        """为 workspace 级或按 workspace 展示的配额构建明细。"""
        if per_unit_limit is None or not active_workspaces:
            return []
        details = []
        for workspace in active_workspaces:
            workspace_used = usage_func(tenant_id, workspace.id)
            details.append({
                "workspace_id": str(workspace.id),
                "workspace_name": workspace.name,
                "used": workspace_used,
                "limit": per_unit_limit,
                "percentage": pct(workspace_used, per_unit_limit),
            })
        return details

    app_quota_per_workspace = quota_config.get("app_quota")
    knowledge_quota_per_workspace = quota_config.get("knowledge_capacity_quota")
    memory_quota_per_workspace = quota_config.get("memory_engine_quota")
    end_user_quota_per_workspace = quota_config.get("end_user_quota")
    ontology_quota_per_workspace = quota_config.get("ontology_project_quota")
    end_user_memory_limit = quota_config.get("end_user_memory_limit")
    memory_write_qps_limit = quota_config.get("pre_user_memory_write_qps_limit")

    def effective_workspace_limit(per_workspace_limit):
        if per_workspace_limit is not None and workspace_count > 0:
            return per_workspace_limit * workspace_count
        return per_workspace_limit

    app_effective_limit = effective_workspace_limit(app_quota_per_workspace)
    knowledge_effective_limit = effective_workspace_limit(knowledge_quota_per_workspace)
    memory_effective_limit = effective_workspace_limit(memory_quota_per_workspace)
    end_user_effective_limit = effective_workspace_limit(end_user_quota_per_workspace)
    ontology_effective_limit = effective_workspace_limit(ontology_quota_per_workspace)

    api_ops_current = 0
    try:
        from app.aioRedis import aio_redis as _aio_redis
        from app.models.api_key_model import ApiKey

        api_key_ids = db.query(ApiKey.id).join(
            Workspace, ApiKey.workspace_id == Workspace.id
        ).filter(
            Workspace.tenant_id == tenant_id,
            ApiKey.is_active.is_(True),
        ).all()
        for (key_id,) in api_key_ids:
            redis_key = API_KEY_QPS_REDIS_KEY.format(api_key_id=key_id)
            value = await _aio_redis.get(redis_key)
            api_ops_current = max(api_ops_current, int(value) if value else 0)
    except Exception as e:
        logger.warning(f"获取 api_ops_current 失败，返回 0: {type(e).__name__}: {e}")

    memory_write_qps_current = 0
    try:
        import time

        from app.aioRedis import aio_redis as _aio_redis
        from app.celery_task_scheduler.rate_policy import RATE_LIMIT_PREFIX, RATE_WINDOW_MS

        now_ms = int(time.time() * 1000)
        task_name = "app.core.memory.agent.write_message"
        for end_user_id in repo.list_active_end_user_ids(tenant_id):
            unit_key = f"{task_name}:{end_user_id}"
            redis_key = f"{RATE_LIMIT_PREFIX}{unit_key}"
            await _aio_redis.zremrangebyscore(redis_key, 0, now_ms - RATE_WINDOW_MS)
            current = int(await _aio_redis.zcard(redis_key) or 0)
            memory_write_qps_current = max(memory_write_qps_current, current)
    except Exception as e:
        logger.warning(
            "获取 memory_write_qps_current 失败，返回 0: "
            f"{type(e).__name__}: {e}"
        )

    api_ops_limit = quota_config.get("api_ops_rate_limit")
    return {
        "workspace": {
            "used": workspace_count,
            "limit": quota_config.get("workspace_quota"),
            "percentage": pct(workspace_count, quota_config.get("workspace_quota")),
        },
        "skill": {
            "used": skill_count,
            "limit": quota_config.get("skill_quota"),
            "percentage": pct(skill_count, quota_config.get("skill_quota")),
        },
        "app": {
            "used": app_count,
            "limit": app_effective_limit,
            "percentage": pct(app_count, app_effective_limit),
            "per_workspace": _build_per_workspace_detail(
                repo.count_apps, app_quota_per_workspace
            ),
        },
        "knowledge_capacity": {
            "used": round(knowledge_gb, 2),
            "limit": knowledge_effective_limit,
            "percentage": pct(knowledge_gb, knowledge_effective_limit),
            "unit": "GB",
            "per_workspace": _build_per_workspace_detail(
                repo.sum_knowledge_capacity_gb, knowledge_quota_per_workspace
            ),
        },
        "memory_engine": {
            "used": memory_count,
            "limit": memory_effective_limit,
            "percentage": pct(memory_count, memory_effective_limit),
            "per_workspace": _build_per_workspace_detail(
                repo.count_memory_engines, memory_quota_per_workspace
            ),
        },
        "end_user": {
            "used": end_user_count,
            "limit": end_user_effective_limit,
            "percentage": pct(end_user_count, end_user_effective_limit),
            "per_workspace": _build_per_workspace_detail(
                repo.count_end_users, end_user_quota_per_workspace
            ),
        },
        "ontology_project": {
            "used": ontology_count,
            "limit": ontology_effective_limit,
            "percentage": pct(ontology_count, ontology_effective_limit),
            "per_workspace": _build_per_workspace_detail(
                repo.count_ontology_projects, ontology_quota_per_workspace
            ),
        },
        "model": {
            "used": model_count,
            "limit": quota_config.get("model_quota"),
            "percentage": pct(model_count, quota_config.get("model_quota")),
        },
        "api_ops_rate_limit": {
            "current": api_ops_current,
            "limit": api_ops_limit,
            "percentage": pct(api_ops_current, api_ops_limit),
            "unit": "次/秒/API Key",
        },
        "end_user_memory_limit": {
            "used": end_user_memory_count,
            "limit": end_user_memory_limit,
            "percentage": pct(end_user_memory_count, end_user_memory_limit),
            "unit": "记忆节点/用户",
            "aggregation": "max_per_end_user",
            "per_workspace": _build_per_workspace_detail(
                repo.max_end_user_memory_count, end_user_memory_limit
            ),
        },
        "pre_user_memory_write_qps_limit": {
            "current": memory_write_qps_current,
            "limit": memory_write_qps_limit,
            "percentage": pct(memory_write_qps_current, memory_write_qps_limit),
            "unit": "次/秒/用户",
            "aggregation": "max_per_end_user",
        },
    }
