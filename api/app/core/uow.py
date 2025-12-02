"""
Unit of Work Pattern Implementation
Manages database transactions and coordinates multiple repositories.

事务边界管理:
- 使用 with 语句明确事务边界
- 所有数据库操作必须在 with 块内执行
- 必须显式调用 commit() 提交事务
- 异常会自动触发回滚

长事务监控:
- 自动监控事务持续时间
- 检测并告警长事务（默认 > 5秒）
- 提供事务性能统计
"""
from abc import ABC, abstractmethod
from typing import Callable, TypeVar, Generic, Optional, Dict, Any
from sqlalchemy.orm import Session
import time

from app.repositories.generic_file_repository import GenericFileRepository
from app.repositories.user_repository import UserRepository
from app.repositories.workspace_repository import WorkspaceRepository
from app.repositories.workspace_invite_repository import WorkspaceInviteRepository
from app.repositories.tenant_repository import TenantRepository
from app.repositories.model_repository import ModelConfigRepository, ModelApiKeyRepository
from app.core.logging_config import get_logger

logger = get_logger(__name__)

T = TypeVar('T')


class IUnitOfWork(ABC):
    """工作单元接口"""
    
    files: GenericFileRepository
    users: UserRepository
    workspaces: WorkspaceRepository
    workspace_invites: WorkspaceInviteRepository
    tenants: TenantRepository
    model_configs: ModelConfigRepository
    model_api_keys: ModelApiKeyRepository
    
    @abstractmethod
    def __enter__(self):
        """进入上下文"""
        pass
    
    @abstractmethod
    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出上下文"""
        pass
    
    @abstractmethod
    def commit(self):
        """提交事务"""
        pass
    
    @abstractmethod
    def rollback(self):
        """回滚事务"""
        pass


class SqlAlchemyUnitOfWork(IUnitOfWork):
    """
    SQLAlchemy 工作单元实现
    
    事务边界说明:
    - __enter__: 开始事务 (创建新的 session)
    - __exit__: 结束事务 (自动回滚异常，关闭 session)
    - commit(): 显式提交事务
    - rollback(): 显式回滚事务
    
    长事务监控:
    - 自动记录事务开始时间
    - 在事务结束时计算持续时间
    - 超过阈值时发出告警
    
    使用示例:
        with uow:
            # 事务开始
            user = uow.users.create_user(data)
            workspace = uow.workspaces.create_workspace(data)
            # 所有操作在同一事务中
            uow.commit()
            # 事务提交
        # 事务结束，session 关闭
    """
    
    # 长事务阈值（秒）
    LONG_TRANSACTION_THRESHOLD = 5.0
    WARNING_THRESHOLD = 2.0
    
    def __init__(
        self,
        session_factory: Callable[[], Session],
        transaction_name: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        enable_monitoring: bool = True
    ):
        self.session_factory = session_factory
        self._session: Session = None
        self._transaction_active = False
        self._transaction_name = transaction_name if transaction_name is not None else "unnamed"
        self._context = context or {}
        self._enable_monitoring = enable_monitoring
        self._start_time: Optional[float] = None
    
    def __enter__(self):
        """
        进入事务上下文
        创建新的数据库 session 并开始事务
        同时开始监控事务持续时间
        """
        self._session = self.session_factory()
        self._transaction_active = True
        
        # 记录事务开始时间
        if self._enable_monitoring:
            self._start_time = time.time()
            logger.debug(
                "transaction_started",
                transaction_name=self._transaction_name,
                **self._context
            )
        
        # 初始化所有仓储，共享同一个 session
        # 确保所有仓储操作在同一事务中
        self.files = GenericFileRepository(self._session)
        self.users = UserRepository(self._session)
        self.workspaces = WorkspaceRepository(self._session)
        self.workspace_invites = WorkspaceInviteRepository(self._session)
        self.tenants = TenantRepository(self._session)
        
        # Note: ModelConfigRepository and ModelApiKeyRepository use static methods
        # They don't need session in constructor, but we provide access to the session
        self.model_configs = ModelConfigRepository
        self.model_api_keys = ModelApiKeyRepository
        self.session = self._session  # Provide direct access to session for static method repositories
        
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        退出事务上下文
        
        如果发生异常:
        - 自动回滚事务
        - 关闭 session
        
        如果没有异常:
        - 仅关闭 session (需要显式调用 commit)
        
        同时检查事务持续时间并发出告警
        """
        try:
            if exc_type is not None:
                # 异常发生，自动回滚
                self.rollback()
        finally:
            # 检查事务持续时间
            if self._enable_monitoring and self._start_time is not None:
                duration = time.time() - self._start_time
                self._check_transaction_duration(duration)
                
                logger.debug(
                    "transaction_completed",
                    transaction_name=self._transaction_name,
                    duration_seconds=round(duration, 3),
                    **self._context
                )
            
            # 无论如何都要关闭 session
            self._session.close()
            self._transaction_active = False
    
    def commit(self):
        """
        显式提交事务
        
        注意: 必须在 with 块内调用
        提交后事务仍然活跃，可以继续操作
        """
        if not self._transaction_active:
            raise RuntimeError("Cannot commit: transaction is not active")
        
        logger.debug("Committing transaction")
        self._session.commit()
        logger.debug("Transaction committed successfully")
    
    def rollback(self):
        """
        显式回滚事务
        
        注意: 必须在 with 块内调用
        回滚后事务仍然活跃，可以继续操作
        """
        if not self._transaction_active:
            raise RuntimeError("Cannot rollback: transaction is not active")
        
        logger.debug("Rolling back transaction")
        self._session.rollback()
        logger.debug("Transaction rolled back successfully")
    
    def _check_transaction_duration(self, duration: float):
        """
        检查事务持续时间并发出告警
        
        Args:
            duration: 事务持续时间（秒）
        """
        if duration >= self.LONG_TRANSACTION_THRESHOLD:
            # 长事务告警
            logger.warning(
                f"Long transaction detected: {self._transaction_name} took {round(duration, 3)}s "
                f"(threshold: {self.LONG_TRANSACTION_THRESHOLD}s). "
                f"Consider breaking down the transaction or moving non-critical operations outside. "
                f"Context: {self._context}"
            )
        elif duration >= self.WARNING_THRESHOLD:
            # 警告级别
            logger.info(
                f"Slow transaction detected: {self._transaction_name} took {round(duration, 3)}s "
                f"(threshold: {self.WARNING_THRESHOLD}s). "
                f"Monitor this transaction for potential optimization. "
                f"Context: {self._context}"
            )
    
    def execute_in_transaction(self, func: Callable[[IUnitOfWork], T]) -> T:
        """
        在事务中执行函数，自动管理事务边界
        
        这是一个便捷方法，用于明确事务边界:
        - 自动开始事务
        - 执行函数
        - 自动提交事务
        - 异常时自动回滚
        
        Args:
            func: 接受 UoW 作为参数的函数
            
        Returns:
            函数的返回值
            
        Example:
            def create_user_and_workspace(uow):
                user = uow.users.create_user(user_data)
                workspace = uow.workspaces.create_workspace(ws_data)
                return user, workspace
            
            result = uow.execute_in_transaction(create_user_and_workspace)
        """
        logger.debug("Starting transaction execution")
        with self:
            try:
                result = func(self)
                self.commit()
                logger.debug("Transaction execution completed successfully")
                return result
            except Exception as e:
                logger.error(f"Transaction execution failed: {str(e)}")
                # Rollback is automatic in __exit__
                raise
