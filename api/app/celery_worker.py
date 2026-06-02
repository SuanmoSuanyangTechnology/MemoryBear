"""
Celery Worker 入口点
用于启动 Celery Worker: celery -A app.celery_worker worker --loglevel=info
"""
# 必须在导入任何使用 DashScope SDK 的模块之前应用补丁
import app.utils.dashscope_patch  # noqa: F401
from celery.signals import worker_process_init
from app.celery_app import celery_app
from app.core.logging_config import LoggingConfig, get_logger

# Initialize logging system for Celery worker
LoggingConfig.setup_logging()
logger = get_logger(__name__)
logger.info("Celery worker logging initialized")

# 导入任务模块以注册任务
import app.tasks

# 导入企业版订阅任务（仅在企业版环境下可用）
try:
    from premium.platform_admin.subscription_tasks import (  # noqa: F401
        process_expired_subscriptions_task,
    )
except ImportError:
    pass  # 社区版无 premium 模块，静默跳过


@worker_process_init.connect
def _reinit_db_pool(**kwargs):
    """
    prefork 子进程启动时重建被 fork 污染的资源。
    
    fork() 后子进程继承了父进程的：
    1. SQLAlchemy 连接池 — 多进程共享 TCP socket 导致 DB 连接损坏
    2. ThreadPoolExecutor — fork 后线程状态不确定，第二个任务会死锁
    """
    # 重建 DB 连接池
    from app.db import engine
    engine.dispose()
    logger.info("DB connection pool disposed for forked worker process")

    # 重建模块级 ThreadPoolExecutor（fork 后线程池不可用）
    try:
        from app.core.rag.deepdoc.parser import figure_parser
        from concurrent.futures import ThreadPoolExecutor
        figure_parser.shared_executor = ThreadPoolExecutor(max_workers=10)
        logger.info("figure_parser.shared_executor recreated")
    except Exception as e:
        logger.warning(f"Failed to recreate figure_parser.shared_executor: {e}")

    try:
        from app.core.rag.utils import libre_office
        from concurrent.futures import ThreadPoolExecutor
        import os
        max_workers = os.cpu_count() * 2 if os.cpu_count() else 4
        libre_office.executor = ThreadPoolExecutor(max_workers=max_workers)
        logger.info("libre_office.executor recreated")
    except Exception as e:
        logger.warning(f"Failed to recreate libre_office.executor: {e}")


__all__ = ['celery_app']
