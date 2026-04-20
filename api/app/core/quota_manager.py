"""
统一配额管理器 - 社区版和 SaaS 版共用

配额来源策略：
1. 优先从 premium 模块的 tenant_subscriptions 表读取（SaaS 版）
2. 降级到 default_free_plan.py 配置文件（社区版兜底）
"""
import asyncio
import time
from functools import wraps
from typing import Optional, Callable, Dict, Any
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.logging_config import get_auth_logger
from app.i18n.exceptions import QuotaExceededError, InternalServerError

logger = get_auth_logger()

# Redis key 格式常量，与 RateLimiterService.check_qps 保持一致（per api_key 独立计数）
API_KEY_QPS_REDIS_KEY = "rate_limit:qps:{api_key_id}"


def _get_user_from_kwargs(kwargs: dict):
    """从 kwargs 中获取 user 对象"""
    for key in ["user", "current_user"]:
        if key in kwargs:
            return kwargs[key]
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
                return app.workspace.tenant_id

    return None


def _get_quota_config(db: Session, tenant_id: UUID) -> Optional[Dict[str, Any]]:
    """
    获取租户的配额配置

    优先级：
    1. premium 模块的 tenant_subscriptions（SaaS 版）
    2. default_free_plan.py 配置文件（社区版兜底）
    """
    # 尝试从 premium 模块获取（SaaS 版）
    try:
        from premium.platform_admin.package_plan_service import TenantSubscriptionService
        # premium 模块存在，运行时错误不应被静默降级，直接抛出
        quota_config = TenantSubscriptionService(db).get_effective_quota(tenant_id)
        if quota_config:
            logger.debug(f"从 premium 模块获取租户 {tenant_id} 配额配置")
            return quota_config
        # premium 存在但该租户无订阅记录，降级到免费套餐
        logger.debug(f"租户 {tenant_id} 无 premium 订阅，降级到免费套餐")
    except (ModuleNotFoundError, ImportError):
        # 社区版：premium 包不存在，正常降级
        logger.debug("premium 模块不存在，使用社区版免费套餐配额")

    # 降级到社区版配置文件
    try:
        from app.config.default_free_plan import DEFAULT_FREE_PLAN
        logger.debug(f"使用社区版免费套餐配额: tenant={tenant_id}")
        return DEFAULT_FREE_PLAN.get("quotas")
    except Exception as e:
        logger.error(f"无法从配置文件获取配额: {e}")
        return None


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

    def count_apps(self, tenant_id: UUID) -> int:
        from app.models.app_model import App
        from app.models.workspace_model import Workspace
        return self.db.query(App).join(
            Workspace, App.workspace_id == Workspace.id
        ).filter(
            Workspace.tenant_id == tenant_id,
            App.is_active.is_(True)
        ).count()

    def count_skills(self, tenant_id: UUID) -> int:
        from app.models.skill_model import Skill
        return self.db.query(Skill).filter(
            Skill.tenant_id == tenant_id,
            Skill.is_active.is_(True)
        ).count()

    def sum_knowledge_capacity_gb(self, tenant_id: UUID) -> float:
        from app.models.document_model import Document
        from app.models.knowledge_model import Knowledge
        from app.models.workspace_model import Workspace
        result = self.db.query(func.coalesce(func.sum(Document.file_size), 0)).join(
            Knowledge, Document.kb_id == Knowledge.id
        ).join(
            Workspace, Knowledge.workspace_id == Workspace.id
        ).filter(
            Workspace.tenant_id == tenant_id,
            Document.status == 1,
        ).scalar()
        return float(result) / (1024 ** 3) if result else 0.0

    def count_memory_engines(self, tenant_id: UUID) -> int:
        from app.models.memory_config_model import MemoryConfig
        from app.models.workspace_model import Workspace
        return self.db.query(MemoryConfig).join(
            Workspace, MemoryConfig.workspace_id == Workspace.id
        ).filter(
            Workspace.tenant_id == tenant_id
        ).count()

    def count_end_users(self, tenant_id: UUID) -> int:
        from app.models.end_user_model import EndUser
        from app.models.workspace_model import Workspace
        return self.db.query(EndUser).join(
            Workspace, EndUser.workspace_id == Workspace.id
        ).filter(
            Workspace.tenant_id == tenant_id
        ).count()

    def count_models(self, tenant_id: UUID) -> int:
        from app.models.models_model import ModelConfig
        return self.db.query(ModelConfig).filter(
            ModelConfig.tenant_id == tenant_id,
            ModelConfig.is_active == True
        ).count()

    def count_ontology_projects(self, tenant_id: UUID) -> int:
        from app.models.ontology_scene import OntologyScene
        from app.models.workspace_model import Workspace
        return self.db.query(OntologyScene).join(
            Workspace, OntologyScene.workspace_id == Workspace.id
        ).filter(
            Workspace.tenant_id == tenant_id
        ).count()

    def get_usage_by_quota_type(self, tenant_id: UUID, quota_type: str):
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
        return fn(tenant_id) if fn else 0


