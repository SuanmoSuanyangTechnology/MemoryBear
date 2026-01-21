import os
import platform
from datetime import timedelta
from urllib.parse import quote

from app.core.config import settings
from celery import Celery

# 创建 Celery 应用实例
# broker: 任务队列（使用 Redis DB 0）
# backend: 结果存储（使用 Redis DB 10）
celery_app = Celery(
    "redbear_tasks",
    broker=f"redis://:{quote(settings.REDIS_PASSWORD)}@{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.CELERY_BROKER}",
    backend=f"redis://:{quote(settings.REDIS_PASSWORD)}@{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.CELERY_BACKEND}",
)

# Default queue for unrouted tasks
celery_app.conf.task_default_queue = 'io_tasks'

# macOS 兼容性配置
if platform.system() == 'Darwin':
    os.environ.setdefault('OBJC_DISABLE_INITIALIZE_FORK_SAFETY', 'YES')

# Celery 配置
celery_app.conf.update(
    # 序列化
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    
    # 时区
    timezone='Asia/Shanghai',
    enable_utc=True,
    
    # 任务追踪
    task_track_started=True,
    task_ignore_result=False,
    
    # 超时设置
    task_time_limit=1800,  # 30分钟硬超时
    task_soft_time_limit=1500,  # 25分钟软超时
    
    # Worker 设置 (per-worker settings are in docker-compose command line)
    worker_prefetch_multiplier=1,  # Don't hoard tasks, fairer distribution
    
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
        # IO-bound tasks → io_tasks queue (gevent worker)
        'app.core.memory.agent.read_message_priority': {'queue': 'io_tasks'},
        'app.core.memory.agent.read_message': {'queue': 'io_tasks'},
        'app.core.memory.agent.write_message': {'queue': 'io_tasks'},
        
        # CPU-bound tasks → cpu_tasks queue (prefork worker)
        'app.core.rag.tasks.parse_document': {'queue': 'cpu_tasks'},
        'app.core.rag.tasks.build_graphrag_for_kb': {'queue': 'cpu_tasks'},
        
        # Beat/periodic tasks → cpu_tasks queue (prefork worker)
        'app.tasks.workspace_reflection_task': {'queue': 'cpu_tasks'},
        'app.tasks.regenerate_memory_cache': {'queue': 'cpu_tasks'},
        'app.tasks.run_forgetting_cycle_task': {'queue': 'cpu_tasks'},
        'app.controllers.memory_storage_controller.search_all': {'queue': 'cpu_tasks'},
    },
)

# 自动发现任务模块
celery_app.autodiscover_tasks(['app'])

# Celery Beat schedule for periodic tasks
memory_increment_schedule = timedelta(hours=settings.MEMORY_INCREMENT_INTERVAL_HOURS)
memory_cache_regeneration_schedule = timedelta(hours=settings.MEMORY_CACHE_REGENERATION_HOURS)
workspace_reflection_schedule = timedelta(seconds=30)  # 每30秒运行一次settings.REFLECTION_INTERVAL_TIME
forgetting_cycle_schedule = timedelta(hours=24)  # 每24小时运行一次遗忘周期

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
}

# 如果配置了默认工作空间ID，则添加记忆总量统计任务
if settings.DEFAULT_WORKSPACE_ID:
    beat_schedule_config["write-total-memory"] = {
        "task": "app.controllers.memory_storage_controller.search_all",
        "schedule": memory_increment_schedule,
        "kwargs": {
            "workspace_id": settings.DEFAULT_WORKSPACE_ID,
        },
    }

celery_app.conf.beat_schedule = beat_schedule_config
