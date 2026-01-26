"""
遗忘引擎配置工具模块

本模块提供从数据库加载配置并创建遗忘引擎组件的辅助函数。

Functions:
    calculate_forgetting_rate: 计算遗忘速率（lambda_time / lambda_mem）
    load_actr_config_from_db: 从数据库加载 ACT-R 配置参数
    create_actr_calculator_from_config: 从配置创建 ACTRCalculator 实例
"""

import logging
from typing import Optional, Dict, Any
from uuid import UUID
from sqlalchemy.orm import Session

from app.repositories.memory_config_repository import MemoryConfigRepository
from app.core.memory.storage_services.forgetting_engine.actr_calculator import ACTRCalculator


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
    从数据库加载 ACT-R 配置参数
    
    从 PostgreSQL 的 memory_config 表读取配置参数，
    并计算派生参数（如 forgetting_rate）。
    
    Args:
        db: 数据库会话
        config_id: 配置 ID（可选，如果为 None 则使用默认值）
    
    Returns:
        Dict[str, Any]: 配置参数字典，包含：
            - decay_constant: 衰减常数 d
            - lambda_time: 时间衰减参数
            - lambda_mem: 记忆衰减参数
            - forgetting_rate: 遗忘速率（根据 lambda_time / lambda_mem 计算得出）
            - offset: 偏移量
            - max_history_length: 访问历史最大长度
            - forgetting_threshold: 遗忘阈值
            - min_days_since_access: 最小未访问天数
            - enable_llm_summary: 是否使用 LLM 生成摘要
            - max_merge_batch_size: 单次最大融合节点对数
            - forgetting_interval_hours: 遗忘周期间隔
            
        注意：llm_id 不包含在返回的配置中，需要时由 forgetting_strategy 直接从数据库读取
    
    Raises:
        ValueError: 如果指定的 config_id 不存在
    """
    # 必须指定 config_id
    if config_id is None:
        logger.error("未指定 config_id，无法加载配置")
        raise ValueError("config_id 不能为空，必须指定一个有效的配置 ID")
    
    # 从数据库加载配置
    try:
        repository = MemoryConfigRepository()
        db_config = repository.get_by_id(db, config_id)
        
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
            'forgetting_interval_hours': forgetting_interval_hours
            # 注意：llm_id 不包含在配置响应中，仅在内部使用
        }
        
        logger.info(
            f"成功加载 ACT-R 配置: config_id={config_id}, "
            f"forgetting_rate={forgetting_rate:.4f}"
        )
        
        return config
    
    except Exception as e:
        logger.error(f"加载 ACT-R 配置失败: config_id={config_id}, 错误: {str(e)}")
        raise


def create_actr_calculator_from_config(
    db: Session,
    config_id: Optional[UUID] = None
) -> ACTRCalculator:
    """
    从数据库配置创建 ACTRCalculator 实例
    
    这是创建 ACTRCalculator 的推荐方式，确保使用数据库中的配置参数。
    
    Args:
        db: 数据库会话
        config_id: 配置 ID（可选，如果为 None 则使用默认值）
    
    Returns:
        ACTRCalculator: 配置好的 ACT-R 计算器实例
    
    Raises:
        ValueError: 如果指定的 config_id 不存在
    
    Examples:
    """
    # 加载配置
    config = load_actr_config_from_db(db, config_id)
    
    # 创建计算器
    calculator = ACTRCalculator(
        decay_constant=config['decay_constant'],
        forgetting_rate=config['forgetting_rate'],
        offset=config['offset'],
        max_history_length=config['max_history_length']
    )
    
    logger.info(
        f"创建 ACTRCalculator: config_id={config_id}, "
        f"decay_constant={config['decay_constant']}, "
        f"forgetting_rate={config['forgetting_rate']:.4f}, "
        f"offset={config['offset']}"
    )
    
    return calculator
