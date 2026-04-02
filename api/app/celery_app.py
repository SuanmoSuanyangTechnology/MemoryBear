import os
import platform
import re
from datetime import timedelta
from urllib.parse import quote

from celery import Celery
from celery.schedules import crontab
from kombu import Exchange, Queue

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

# ── Consistent Hash Exchange for write_message sharding ──
# Routes write tasks by end_user_id so same-user messages land on the same
# queue, giving sequential processing per user without blocking locks.
# Requires: rabbitmq-plugins enable rabbitmq_consistent_hash_exchange
MEMORY_WRITE_SHARDS = int(os.getenv('MEMORY_WRITE_SHARDS', '5'))

memory_write_exchange = Exchange(
    'memory_write_hash',
    type='x-consistent-hash',
    durable=True,
)

memory_write_queues = [
    Queue(
        f'memory_write_{i}',
        exchange=memory_write_exchange,
        routing_key='1',  # equal weight per shard
        durable=True,
    )
    for i in range(MEMORY_WRITE_SHARDS)
]

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

    # Declare shard queues so workers can consume them
    task_queues=memory_write_queues,

    # task routing
    task_routes={
        # Memory tasks → memory_tasks queue (threads worker)
        'app.core.memory.agent.read_message_priority': {'queue': 'memory_tasks'},
        'app.core.memory.agent.read_message': {'queue': 'memory_tasks'},
        # write_message is routed via consistent hash exchange at call site
        'app.tasks.write_perceptual_memory': {'queue': 'memory_tasks'},

        # Long-term storage tasks → memory_tasks queue (batched write strategies)
        'app.core.memory.agent.long_term_storage.window': {'queue': 'memory_tasks'},
        'app.core.memory.agent.long_term_storage.time': {'queue': 'memory_tasks'},
        'app.core.memory.agent.long_term_storage.aggregate': {'queue': 'memory_tasks'},

        # Clustering tasks → memory_tasks queue (使用相同的 worker，避免 macOS fork 问题)
        'app.tasks.run_incremental_clustering': {'queue': 'memory_tasks'},

        # Document tasks → document_tasks queue (prefork worker)
        'app.core.rag.tasks.parse_document': {'queue': 'document_tasks'},
        'app.core.rag.tasks.build_graphrag_for_kb': {'queue': 'document_tasks'},
        'app.core.rag.tasks.sync_knowledge_for_kb': {'queue': 'document_tasks'},

        # Beat/periodic tasks → periodic_tasks queue (dedicated periodic worker)
        'app.tasks.workspace_reflection_task': {'queue': 'periodic_tasks'},
        'app.tasks.regenerate_memory_cache': {'queue': 'periodic_tasks'},
        'app.tasks.run_forgetting_cycle_task': {'queue': 'periodic_tasks'},
        'app.tasks.write_all_workspaces_memory_task': {'queue': 'periodic_tasks'},
        'app.tasks.update_implicit_emotions_storage': {'queue': 'periodic_tasks'},
        'app.tasks.init_implicit_emotions_for_users': {'queue': 'periodic_tasks'},
        'app.tasks.init_interest_distribution_for_users': {'queue': 'periodic_tasks'},
        'app.tasks.init_community_clustering_for_users': {'queue': 'periodic_tasks'},
    },
)

# 自动发现任务模块
celery_app.autodiscover_tasks(['app'])

# Celery Beat schedule for periodic tasks
memory_increment_schedule = crontab(hour=settings.MEMORY_INCREMENT_HOUR, minute=settings.MEMORY_INCREMENT_MINUTE)
memory_cache_regeneration_schedule = timedelta(hours=settings.MEMORY_CACHE_REGENERATION_HOURS)
workspace_reflection_schedule = timedelta(seconds=settings.WORKSPACE_REFLECTION_INTERVAL_SECONDS)
forgetting_cycle_schedule = timedelta(hours=settings.FORGETTING_CYCLE_INTERVAL_HOURS)
implicit_emotions_update_schedule = crontab(
    hour=settings.IMPLICIT_EMOTIONS_UPDATE_HOUR,
    minute=settings.IMPLICIT_EMOTIONS_UPDATE_MINUTE,
)

# 构建定时任务配置
beat_schedule_config = {
    "run-workspace-reflection": {
        "task": "app.tasks.workspace_reflection_task",
        "schedule": workspace_reflection_schedule,
        "args": (),
    },
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
}

celery_app.conf.beat_schedule = beat_schedule_config
