import os
import platform
import re
from datetime import timedelta
from urllib.parse import quote

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings
from app.core.logging_config import get_logger

logger = get_logger(__name__)


def _mask_url(url: str) -> str:
    """隐藏 URL 中的密码部分，适用于 redis:// 和 amqp:// 等协议"""
    return re.sub(r'(://[^:]*:)[^@]+(@)', r'\1***\2', url)


# macOS fork() safety - must be set before any Celery initialization
if platform.system() == 'Darwin':
    os.environ.setdefault('OBJC_DISABLE_INITIALIZE_FORK_SAFETY', 'YES')

# 创建 Celery 应用实例
# broker: 优先使用环境变量 CELERY_BROKER_URL（支持 amqp:// 等任意协议），
#         未配置则回退到 Redis 方案
# backend: 结果存储（使用 Redis）
# NOTE: 不要在 .env 中设置 BROKER_URL / RESULT_BACKEND / CELERY_BROKER / CELERY_BACKEND，
#       这些名称会被 Celery CLI 的 Click 框架劫持，详见 docs/celery-env-bug-report.md

_broker_url = os.getenv("CELERY_BROKER_URL") or \
              f"redis://:{quote(settings.REDIS_PASSWORD)}@{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB_CELERY_BROKER}"
_backend_url = f"redis://:{quote(settings.REDIS_PASSWORD)}@{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB_CELERY_BACKEND}"
os.environ["CELERY_BROKER_URL"] = _broker_url
os.environ["CELERY_RESULT_BACKEND"] = _backend_url
# Neutralize legacy Celery env vars that can be hijacked by Celery's CLI/Click
# integration and accidentally override our canonical URLs.
os.environ.pop("BROKER_URL", None)
os.environ.pop("RESULT_BACKEND", None)
os.environ.pop("CELERY_BROKER", None)
os.environ.pop("CELERY_BACKEND", None)

celery_app = Celery(
    "redbear_tasks",
    broker=_broker_url,
    backend=_backend_url,
)

logger.info(
    "Celery app initialized",
    extra={
        "broker": _mask_url(_broker_url),
        "backend": _mask_url(_backend_url),
    },
)
# Default queue for unrouted tasks
celery_app.conf.task_default_queue = 'memory_tasks'

# macOS 兼容性配置
if platform.system() == 'Darwin':
    os.environ.setdefault('OBJC_DISABLE_INITIALIZE_FORK_SAFETY', 'YES')

