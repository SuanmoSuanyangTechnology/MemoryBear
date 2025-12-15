"""
Compensation Transaction Handler
Handles operations that cannot be rolled back (like file system operations).
"""
from typing import List, Callable
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class CompensationHandler:
    """补偿事务处理器，用于处理无法回滚的操作"""
    
    def __init__(self):
        self._compensations: List[Callable] = []
    
    def register(self, compensation: Callable):
        """
        注册补偿操作
        
        Args:
            compensation: 补偿操作的可调用对象
        """
        self._compensations.append(compensation)
        logger.debug(f"Registered compensation operation: {compensation.__name__ if hasattr(compensation, '__name__') else 'lambda'}")
    
    def execute(self):
        """执行所有补偿操作（按注册的逆序执行）"""
        if not self._compensations:
            logger.debug("No compensation operations to execute")
            return
        
        logger.info(f"Executing {len(self._compensations)} compensation operations")
        
        for compensation in reversed(self._compensations):
            try:
                compensation()
                logger.debug("Compensation operation executed successfully")
            except Exception as e:
                logger.error(f"补偿操作失败: {e}", exc_info=True)
    
    def clear(self):
        """清空补偿操作"""
        count = len(self._compensations)
        self._compensations.clear()
        if count > 0:
            logger.debug(f"Cleared {count} compensation operations")