def _check_quota(
    db: Session,
    tenant_id: UUID,
    quota_type: str,
    resource_name: str,
    usage_func: Optional[Callable] = None,
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
            current_usage = usage_func(db, tenant_id)
        else:
            current_usage = QuotaUsageRepository(db).get_usage_by_quota_type(tenant_id, quota_type)

        if current_usage >= quota_limit:
            logger.warning(
                f"配额不足: tenant={tenant_id}, type={quota_type}, "
                f"usage={current_usage}, limit={quota_limit}"
            )
            raise QuotaExceededError(
                resource=resource_name,
                current_usage=current_usage,
                quota_limit=quota_limit,
            )

        logger.debug(
            f"配额检查通过: tenant={tenant_id}, type={quota_type}, "
            f"usage={current_usage}, limit={quota_limit}"
        )

    except QuotaExceededError:
        raise
    except Exception as e:
        logger.error(
            f"配额检查异常: tenant={tenant_id}, type={quota_type}, "
            f"error_type={type(e).__name__}, error={str(e)}",
            exc_info=True,
        )
        raise


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
        _check_quota(db, user.tenant_id, "app_quota", "app")
        return await func(*args, **kwargs)

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        db: Session = kwargs.get("db")
        user = _get_user_from_kwargs(kwargs)
        if not db or not user:
            logger.error(f"配额检查失败：{func.__name__} 缺少 db 或 user 参数，拒绝请求")
            raise InternalServerError()
        _check_quota(db, user.tenant_id, "app_quota", "app")
        return func(*args, **kwargs)

    return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper


def check_knowledge_capacity_quota(func: Callable) -> Callable:
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
        _check_quota(db, tenant_id, "knowledge_capacity_quota", "knowledge_capacity")
        return await func(*args, **kwargs)

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        db: Session = kwargs.get("db")
        user = _get_user_from_kwargs(kwargs)
        if not db or not user:
            logger.error(f"配额检查失败：{func.__name__} 缺少 db 或 user 参数，拒绝请求")
            raise InternalServerError()
        _check_quota(db, user.tenant_id, "knowledge_capacity_quota", "knowledge_capacity")
        return func(*args, **kwargs)

    return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper


def check_memory_engine_quota(func: Callable) -> Callable:
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        db: Session = kwargs.get("db")
        user = _get_user_from_kwargs(kwargs)
        if not db or not user:
            logger.error(f"配额检查失败：{func.__name__} 缺少 db 或 user 参数，拒绝请求")
            raise InternalServerError()
        _check_quota(db, user.tenant_id, "memory_engine_quota", "memory_engine")
        return await func(*args, **kwargs)

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        db: Session = kwargs.get("db")
        user = _get_user_from_kwargs(kwargs)
        if not db or not user:
            logger.error(f"配额检查失败：{func.__name__} 缺少 db 或 user 参数，拒绝请求")
            raise InternalServerError()
        _check_quota(db, user.tenant_id, "memory_engine_quota", "memory_engine")
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
        _check_quota(db, tenant_id, "end_user_quota", "end_user")
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
        _check_quota(db, tenant_id, "end_user_quota", "end_user")
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
        _check_quota(db, user.tenant_id, "ontology_project_quota", "ontology_project")
        return await func(*args, **kwargs)

    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        db: Session = kwargs.get("db")
        user = _get_user_from_kwargs(kwargs)
        if not db or not user:
            logger.error(f"配额检查失败：{func.__name__} 缺少 db 或 user 参数，拒绝请求")
            raise InternalServerError()
        _check_quota(db, user.tenant_id, "ontology_project_quota", "ontology_project")
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

        if model_data.is_active is True:
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

        if model_data.is_active is True:
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
    """获取租户所有配额的使用情况"""
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
    model_count = repo.count_models(tenant_id)
    ontology_count = repo.count_ontology_projects(tenant_id)

    api_ops_current = 0
    try:
        from app.aioRedis import aio_redis as _aio_redis
        from app.models.api_key_model import ApiKey
        from app.models.workspace_model import Workspace
        _now = time.time()
        # api_ops_rate_limit 限的是每个 api_key 每秒最高限额
        # 展示当前最接近触发限流的 key 的 QPS（取最大值）
        api_key_ids = db.query(ApiKey.id).join(
            Workspace, ApiKey.workspace_id == Workspace.id
        ).filter(
            Workspace.tenant_id == tenant_id,
            ApiKey.is_active.is_(True)
        ).all()
        for (key_id,) in api_key_ids:
            _rk = API_KEY_QPS_REDIS_KEY.format(api_key_id=key_id)
            count = int(await _aio_redis.zcount(_rk, _now - 1, "+inf") or 0)
            if count > api_ops_current:
                api_ops_current = count
    except Exception as e:
        logger.warning(f"获取 api_ops_current 失败，返回 0: {type(e).__name__}: {e}")

    return {
        "workspace": {"used": workspace_count, "limit": quota_config.get("workspace_quota"), "percentage": pct(workspace_count, quota_config.get("workspace_quota"))},
        "skill": {"used": skill_count, "limit": quota_config.get("skill_quota"), "percentage": pct(skill_count, quota_config.get("skill_quota"))},
        "app": {"used": app_count, "limit": quota_config.get("app_quota"), "percentage": pct(app_count, quota_config.get("app_quota"))},
        "knowledge_capacity": {"used": round(knowledge_gb, 2), "limit": quota_config.get("knowledge_capacity_quota"), "percentage": pct(knowledge_gb, quota_config.get("knowledge_capacity_quota")), "unit": "GB"},
        "memory_engine": {"used": memory_count, "limit": quota_config.get("memory_engine_quota"), "percentage": pct(memory_count, quota_config.get("memory_engine_quota"))},
        "end_user": {"used": end_user_count, "limit": quota_config.get("end_user_quota"), "percentage": pct(end_user_count, quota_config.get("end_user_quota"))},
        "ontology_project": {"used": ontology_count, "limit": quota_config.get("ontology_project_quota"), "percentage": pct(ontology_count, quota_config.get("ontology_project_quota"))},
        "model": {"used": model_count, "limit": quota_config.get("model_quota"), "percentage": pct(model_count, quota_config.get("model_quota"))},
        "api_ops_rate_limit": {"current": api_ops_current, "limit": quota_config.get("api_ops_rate_limit"), "percentage": None, "unit": "次/秒"},
    }
