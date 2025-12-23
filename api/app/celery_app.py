import os
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

# 配置使用本地队列，避免与远程 worker 冲突
celery_app.conf.task_default_queue = 'localhost_test_wyl'
celery_app.conf.task_default_exchange = 'localhost_test_wyl'
celery_app.conf.task_default_routing_key = 'localhost_test_wyl'

# macOS 兼容性配置
import platform

if platform.system() == 'Darwin':  # macOS
    # 设置环境变量解决 fork 问题
    os.environ.setdefault('OBJC_DISABLE_INITIALIZE_FORK_SAFETY', 'YES')
    
    # 使用 solo 池避免多进程问题
    celery_app.conf.worker_pool = 'solo'
    
    # 设置唯一的节点名称
    import socket
    import time
    hostname = socket.gethostname()
    timestamp = int(time.time())
    celery_app.conf.worker_name = f"celery@{hostname}-{timestamp}"

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
    task_time_limit=30 * 60,  # 30 分钟硬超时
    task_soft_time_limit=25 * 60,  # 25 分钟软超时
    
    # Worker 设置 - 针对 macOS 优化
    worker_prefetch_multiplier=1,  # 减少预取任务数，避免内存堆积
    worker_max_tasks_per_child=10,  # 大幅减少每个 worker 执行的任务数，频繁重启防止内存泄漏
    worker_max_memory_per_child=200000,  # 200MB 内存限制，超过后重启 worker
    
    # 结果过期时间
    result_expires=3600,  # 结果保存 1 小时
    
    # 任务确认设置
    task_acks_late=True,  # 任务完成后才确认，避免任务丢失
    worker_disable_rate_limits=True,  # 禁用速率限制
    
    # 任务路由（可选，用于不同队列）
    # task_routes={
    #     'app.core.rag.tasks.parse_document': {'queue': 'document_processing'},
    #     'app.core.memory.agent.read_message': {'queue': 'memory_processing'},
    #     'app.core.memory.agent.write_message': {'queue': 'memory_processing'},
    #     'tasks.process_item': {'queue': 'default'},
    # },
)

# 自动发现任务模块
celery_app.autodiscover_tasks(['app'])

# Celery Beat schedule for periodic tasks
reflection_schedule = timedelta(seconds=settings.REFLECTION_INTERVAL_SECONDS)
health_schedule = timedelta(seconds=settings.HEALTH_CHECK_SECONDS)
memory_increment_schedule = timedelta(hours=settings.MEMORY_INCREMENT_INTERVAL_HOURS)
memory_cache_regeneration_schedule = timedelta(hours=settings.MEMORY_CACHE_REGENERATION_HOURS)
workspace_reflection_schedule = timedelta(seconds=30)  # 每30秒运行一次settings.REFLECTION_INTERVAL_TIME
# 构建定时任务配置
beat_schedule_config = {

    # "check-read-service": {
    #     "task": "app.core.memory.agent.health.check_read_service",
    #     "schedule": health_schedule,
    #     "args": (),
    # },
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