# Celery 配置
celery_app.conf.update(
    # 序列化
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',

    # # 时区
    # timezone='Asia/Shanghai',
    # enable_utc=False,

    # 任务追踪
    task_track_started=True,
    task_ignore_result=False,

    # 超时设置
    task_time_limit=3600,  # 60分钟硬超时
    task_soft_time_limit=3000,  # 50分钟软超时

    # Worker 设置 (per-worker settings are in docker-compose command line)
    worker_prefetch_multiplier=1,  # Don't hoard tasks, fairer distribution
    worker_redirect_stdouts_level='INFO',  # stdout/print → INFO instead of WARNING

    # 结果过期时间
    result_expires=3600,  # 结果保存1小时

    # 任务确认设置
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_disable_rate_limits=True,

    # FLower setting 
    worker_send_task_events=True,
    task_send_sent_event=True,

    # task routing
    task_routes={
        # Memory tasks → memory_tasks queue (threads worker)
        'app.core.memory.agent.read_message_priority': {'queue': 'memory_tasks'},
        'app.core.memory.agent.read_message': {'queue': 'memory_tasks'},
        'app.core.memory.agent.write_message': {'queue': 'memory_tasks'},

        # Long-term storage tasks → memory_tasks queue (batched write strategies)
        'app.core.memory.agent.long_term_storage.window': {'queue': 'memory_tasks'},
        'app.core.memory.agent.long_term_storage.time': {'queue': 'memory_tasks'},
        'app.core.memory.agent.long_term_storage.aggregate': {'queue': 'memory_tasks'},

        # Clustering tasks → memory_tasks queue (使用相同的 worker，避免 macOS fork 问题)
        'app.tasks.run_incremental_clustering': {'queue': 'memory_tasks'},

        # Metadata extraction → memory_tasks queue
        'app.tasks.extract_user_metadata': {'queue': 'memory_tasks'},

        # Async emotion extraction → memory_tasks queue (IO-bound LLM calls)
        'app.tasks.extract_emotion_batch': {'queue': 'memory_tasks'},

        # Async metadata extraction → memory_tasks queue
        'app.tasks.extract_metadata_batch': {'queue': 'memory_tasks'},

        # Document tasks → document_tasks queue (prefork worker)
        'app.core.rag.tasks.parse_document': {'queue': 'document_tasks'},
        'app.core.rag.tasks.sync_knowledge_for_kb': {'queue': 'document_tasks'},

        # GraphRAG tasks → graphrag_tasks queue (独立队列，避免阻塞文档解析)
        'app.core.rag.tasks.build_graphrag_for_kb': {'queue': 'graphrag_tasks'},
        'app.core.rag.tasks.build_graphrag_for_document': {'queue': 'graphrag_tasks'},

        # Beat/periodic tasks → periodic_tasks queue (dedicated periodic worker)
        'app.tasks.workspace_reflection_task': {'queue': 'periodic_tasks'},
        'app.tasks.layer2_reflection_task': {'queue': 'periodic_tasks'},
        'app.tasks.layer2_dedup_full_scan_task': {'queue': 'periodic_tasks'},
        'app.tasks.regenerate_memory_cache': {'queue': 'periodic_tasks'},
        'app.tasks.run_forgetting_cycle_task': {'queue': 'periodic_tasks'},
        'app.tasks.write_all_workspaces_memory_task': {'queue': 'periodic_tasks'},
        'app.tasks.update_implicit_emotions_storage': {'queue': 'periodic_tasks'},
        'app.tasks.init_implicit_emotions_for_users': {'queue': 'periodic_tasks'},
        'app.tasks.init_interest_distribution_for_users': {'queue': 'periodic_tasks'},
        'app.tasks.init_community_clustering_for_users': {'queue': 'periodic_tasks'},
        'app.tasks.refresh_hot_memory_tags_cache': {'queue': 'periodic_tasks'},

        # Sliding window write tasks → memory_tasks queue (IO-bound async tasks)
        'app.tasks.sliding_window_write': {'queue': 'memory_tasks'},
        'app.tasks.flush_conversation': {'queue': 'memory_tasks'},

        # Sliding window idle scan → periodic_tasks queue (Beat scheduler)
        'app.tasks.scan_idle_conversations': {'queue': 'periodic_tasks'},
        'app.tasks.scan_workflow_schedule_triggers': {'queue': 'periodic_tasks'},
        'app.tasks.run_workflow_schedule_trigger': {'queue': 'workflow_trigger_tasks'},
    },
)

# 自动发现任务模块
celery_app.autodiscover_tasks(['app'])

# 企业版订阅任务路由（仅在 premium 模块存在时注册，避免社区版 worker 误接任务）
try:
    import premium.platform_admin.subscription_tasks  # noqa: F401
    _HAS_SUBSCRIPTION_TASKS = True
    # 状态变更任务 → subscription_state_tasks 队列（轻量，每 10 分钟一次）
    celery_app.conf.task_routes['subscription.process_expired_subscriptions'] = {
        'queue': 'subscription_state_tasks'
    }
    # 邮件发送任务 → subscription_email_tasks 队列（长耗时，每小时一次，跑一小时）
    celery_app.conf.task_routes['subscription.expiration_reminder'] = {
        'queue': 'subscription_email_tasks'
    }
    celery_app.conf.task_routes['subscription.expired_notice'] = {
        'queue': 'subscription_email_tasks'
    }
    # 邮件任务自身也显式配置 acks_late/time_limit，避免硬超时后旧任务重新入队。
except ImportError:
    _HAS_SUBSCRIPTION_TASKS = False

