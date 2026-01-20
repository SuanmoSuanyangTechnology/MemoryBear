"""
Celery Worker 入口点
用于启动 Celery Worker: celery -A app.celery_worker worker --loglevel=info
"""
from app.celery_app import celery_app
from app.core.logging_config import LoggingConfig, get_logger

# Initialize logging system for Celery worker
LoggingConfig.setup_logging()
logger = get_logger(__name__)
logger.info("Celery worker logging initialized")

# 导入任务模块以注册任务
import app.tasks

__all__ = ['celery_app']