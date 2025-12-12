# -*- coding: utf-8 -*-
"""Neo4j仓储单元测试

本模块测试Neo4j仓储层的CRUD操作和查询功能。
使用mock隔离数据库依赖，确保测试的独立性和可重复性。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime
from uuid import uuid4

from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.repositories.neo4j.dialog_repository import DialogRepository
from app.repositories.neo4j.statement_repository import StatementRepository
from app.repositories.neo4j.entity_repository import EntityRepository
from app.core.memory.models.graph_models import (
    DialogueNode,
    StatementNode,
    ExtractedEntityNode
)
from app.core.memory.utils.ontology import TemporalInfo


# ==================== Fixtures ====================

@pytest.fixture
def mock_connector():
    """创建mock的Neo4j连接器"""
    connector = AsyncMock(spec=Neo4jConnector)
    return connector


@pytest.fixture
def dialog_repository(mock_connector):
    """创建对话仓储实例"""
    return DialogRepository(mock_connector)


@pytest.fixture
def statement_repository(mock_connector):
    """创建陈述句仓储实例"""
    return StatementRepository(mock_connector)


@pytest.fixture
def entity_repository(mock_connector):
    """创建实体仓储实例"""
    return EntityRepository(mock_connector)


@pytest.fixture
def sample_dialog():
    """创建示例对话节点"""
    return DialogueNode(
        id=uuid4().hex,
        name="测试对话",
        ref_id="ref_123",
        content="用户: 你好\\nAI: 你好，有什么可以帮助你的？",
        group_id="group_123",
        user_id="user_456",
        apply_id="app_789",
        created_at=datetime.now(),
        dialog_embedding=[0.1, 0.2, 0.3]
    )


@pytest.fixture
def sample_statement():
    """创建示例陈述句节点"""
    return StatementNode(
        id=uuid4().hex,
        name="测试陈述句",
        chunk_id="chunk_123",
        stmt_type="Fact",
        temporal_info=TemporalInfo.STATIC,
        statement="这是一个测试陈述句",
        connect_strength="Strong",
        group_id="group_123",
        user_id="user_456",
        apply_id="app_789",
        created_at=datetime.now(),
        statement_embedding=[0.1, 0.2, 0.3]
    )


@pytest.fixture
def sample_entity():
    """创建示例实体节点"""
    return ExtractedEntityNode(
        id=uuid4().hex,
        name="测试实体",
        entity_idx=1,
        statement_id="stmt_123",
        entity_type="Person",
        description="这是一个测试实体",
        fact_summary="测试事实摘要",
        connect_strength="Strong",
        group_id="group_123",
        user_id="user_456",
        apply_id="app_789",
        created_at=datetime.now(),
        name_embedding=[0.1, 0.2, 0.3]
    )


# ==================== DialogRepository Tests ====================

@pytest.mark.anyio
async def test_dialog_repository_create(dialog_repository, mock_connector, sample_dialog):
    """测试创建对话节点"""
    # Arrange
    mock_connector.execute_query.return_value = [{"n": sample_dialog.model_dump()}]
    
    # Act
    result = await dialog_repository.create(sample_dialog)
    
    # Assert
    assert result == sample_dialog
    mock_connector.execute_query.assert_called_once()


@pytest.mark.anyio
async def test_dialog_repository_get_by_id(dialog_repository, mock_connector, sample_dialog):
    """测试根据ID获取对话"""
    # Arrange
    mock_connector.execute_query.return_value = [{"n": sample_dialog.model_dump()}]
    
    # Act
    result = await dialog_repository.get_by_id(sample_dialog.id)
    
    # Assert
    assert result is not None
    assert result.id == sample_dialog.id


@pytest.mark.anyio
async def test_dialog_repository_delete(dialog_repository, mock_connector):
    """测试删除对话节点"""
    # Arrange
    mock_connector.execute_query.return_value = [{"deleted": 1}]
    
    # Act
    result = await dialog_repository.delete("dialog_123")
    
    # Assert
    assert result is True


# ==================== StatementRepository Tests ====================

@pytest.mark.anyio
async def test_statement_repository_create(statement_repository, mock_connector, sample_statement):
    """测试创建陈述句节点"""
    # Arrange
    mock_connector.execute_query.return_value = [{"n": sample_statement.model_dump()}]
    
    # Act
    result = await statement_repository.create(sample_statement)
    
    # Assert
    assert result == sample_statement


@pytest.mark.anyio
async def test_statement_repository_find_by_chunk_id(statement_repository, mock_connector, sample_statement):
    """测试根据chunk_id查询陈述句"""
    # Arrange
    mock_connector.execute_query.return_value = [{"n": sample_statement.model_dump()}]
    
    # Act
    results = await statement_repository.find_by_chunk_id("chunk_123")
    
    # Assert
    assert len(results) == 1
    assert results[0].chunk_id == "chunk_123"


# ==================== EntityRepository Tests ====================

@pytest.mark.anyio
async def test_entity_repository_create(entity_repository, mock_connector, sample_entity):
    """测试创建实体节点"""
    # Arrange
    mock_connector.execute_query.return_value = [{"n": sample_entity.model_dump()}]
    
    # Act
    result = await entity_repository.create(sample_entity)
    
    # Assert
    assert result == sample_entity


@pytest.mark.anyio
async def test_entity_repository_find_by_type(entity_repository, mock_connector, sample_entity):
    """测试根据类型查询实体"""
    # Arrange
    mock_connector.execute_query.return_value = [{"n": sample_entity.model_dump()}]
    
    # Act
    results = await entity_repository.find_by_type("Person")
    
    # Assert
    assert len(results) == 1
    assert results[0].entity_type == "Person"


# ==================== RepositoryFactory Tests ====================

def test_repository_factory_initialization_with_neo4j():
    """测试使用Neo4j连接器初始化仓储工厂"""
    from app.repositories import RepositoryFactory
    
    # Arrange
    mock_connector = MagicMock(spec=Neo4jConnector)
    
    # Act
    factory = RepositoryFactory(neo4j_connector=mock_connector)
    
    # Assert
    assert factory.neo4j_connector is mock_connector
    assert factory.db_session is None


def test_repository_factory_initialization_with_db_session():
    """测试使用数据库会话初始化仓储工厂"""
    from app.repositories import RepositoryFactory
    from sqlalchemy.orm import Session
    
    # Arrange
    mock_session = MagicMock(spec=Session)
    
    # Act
    factory = RepositoryFactory(db_session=mock_session)
    
    # Assert
    assert factory.db_session is mock_session
    assert factory.neo4j_connector is None


def test_repository_factory_initialization_with_both():
    """测试同时使用Neo4j连接器和数据库会话初始化仓储工厂"""
    from app.repositories import RepositoryFactory
    from sqlalchemy.orm import Session
    
    # Arrange
    mock_connector = MagicMock(spec=Neo4jConnector)
    mock_session = MagicMock(spec=Session)
    
    # Act
    factory = RepositoryFactory(
        neo4j_connector=mock_connector,
        db_session=mock_session
    )
    
    # Assert
    assert factory.neo4j_connector is mock_connector
    assert factory.db_session is mock_session


def test_repository_factory_get_dialog_repository():
    """测试仓储工厂获取对话仓储"""
    from app.repositories import RepositoryFactory
    
    # Arrange
    mock_connector = MagicMock(spec=Neo4jConnector)
    factory = RepositoryFactory(neo4j_connector=mock_connector)
    
    # Act
    repo = factory.get_dialog_repository()
    
    # Assert
    assert isinstance(repo, DialogRepository)
    assert repo.connector is mock_connector


def test_repository_factory_get_statement_repository():
    """测试仓储工厂获取陈述句仓储"""
    from app.repositories import RepositoryFactory
    
    # Arrange
    mock_connector = MagicMock(spec=Neo4jConnector)
    factory = RepositoryFactory(neo4j_connector=mock_connector)
    
    # Act
    repo = factory.get_statement_repository()
    
    # Assert
    assert isinstance(repo, StatementRepository)
    assert repo.connector is mock_connector


def test_repository_factory_get_entity_repository():
    """测试仓储工厂获取实体仓储"""
    from app.repositories import RepositoryFactory
    
    # Arrange
    mock_connector = MagicMock(spec=Neo4jConnector)
    factory = RepositoryFactory(neo4j_connector=mock_connector)
    
    # Act
    repo = factory.get_entity_repository()
    
    # Assert
    assert isinstance(repo, EntityRepository)
    assert repo.connector is mock_connector


def test_repository_factory_get_user_repository():
    """测试仓储工厂获取用户仓储"""
    from app.repositories import RepositoryFactory
    from app.repositories.user_repository import UserRepository
    from sqlalchemy.orm import Session
    
    # Arrange
    mock_session = MagicMock(spec=Session)
    factory = RepositoryFactory(db_session=mock_session)
    
    # Act
    repo = factory.get_user_repository()
    
    # Assert
    assert isinstance(repo, UserRepository)
    assert repo.db is mock_session


def test_repository_factory_get_workspace_repository():
    """测试仓储工厂获取工作空间仓储"""
    from app.repositories import RepositoryFactory
    from app.repositories.workspace_repository import WorkspaceRepository
    from sqlalchemy.orm import Session
    
    # Arrange
    mock_session = MagicMock(spec=Session)
    factory = RepositoryFactory(db_session=mock_session)
    
    # Act
    repo = factory.get_workspace_repository()
    
    # Assert
    assert isinstance(repo, WorkspaceRepository)
    assert repo.db is mock_session


def test_repository_factory_get_app_repository():
    """测试仓储工厂获取应用仓储"""
    from app.repositories import RepositoryFactory
    from app.repositories.app_repository import AppRepository
    from sqlalchemy.orm import Session
    
    # Arrange
    mock_session = MagicMock(spec=Session)
    factory = RepositoryFactory(db_session=mock_session)
    
    # Act
    repo = factory.get_app_repository()
    
    # Assert
    assert isinstance(repo, AppRepository)
    assert repo.db is mock_session


def test_repository_factory_get_db_session():
    """测试仓储工厂获取数据库会话（用于函数式仓储）"""
    from app.repositories import RepositoryFactory
    from sqlalchemy.orm import Session
    
    # Arrange
    mock_session = MagicMock(spec=Session)
    factory = RepositoryFactory(db_session=mock_session)
    
    # Act
    db = factory.get_db_session()
    
    # Assert
    assert db is mock_session


def test_repository_factory_without_connector():
    """测试未初始化连接器时获取Neo4j仓储"""
    from app.repositories import RepositoryFactory
    
    # Arrange
    factory = RepositoryFactory()
    
    # Act & Assert
    with pytest.raises(ValueError, match="Neo4j connector not initialized"):
        factory.get_dialog_repository()
    
    with pytest.raises(ValueError, match="Neo4j connector not initialized"):
        factory.get_statement_repository()
    
    with pytest.raises(ValueError, match="Neo4j connector not initialized"):
        factory.get_entity_repository()


def test_repository_factory_without_db_session():
    """测试未初始化数据库会话时获取PostgreSQL仓储"""
    from app.repositories import RepositoryFactory
    
    # Arrange
    factory = RepositoryFactory()
    
    # Act & Assert
    with pytest.raises(ValueError, match="Database session not initialized"):
        factory.get_user_repository()
    
    with pytest.raises(ValueError, match="Database session not initialized"):
        factory.get_workspace_repository()
    
    with pytest.raises(ValueError, match="Database session not initialized"):
        factory.get_app_repository()
    
    with pytest.raises(ValueError, match="Database session not initialized"):
        factory.get_db_session()


def test_repository_factory_multiple_repository_creation():
    """测试仓储工厂可以创建多个不同类型的仓储"""
    from app.repositories import RepositoryFactory
    from app.repositories.user_repository import UserRepository
    from app.repositories.workspace_repository import WorkspaceRepository
    from app.repositories.app_repository import AppRepository
    from sqlalchemy.orm import Session
    
    # Arrange
    mock_connector = MagicMock(spec=Neo4jConnector)
    mock_session = MagicMock(spec=Session)
    factory = RepositoryFactory(
        neo4j_connector=mock_connector,
        db_session=mock_session
    )
    
    # Act
    dialog_repo = factory.get_dialog_repository()
    statement_repo = factory.get_statement_repository()
    entity_repo = factory.get_entity_repository()
    user_repo = factory.get_user_repository()
    workspace_repo = factory.get_workspace_repository()
    app_repo = factory.get_app_repository()
    db_session = factory.get_db_session()
    
    # Assert
    assert isinstance(dialog_repo, DialogRepository)
    assert isinstance(statement_repo, StatementRepository)
    assert isinstance(entity_repo, EntityRepository)
    assert isinstance(user_repo, UserRepository)
    assert isinstance(workspace_repo, WorkspaceRepository)
    assert isinstance(app_repo, AppRepository)
    assert db_session is mock_session


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
