"""
遗忘引擎配置工具模块

本模块提供从数据库加载配置并创建遗忘引擎组件的辅助函数。

Functions:
    calculate_forgetting_rate: 计算遗忘速率（lambda_time / lambda_mem）
    load_actr_config_from_db: 从数据库同步加载 ACT-R 配置参数（Celery 任务等同步场景）
    load_actr_config_from_db_async: 从数据库异步加载 ACT-R 配置参数（FastAPI 异步场景）
"""

import logging
from typing import Optional, Dict, Any
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory_config_model import MemoryConfig
from app.repositories.memory_config_repository import MemoryConfigRepository

logger = logging.getLogger(__name__)


def calculate_forgetting_rate(lambda_time: float, lambda_mem: float) -> float:
    """
    计算遗忘速率
    
    公式：forgetting_rate = lambda_time / lambda_mem
    
    这个计算将两个独立的 lambda 参数组合成一个统一的遗忘速率参数，
    用于 ACT-R 激活值计算。
    
    Args:
        lambda_time: 时间衰减参数（0-1）
        lambda_mem: 记忆衰减参数（0-1）
    
    Returns:
        float: 遗忘速率
    
    Raises:
        ValueError: 如果 lambda_mem 为 0
    
    Examples:
        >>> calculate_forgetting_rate(0.5, 0.5)
        1.0
        >>> calculate_forgetting_rate(0.3, 0.5)
        0.6
    """
    if lambda_mem == 0:
        raise ValueError("lambda_mem 不能为 0")
    
    forgetting_rate = lambda_time / lambda_mem
    
    logger.debug(
        f"计算遗忘速率: lambda_time={lambda_time}, "
        f"lambda_mem={lambda_mem}, "
        f"forgetting_rate={forgetting_rate:.4f}"
    )
    
    return forgetting_rate


def load_actr_config_from_db(
    db: Session,
    config_id: Optional[UUID] = None
) -> Dict[str, Any]:
    """
    从数据库同步加载 ACT-R 配置参数

    从 PostgreSQL 的 memory_config 表读取配置参数，
    并计算派生参数（如 forgetting_rate）。同步版本，适用于 Celery 等同步场景。

    与 load_actr_config_from_db_async 逻辑完全一致，
    仅数据访问路径不同（Repository vs 原生 select），保持两套方案独立。

    Args:
        db: 数据库会话
        config_id: 配置 ID（可选，如果为 None 则使用默认值）

    Returns:
        Dict[str, Any]: 配置参数字典

    Raises:
        ValueError: 如果指定的 config_id 不存在
    """
    if config_id is None:
        logger.error("未指定 config_id，无法加载配置")
        raise ValueError("config_id 不能为空，必须指定一个有效的配置 ID")

    try:
        db_config = MemoryConfigRepository.get_by_id(db, config_id)
        if db_config is None:
            logger.error(f"配置不存在: config_id={config_id}")
            raise ValueError(f"配置不存在: config_id={config_id}")

        lambda_time = db_config.lambda_time
        lambda_mem = db_config.lambda_mem
        forgetting_rate = calculate_forgetting_rate(lambda_time, lambda_mem)

        config = {
            'decay_constant': db_config.decay_constant,
            'lambda_time': lambda_time,
            'lambda_mem': lambda_mem,
            'forgetting_rate': forgetting_rate,
            'offset': db_config.offset,
            'max_history_length': db_config.max_history_length,
            'forgetting_threshold': db_config.forgetting_threshold,
            'min_days_since_access': db_config.min_days_since_access,
            'enable_llm_summary': db_config.enable_llm_summary,
            'max_merge_batch_size': db_config.max_merge_batch_size,
            'forgetting_interval_hours': db_config.forgetting_interval_hours,
            'is_default': bool(db_config.is_default),
        }

        logger.info(
            f"成功加载 ACT-R 配置: config_id={config_id}, "
            f"forgetting_rate={forgetting_rate:.4f}"
        )
        return config

    except Exception as e:
        logger.error(f"加载 ACT-R 配置失败: config_id={config_id}, 错误: {str(e)}")
        raise


async def load_actr_config_from_db_async(
    db: AsyncSession,
    config_id: Optional[UUID] = None
) -> Dict[str, Any]:
    """
    从数据库异步加载 ACT-R 配置参数

    从 PostgreSQL 的 memory_config 表读取配置参数，
    并计算派生参数（如 forgetting_rate）。异步版本，适用于 FastAPI 请求处理。

    Args:
        db: 异步数据库会话
        config_id: 配置 ID（可选，如果为 None 则使用默认值）

    Returns:
        Dict[str, Any]: 配置参数字典

    Raises:
        ValueError: 如果指定的 config_id 不存在
    """
    if config_id is None:
        logger.error("未指定 config_id，无法加载配置")
        raise ValueError("config_id 不能为空，必须指定一个有效的配置 ID")

    try:
        result = await db.execute(
            select(MemoryConfig).where(MemoryConfig.config_id == config_id)
        )
        db_config = result.scalars().first()

        if db_config is None:
            logger.error(f"配置不存在: config_id={config_id}")
            raise ValueError(f"配置不存在: config_id={config_id}")

        # 读取配置参数（信任数据库默认值）
        lambda_time = db_config.lambda_time
        lambda_mem = db_config.lambda_mem
        decay_constant = db_config.decay_constant
        offset = db_config.offset
        max_history_length = db_config.max_history_length
        forgetting_threshold = db_config.forgetting_threshold
        min_days_since_access = db_config.min_days_since_access
        enable_llm_summary = db_config.enable_llm_summary
        max_merge_batch_size = db_config.max_merge_batch_size
        forgetting_interval_hours = db_config.forgetting_interval_hours
        is_default = db_config.is_default

        # 计算 forgetting_rate
        forgetting_rate = calculate_forgetting_rate(lambda_time, lambda_mem)

        config = {
            'decay_constant': decay_constant,
            'lambda_time': lambda_time,
            'lambda_mem': lambda_mem,
            'forgetting_rate': forgetting_rate,
            'offset': offset,
            'max_history_length': max_history_length,
            'forgetting_threshold': forgetting_threshold,
            'min_days_since_access': min_days_since_access,
            'enable_llm_summary': enable_llm_summary,
            'max_merge_batch_size': max_merge_batch_size,
            'forgetting_interval_hours': forgetting_interval_hours,
            'is_default': bool(is_default)
        }

        logger.info(
            f"成功加载 ACT-R 配置: config_id={config_id}, "
            f"forgetting_rate={forgetting_rate:.4f}"
        )

        return config

    except Exception as e:
        logger.error(f"加载 ACT-R 配置失败: config_id={config_id}, 错误: {str(e)}")
        raise