# Celery Beat schedule for periodic tasks
memory_increment_schedule = crontab(hour=settings.MEMORY_INCREMENT_HOUR, minute=settings.MEMORY_INCREMENT_MINUTE)
memory_cache_regeneration_schedule = timedelta(hours=settings.MEMORY_CACHE_REGENERATION_HOURS)
workspace_reflection_schedule = timedelta(seconds=settings.WORKSPACE_REFLECTION_INTERVAL_SECONDS)
forgetting_cycle_schedule = timedelta(hours=settings.FORGETTING_CYCLE_INTERVAL_HOURS)
implicit_emotions_update_schedule = crontab(
    hour=settings.IMPLICIT_EMOTIONS_UPDATE_HOUR,
    minute=settings.IMPLICIT_EMOTIONS_UPDATE_MINUTE,
)
layer2_reflection_schedule = timedelta(minutes=settings.LAYER2_REFLECTION_INTERVAL_MINUTES)
layer2_dedup_full_scan_schedule = crontab(hour=settings.LAYER2_DEDUP_FULL_SCAN_HOUR, minute=0)
hot_memory_tags_refresh_schedule = crontab(hour=settings.HOT_MEMORY_TAGS_REFRESH_HOUR, minute=0)
# 构建定时任务配置
beat_schedule_config = {
    # "run-workspace-reflection": {
    #     "task": "app.tasks.workspace_reflection_task",
    #     "schedule": workspace_reflection_schedule,
    #     "args": (),
    # },
    "regenerate-memory-cache": {
        "task": "app.tasks.regenerate_memory_cache",
        "schedule": memory_cache_regeneration_schedule,
        "args": (),
    },
    "run-forgetting-cycle": {
        "task": "app.tasks.run_forgetting_cycle_task",
        "schedule": forgetting_cycle_schedule,
        "kwargs": {
            "config_id": None,  # 使用默认配置，可以通过环境变量配置
        },
    },
    "write-all-workspaces-memory": {
        "task": "app.tasks.write_all_workspaces_memory_task",
        "schedule": memory_increment_schedule,
        "args": (),
    },
    "update-implicit-emotions-storage": {
        "task": "app.tasks.update_implicit_emotions_storage",
        "schedule": implicit_emotions_update_schedule,
        "args": (),
    },
    "run-layer2-reflection": {
            "task": "app.tasks.layer2_reflection_task",
            "schedule": layer2_reflection_schedule,
            "args": (),
    },
    "run-layer2-dedup-full-scan": {
        "task": "app.tasks.layer2_dedup_full_scan_task",
        "schedule": layer2_dedup_full_scan_schedule,
        "args": (),
    },
    "refresh-hot-memory-tags-cache": {
        "task": "app.tasks.refresh_hot_memory_tags_cache",
        "schedule": hot_memory_tags_refresh_schedule,
        "args": (),
    },
    # "scan-idle-conversations": {
    #     "task": "app.tasks.scan_idle_conversations",
    #     "schedule": 3600.0,
    #     "options": {"queue": "periodic_tasks"},
    # },
    # FIXME: Infinite task accumulation

    "scan-workflow-schedule-triggers": {
        "task": "app.tasks.scan_workflow_schedule_triggers",
        "schedule": 60.0,
        "options": {"queue": "periodic_tasks"},
    },
}

celery_app.conf.beat_schedule = beat_schedule_config

# 企业版订阅任务调度配置（_HAS_SUBSCRIPTION_TASKS 在上方路由注册处探测完成）
if _HAS_SUBSCRIPTION_TASKS:
    celery_app.conf.beat_schedule.update({
        # 主处理：每10分钟扫一次过期订阅（支持10万租户，concurrency=4可并行处理）
        "process-expired-subscriptions": {
            "task": "subscription.process_expired_subscriptions",
            "schedule": crontab(minute="*/10"),
            "options": {"queue": "subscription_state_tasks"},
        },
        # 兜底修复：每天北京凌晨 2:00（= UTC 18:00）再全量扫一次，
        # 处理主循环因异常/重启/时钟漂移遗漏的租户
        "process-expired-subscriptions-daily": {
            "task": "subscription.process_expired_subscriptions",
            "schedule": crontab(hour=18, minute=0),  # UTC 18:00 = CST 02:00
            "options": {"queue": "subscription_state_tasks"},
        },
        # 到期提醒：每小时整点投递；旧扫描任务超过 1 小时未被消费则过期丢弃
        "subscription-expiration-reminder": {
            "task": "subscription.expiration_reminder",
            "schedule": crontab(minute=0),
            "options": {"queue": "subscription_email_tasks", "expires": 3600},
        },
    })
