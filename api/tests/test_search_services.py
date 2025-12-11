# -*- coding: utf-8 -*-
"""搜索服务单元测试

本模块测试搜索服务的关键词搜索、语义搜索和混合搜索功能。
使用mock隔离数据库和嵌入器依赖，确保测试的独立性和可重复性。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from uuid import uuid4

from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.core.memory.storage_services.search.search_strategy import SearchResult
from app.core.memory.storage_services.search.keyword_search import KeywordSearchStrategy
from app.core.memory.storage_services.search.semantic_search import SemanticSearchStrategy
from app.core.memory.storage_services.search.hybrid_search import HybridSearchStrategy
from app.core.memory.src.llm_tools.openai_embedder import OpenAIEmbedderClient


# ==================== Fixtures ====================

@pytest.fixture
def mock_connector():
    """创建mock的Neo4j连接器"""
    connector = AsyncMock(spec=Neo4jConnector)
    connector.close = AsyncMock()
    return connector


@pytest.fixture
def mock_embedder():
    """创建mock的嵌入器客户端"""
    embedder = AsyncMock(spec=OpenAIEmbedderClient)
    embedder.response = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
    return embedder


@pytest.fixture
def sample_statement_results():
    """创建示例陈述句搜索结果"""
    return [
        {
            "id": uuid4().hex,
            "statement": "这是第一个测试陈述句",
            "score": 0.95,
            "group_id": "group_123",
            "created_at": datetime.now().isoformat()
        },
        {
            "id": uuid4().hex,
            "statement": "这是第二个测试陈述句",
            "score": 0.85,
            "group_id": "group_123",
            "created_at": datetime.now().isoformat()
        }
    ]


@pytest.fixture
def sample_chunk_results():
    """创建示例分块搜索结果"""
    return [
        {
            "id": uuid4().hex,
            "content": "这是第一个测试分块",
            "score": 0.90,
            "group_id": "group_123",
            "created_at": datetime.now().isoformat()
        }
    ]


@pytest.fixture
def sample_entity_results():
    """创建示例实体搜索结果"""
    return [
        {
            "id": uuid4().hex,
            "name": "测试实体",
            "entity_type": "Person",
            "score": 0.88,
            "group_id": "group_123",
            "created_at": datetime.now().isoformat()
        }
    ]


# ==================== KeywordSearchStrategy Tests ====================

@pytest.mark.anyio
async def test_keyword_search_basic(
    mock_connector,
    sample_statement_results,
    sample_chunk_results,
    sample_entity_results
):
    """测试基本的关键词搜索功能"""
    # Arrange
    with patch("app.core.memory.storage_services.search.keyword_search.search_graph") as mock_search:
        mock_search.return_value = {
            "statements": sample_statement_results,
            "chunks": sample_chunk_results,
            "entities": sample_entity_results,
            "summaries": []
        }
        
        strategy = KeywordSearchStrategy(connector=mock_connector)
        
        # Act
        result = await strategy.search(
            query_text="测试查询",
            group_id="group_123",
            limit=10
        )
        
        # Assert
        assert isinstance(result, SearchResult)
        assert len(result.statements) == 2
        assert len(result.chunks) == 1
        assert len(result.entities) == 1
        assert result.total_results() == 4
        assert result.metadata["search_type"] == "keyword"
        assert result.metadata["query"] == "测试查询"


@pytest.mark.anyio
async def test_keyword_search_with_include_filter(mock_connector, sample_statement_results):
    """测试带类别过滤的关键词搜索"""
    # Arrange
    with patch("app.core.memory.storage_services.search.keyword_search.search_graph") as mock_search:
        mock_search.return_value = {
            "statements": sample_statement_results,
            "chunks": [],
            "entities": [],
            "summaries": []
        }
        
        strategy = KeywordSearchStrategy(connector=mock_connector)
        
        # Act
        result = await strategy.search(
            query_text="测试查询",
            group_id="group_123",
            limit=10,
            include=["statements"]
        )
        
        # Assert
        assert len(result.statements) == 2
        assert len(result.chunks) == 0
        assert len(result.entities) == 0


@pytest.mark.anyio
async def test_keyword_search_error_handling(mock_connector):
    """测试关键词搜索的错误处理"""
    # Arrange
    with patch("app.core.memory.storage_services.search.keyword_search.search_graph") as mock_search:
        mock_search.side_effect = Exception("数据库连接失败")
        
        strategy = KeywordSearchStrategy(connector=mock_connector)
        
        # Act
        result = await strategy.search(
            query_text="测试查询",
            group_id="group_123",
            limit=10
        )
        
        # Assert
        assert result.total_results() == 0
        assert "error" in result.metadata
        assert "数据库连接失败" in result.metadata["error"]


# ==================== SemanticSearchStrategy Tests ====================

@pytest.mark.anyio
async def test_semantic_search_basic(
    mock_connector,
    mock_embedder,
    sample_statement_results,
    sample_chunk_results
):
    """测试基本的语义搜索功能"""
    # Arrange
    with patch("app.core.memory.storage_services.search.semantic_search.search_graph_by_embedding") as mock_search:
        mock_search.return_value = {
            "statements": sample_statement_results,
            "chunks": sample_chunk_results,
            "entities": [],
            "summaries": []
        }
        
        strategy = SemanticSearchStrategy(
            connector=mock_connector,
            embedder_client=mock_embedder
        )
        
        # Act
        result = await strategy.search(
            query_text="测试查询",
            group_id="group_123",
            limit=10
        )
        
        # Assert
        assert isinstance(result, SearchResult)
        assert len(result.statements) == 2
        assert len(result.chunks) == 1
        assert result.total_results() == 3
        assert result.metadata["search_type"] == "semantic"
        
        # 验证search_graph_by_embedding被调用，并且传入了embedder_client
        mock_search.assert_called_once()
        call_kwargs = mock_search.call_args.kwargs
        assert call_kwargs["embedder_client"] == mock_embedder


@pytest.mark.anyio
async def test_semantic_search_with_include_filter(
    mock_connector,
    mock_embedder,
    sample_statement_results
):
    """测试带类别过滤的语义搜索"""
    # Arrange
    with patch("app.core.memory.storage_services.search.semantic_search.search_graph_by_embedding") as mock_search:
        mock_search.return_value = {
            "statements": sample_statement_results,
            "chunks": [],
            "entities": [],
            "summaries": []
        }
        
        strategy = SemanticSearchStrategy(
            connector=mock_connector,
            embedder_client=mock_embedder
        )
        
        # Act
        result = await strategy.search(
            query_text="测试查询",
            group_id="group_123",
            limit=10,
            include=["statements", "chunks"]
        )
        
        # Assert
        assert len(result.statements) == 2
        assert len(result.entities) == 0


@pytest.mark.anyio
async def test_semantic_search_error_handling(mock_connector, mock_embedder):
    """测试语义搜索的错误处理"""
    # Arrange
    with patch("app.core.memory.storage_services.search.semantic_search.search_graph_by_embedding") as mock_search:
        mock_search.side_effect = Exception("嵌入生成失败")
        
        strategy = SemanticSearchStrategy(
            connector=mock_connector,
            embedder_client=mock_embedder
        )
        
        # Act
        result = await strategy.search(
            query_text="测试查询",
            group_id="group_123",
            limit=10
        )
        
        # Assert
        assert result.total_results() == 0
        assert "error" in result.metadata


# ==================== HybridSearchStrategy Tests ====================

@pytest.mark.anyio
async def test_hybrid_search_basic(
    mock_connector,
    mock_embedder,
    sample_statement_results,
    sample_chunk_results
):
    """测试基本的混合搜索功能"""
    # Arrange
    with patch("app.core.memory.storage_services.search.keyword_search.search_graph") as mock_keyword_search, \
         patch("app.core.memory.storage_services.search.semantic_search.search_graph_by_embedding") as mock_semantic_search:
        
        mock_keyword_search.return_value = {
            "statements": sample_statement_results[:1],
            "chunks": sample_chunk_results,
            "entities": [],
            "summaries": []
        }
        
        mock_semantic_search.return_value = {
            "statements": sample_statement_results[1:],
            "chunks": [],
            "entities": [],
            "summaries": []
        }
        
        strategy = HybridSearchStrategy(
            connector=mock_connector,
            embedder_client=mock_embedder,
            alpha=0.6
        )
        
        # Act
        result = await strategy.search(
            query_text="测试查询",
            group_id="group_123",
            limit=10
        )
        
        # Assert
        assert isinstance(result, SearchResult)
        assert result.total_results() > 0
        assert result.metadata["search_type"] == "hybrid"
        assert result.metadata["alpha"] == 0.6
        
        # 验证两种搜索都被调用
        mock_keyword_search.assert_called_once()
        mock_semantic_search.assert_called_once()


@pytest.mark.anyio
async def test_hybrid_search_reranking(
    mock_connector,
    mock_embedder,
    sample_statement_results
):
    """测试混合搜索的重排序功能"""
    # Arrange
    # 创建有重叠ID的结果
    overlapping_id = uuid4().hex
    keyword_results = [
        {
            "id": overlapping_id,
            "statement": "重叠的陈述句",
            "score": 0.8,
            "group_id": "group_123",
            "created_at": datetime.now().isoformat()
        }
    ]
    
    semantic_results = [
        {
            "id": overlapping_id,
            "statement": "重叠的陈述句",
            "score": 0.9,
            "group_id": "group_123",
            "created_at": datetime.now().isoformat()
        }
    ]
    
    with patch("app.core.memory.storage_services.search.keyword_search.search_graph") as mock_keyword_search, \
         patch("app.core.memory.storage_services.search.semantic_search.search_graph_by_embedding") as mock_semantic_search:
        
        mock_keyword_search.return_value = {
            "statements": keyword_results,
            "chunks": [],
            "entities": [],
            "summaries": []
        }
        
        mock_semantic_search.return_value = {
            "statements": semantic_results,
            "chunks": [],
            "entities": [],
            "summaries": []
        }
        
        strategy = HybridSearchStrategy(
            connector=mock_connector,
            embedder_client=mock_embedder,
            alpha=0.6
        )
        
        # Act
        result = await strategy.search(
            query_text="测试查询",
            group_id="group_123",
            limit=10
        )
        
        # Assert
        assert len(result.statements) == 1  # 重叠的结果应该被合并
        assert "combined_score" in result.statements[0]
        assert "bm25_score" in result.statements[0]
        assert "embedding_score" in result.statements[0]


@pytest.mark.anyio
async def test_hybrid_search_with_forgetting_curve(
    mock_connector,
    mock_embedder,
    sample_statement_results
):
    """测试带遗忘曲线的混合搜索"""
    # Arrange
    with patch("app.core.memory.storage_services.search.keyword_search.search_graph") as mock_keyword_search, \
         patch("app.core.memory.storage_services.search.semantic_search.search_graph_by_embedding") as mock_semantic_search:
        
        mock_keyword_search.return_value = {
            "statements": sample_statement_results,
            "chunks": [],
            "entities": [],
            "summaries": []
        }
        
        mock_semantic_search.return_value = {
            "statements": [],
            "chunks": [],
            "entities": [],
            "summaries": []
        }
        
        strategy = HybridSearchStrategy(
            connector=mock_connector,
            embedder_client=mock_embedder,
            alpha=0.6,
            use_forgetting_curve=True
        )
        
        # Act
        result = await strategy.search(
            query_text="测试查询",
            group_id="group_123",
            limit=10
        )
        
        # Assert
        assert result.total_results() > 0
        assert result.metadata["use_forgetting_curve"] is True
        
        # 验证遗忘权重被应用
        if result.statements:
            assert "forgetting_weight" in result.statements[0]
            assert "time_elapsed_days" in result.statements[0]


@pytest.mark.anyio
async def test_hybrid_search_score_normalization(mock_connector, mock_embedder):
    """测试混合搜索的分数归一化"""
    # Arrange
    # 创建不同分数范围的结果
    keyword_results = [
        {"id": "1", "statement": "结果1", "score": 100, "group_id": "g1", "created_at": datetime.now().isoformat()},
        {"id": "2", "statement": "结果2", "score": 50, "group_id": "g1", "created_at": datetime.now().isoformat()},
    ]
    
    semantic_results = [
        {"id": "3", "statement": "结果3", "score": 0.9, "group_id": "g1", "created_at": datetime.now().isoformat()},
        {"id": "4", "statement": "结果4", "score": 0.5, "group_id": "g1", "created_at": datetime.now().isoformat()},
    ]
    
    with patch("app.core.memory.storage_services.search.keyword_search.search_graph") as mock_keyword_search, \
         patch("app.core.memory.storage_services.search.semantic_search.search_graph_by_embedding") as mock_semantic_search:
        
        mock_keyword_search.return_value = {
            "statements": keyword_results,
            "chunks": [],
            "entities": [],
            "summaries": []
        }
        
        mock_semantic_search.return_value = {
            "statements": semantic_results,
            "chunks": [],
            "entities": [],
            "summaries": []
        }
        
        strategy = HybridSearchStrategy(
            connector=mock_connector,
            embedder_client=mock_embedder,
            alpha=0.5
        )
        
        # Act
        result = await strategy.search(
            query_text="测试查询",
            group_id="g1",
            limit=10
        )
        
        # Assert
        assert result.total_results() == 4
        
        # 验证所有结果都有归一化的分数
        for stmt in result.statements:
            assert "combined_score" in stmt
            assert 0 <= stmt["combined_score"] <= 1


# ==================== SearchResult Tests ====================

def test_search_result_total_results():
    """测试SearchResult的total_results方法"""
    # Arrange
    result = SearchResult(
        statements=[{"id": "1"}, {"id": "2"}],
        chunks=[{"id": "3"}],
        entities=[{"id": "4"}, {"id": "5"}, {"id": "6"}],
        summaries=[]
    )
    
    # Act
    total = result.total_results()
    
    # Assert
    assert total == 6


def test_search_result_to_dict():
    """测试SearchResult的to_dict方法"""
    # Arrange
    result = SearchResult(
        statements=[{"id": "1"}],
        chunks=[],
        entities=[],
        summaries=[],
        metadata={"query": "test"}
    )
    
    # Act
    result_dict = result.to_dict()
    
    # Assert
    assert isinstance(result_dict, dict)
    assert "statements" in result_dict
    assert "chunks" in result_dict
    assert "entities" in result_dict
    assert "summaries" in result_dict
    assert "metadata" in result_dict
    assert result_dict["metadata"]["query"] == "test"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
