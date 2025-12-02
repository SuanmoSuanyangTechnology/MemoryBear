# -*- coding: utf-8 -*-
"""仓储模块

本模块提供统一的数据访问层，包括PostgreSQL和Neo4j的仓储实现。

Classes:
    RepositoryFactory: 仓储工厂，统一管理所有数据库的仓储实例
"""

from typing import Optional
from sqlalchemy.orm import Session

from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.repositories.neo4j.dialog_repository import DialogRepository
from app.repositories.neo4j.statement_repository import StatementRepository
from app.repositories.neo4j.entity_repository import EntityRepository
from app.repositories.user_repository import UserRepository
from app.repositories.workspace_repository import WorkspaceRepository
from app.repositories.app_repository import AppRepository


class RepositoryFactory:
    """仓储工厂 - 统一管理所有数据库的仓储
    
    这个工厂类提供了获取各种仓储实例的统一接口。
    支持Neo4j图数据库和PostgreSQL关系数据库的仓储。
    
    Attributes:
        neo4j_connector: Neo4j连接器实例（可选）
        db_session: SQLAlchemy数据库会话（可选）
        
    Example:
        >>> # 创建工厂实例
        >>> factory = RepositoryFactory(
        ...     neo4j_connector=Neo4jConnector(),
        ...     db_session=db_session
        ... )
        >>> 
        >>> # 获取Neo4j仓储
        >>> dialog_repo = factory.get_dialog_repository()
        >>> statement_repo = factory.get_statement_repository()
        >>> 
        >>> # 获取PostgreSQL仓储
        >>> knowledge_repo = factory.get_knowledge_repository()
    """
    
    def __init__(
        self,
        neo4j_connector: Optional[Neo4jConnector] = None,
        db_session: Optional[Session] = None
    ):
        """初始化仓储工厂
        
        Args:
            neo4j_connector: Neo4j连接器实例（可选）
            db_session: SQLAlchemy数据库会话（可选）
        """
        self.neo4j_connector = neo4j_connector
        self.db_session = db_session
    
    # ==================== Neo4j 仓储 ====================
    
    def get_dialog_repository(self) -> DialogRepository:
        """获取对话仓储
        
        Returns:
            DialogRepository: 对话仓储实例
            
        Raises:
            ValueError: 如果Neo4j连接器未初始化
        """
        if not self.neo4j_connector:
            raise ValueError("Neo4j connector not initialized")
        return DialogRepository(self.neo4j_connector)
    
    def get_statement_repository(self) -> StatementRepository:
        """获取陈述句仓储
        
        Returns:
            StatementRepository: 陈述句仓储实例
            
        Raises:
            ValueError: 如果Neo4j连接器未初始化
        """
        if not self.neo4j_connector:
            raise ValueError("Neo4j connector not initialized")
        return StatementRepository(self.neo4j_connector)
    
    def get_entity_repository(self) -> EntityRepository:
        """获取实体仓储
        
        Returns:
            EntityRepository: 实体仓储实例
            
        Raises:
            ValueError: 如果Neo4j连接器未初始化
        """
        if not self.neo4j_connector:
            raise ValueError("Neo4j connector not initialized")
        return EntityRepository(self.neo4j_connector)
    
    # ==================== PostgreSQL 仓储 ====================
    # 注意：现有的PostgreSQL仓储保持不变，这里只是提供统一的访问接口
    # 部分仓储（如knowledge_repository、document_repository）使用函数式接口
    # 部分仓储（如user_repository、workspace_repository）使用类接口
    
    def get_user_repository(self) -> UserRepository:
        """获取用户仓储
        
        Returns:
            UserRepository: 用户仓储实例
            
        Raises:
            ValueError: 如果数据库会话未初始化
        """
        if not self.db_session:
            raise ValueError("Database session not initialized")
        return UserRepository(self.db_session)
    
    def get_workspace_repository(self) -> WorkspaceRepository:
        """获取工作空间仓储
        
        Returns:
            WorkspaceRepository: 工作空间仓储实例
            
        Raises:
            ValueError: 如果数据库会话未初始化
        """
        if not self.db_session:
            raise ValueError("Database session not initialized")
        return WorkspaceRepository(self.db_session)
    
    def get_app_repository(self) -> AppRepository:
        """获取应用仓储
        
        Returns:
            AppRepository: 应用仓储实例
            
        Raises:
            ValueError: 如果数据库会话未初始化
        """
        if not self.db_session:
            raise ValueError("Database session not initialized")
        return AppRepository(self.db_session)
    
    def get_db_session(self) -> Session:
        """获取数据库会话
        
        用于访问函数式仓储（如knowledge_repository、document_repository）
        
        Returns:
            Session: SQLAlchemy数据库会话
            
        Raises:
            ValueError: 如果数据库会话未初始化
            
        Example:
            >>> factory = RepositoryFactory(db_session=session)
            >>> db = factory.get_db_session()
            >>> # 使用函数式仓储
            >>> from app.repositories import knowledge_repository
            >>> knowledges = knowledge_repository.get_knowledges_paginated(db, [], 1, 10)
        """
        if not self.db_session:
            raise ValueError("Database session not initialized")
        return self.db_session


__all__ = [
    'RepositoryFactory',
]
