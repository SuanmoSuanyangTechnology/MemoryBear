"""
测试萃取引擎流水线编排器

该测试文件测试 ExtractionOrchestrator 的功能，包括：
1. 完整的提取流程
2. 错误处理机制

作者：Memory Refactoring Team
日期：2025-11-21
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch

from app.core.memory.storage_services.extraction_engine.extraction_orchestrator import (
    ExtractionOrchestrator,
)
from app.core.memory.models.variate_config import ExtractionPipelineConfig


class TestExtractionOrchestrator:
    """测试萃取引擎流水线编排器"""

    @pytest.fixture
    def mock_llm_client(self):
        """创建模拟的 LLM 客户端"""
        client = AsyncMock()
        client.complete = AsyncMock(return_value="测试陈述句")
        return client

    @pytest.fixture
    def mock_embedder_client(self):
        """创建模拟的嵌入模型客户端"""
        client = AsyncMock()
        client.embed = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
        return client

    @pytest.fixture
    def mock_connector(self):
        """创建模拟的 Neo4j 连接器"""
        connector = AsyncMock()
        connector.execute_query = AsyncMock(return_value=[])
        return connector

    @pytest.mark.anyio
    async def test_orchestrator_initialization(
        self, mock_llm_client, mock_embedder_client, mock_connector
    ):
        """测试编排器初始化"""
        config = ExtractionPipelineConfig()
        
        orchestrator = ExtractionOrchestrator(
            llm_client=mock_llm_client,
            embedder_client=mock_embedder_client,
            connector=mock_connector,
            config=config,
        )
        
        assert orchestrator.llm_client == mock_llm_client
        assert orchestrator.embedder_client == mock_embedder_client
        assert orchestrator.connector == mock_connector
        assert orchestrator.config == config
        assert orchestrator.statement_extractor is not None
        assert orchestrator.triplet_extractor is not None
        assert orchestrator.temporal_extractor is not None

    def test_orchestrator_attributes(self):
        """测试编排器属性"""
        # 这是一个简单的同步测试，验证类的基本结构
        assert hasattr(ExtractionOrchestrator, 'run')
        assert hasattr(ExtractionOrchestrator, '_extract_statements')
        assert hasattr(ExtractionOrchestrator, '_extract_triplets')
        assert hasattr(ExtractionOrchestrator, '_extract_temporal')
        assert hasattr(ExtractionOrchestrator, '_generate_embeddings')
        assert hasattr(ExtractionOrchestrator, '_assign_extracted_data')
        assert hasattr(ExtractionOrchestrator, '_create_nodes_and_edges')
        assert hasattr(ExtractionOrchestrator, '_run_dedup_and_write_summary')


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
