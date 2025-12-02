# app/core/transaction_monitor.py
"""
事务监控模块

提供事务持续时间监控、长事务检测和告警功能。
"""

import time
import threading
from typing import Optional, Callable, Dict, Any
from contextlib import contextmanager
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class TransactionMonitor:
    """
    事务监控器
    
    功能:
    - 监控事务持续时间
    - 检测长事务
    - 记录事务统计信息
    - 发出长事务告警
    """
    
    # 默认长事务阈值（秒）
    DEFAULT_LONG_TRANSACTION_THRESHOLD = 5.0
    
    # 警告阈值（秒）
    DEFAULT_WARNING_THRESHOLD = 2.0
    
    def __init__(
        self,
        long_transaction_threshold: float = DEFAULT_LONG_TRANSACTION_THRESHOLD,
        warning_threshold: float = DEFAULT_WARNING_THRESHOLD,
        enable_monitoring: bool = True
    ):
        """
        初始化事务监控器
        
        Args:
            long_transaction_threshold: 长事务阈值（秒），超过此时间视为长事务
            warning_threshold: 警告阈值（秒），超过此时间发出警告
            enable_monitoring: 是否启用监控
        """
        self.long_transaction_threshold = long_transaction_threshold
        self.warning_threshold = warning_threshold
        self.enable_monitoring = enable_monitoring
        
        # 事务统计
        self._stats = {
            "total_transactions": 0,
            "long_transactions": 0,
            "warning_transactions": 0,
            "total_duration": 0.0,
            "max_duration": 0.0,
            "min_duration": float('inf')
        }
        
        # 线程本地存储，用于跟踪当前事务
        self._local = threading.local()
    
    @contextmanager
    def monitor_transaction(
        self,
        transaction_name: str = "unnamed",
        context: Optional[Dict[str, Any]] = None
    ):
        """
        监控事务执行
        
        使用示例:
            with monitor.monitor_transaction("create_user"):
                # 执行事务操作
                pass
        
        Args:
            transaction_name: 事务名称，用于日志记录
            context: 事务上下文信息（如 user_id, tenant_id 等）
        """
        if not self.enable_monitoring:
            yield
            return
        
        # 记录开始时间
        start_time = time.time()
        context = context or {}
        
        # 存储到线程本地
        self._local.transaction_name = transaction_name
        self._local.start_time = start_time
        self._local.context = context
        
        logger.debug(
            "transaction_started",
            transaction_name=transaction_name,
            **context
        )
        
        try:
            yield
        finally:
            # 计算持续时间
            duration = time.time() - start_time
            
            # 更新统计
            self._update_stats(duration)
            
            # 检查是否为长事务
            self._check_transaction_duration(
                transaction_name,
                duration,
                context
            )
            
            logger.debug(
                "transaction_completed",
                transaction_name=transaction_name,
                duration_seconds=round(duration, 3),
                **context
            )
    
    def _update_stats(self, duration: float):
        """更新事务统计信息"""
        self._stats["total_transactions"] += 1
        self._stats["total_duration"] += duration
        self._stats["max_duration"] = max(self._stats["max_duration"], duration)
        self._stats["min_duration"] = min(self._stats["min_duration"], duration)
        
        if duration >= self.long_transaction_threshold:
            self._stats["long_transactions"] += 1
        elif duration >= self.warning_threshold:
            self._stats["warning_transactions"] += 1
    
    def _check_transaction_duration(
        self,
        transaction_name: str,
        duration: float,
        context: Dict[str, Any]
    ):
        """
        检查事务持续时间并发出告警
        
        Args:
            transaction_name: 事务名称
            duration: 事务持续时间（秒）
            context: 事务上下文
        """
        if duration >= self.long_transaction_threshold:
            # 长事务告警
            logger.warning(
                f"Long transaction detected: {transaction_name} took {round(duration, 3)}s "
                f"(threshold: {self.long_transaction_threshold}s). "
                f"Consider breaking down the transaction or moving non-critical operations outside. "
                f"Context: {context}"
            )
        elif duration >= self.warning_threshold:
            # 警告级别
            logger.info(
                f"Slow transaction detected: {transaction_name} took {round(duration, 3)}s "
                f"(threshold: {self.warning_threshold}s). "
                f"Monitor this transaction for potential optimization. "
                f"Context: {context}"
            )
    
    def get_stats(self) -> Dict[str, Any]:
        """
        获取事务统计信息
        
        Returns:
            包含统计信息的字典
        """
        if self._stats["total_transactions"] == 0:
            avg_duration = 0.0
        else:
            avg_duration = self._stats["total_duration"] / self._stats["total_transactions"]
        
        return {
            **self._stats,
            "avg_duration": round(avg_duration, 3),
            "long_transaction_rate": (
                self._stats["long_transactions"] / self._stats["total_transactions"]
                if self._stats["total_transactions"] > 0 else 0.0
            ),
            "warning_transaction_rate": (
                self._stats["warning_transactions"] / self._stats["total_transactions"]
                if self._stats["total_transactions"] > 0 else 0.0
            )
        }
    
    def reset_stats(self):
        """重置统计信息"""
        self._stats = {
            "total_transactions": 0,
            "long_transactions": 0,
            "warning_transactions": 0,
            "total_duration": 0.0,
            "max_duration": 0.0,
            "min_duration": float('inf')
        }
        logger.info("transaction_stats_reset")
    
    def print_stats(self):
        """打印统计信息（用于调试）"""
        stats = self.get_stats()
        print("\n" + "=" * 60)
        print("Transaction Statistics")
        print("=" * 60)
        print(f"Total Transactions:     {stats['total_transactions']}")
        print(f"Long Transactions:      {stats['long_transactions']} ({stats['long_transaction_rate']:.1%})")
        print(f"Warning Transactions:   {stats['warning_transactions']} ({stats['warning_transaction_rate']:.1%})")
        print(f"Average Duration:       {stats['avg_duration']:.3f}s")
        print(f"Max Duration:           {stats['max_duration']:.3f}s")
        print(f"Min Duration:           {stats['min_duration']:.3f}s")
        print("=" * 60 + "\n")


# 全局事务监控器实例
transaction_monitor = TransactionMonitor(
    long_transaction_threshold=5.0,  # 5秒
    warning_threshold=2.0,  # 2秒
    enable_monitoring=True
)


def get_transaction_monitor() -> TransactionMonitor:
    """获取全局事务监控器实例"""
    return transaction_monitor
