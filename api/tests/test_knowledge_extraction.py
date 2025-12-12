"""
知识提取模块单元测试

测试内容：
- 分块提取
- 陈述句提取
- 三元组提取
- 时间信息提取
- 嵌入向量生成
- 记忆摘要生成

注意：这些测试主要测试模块的基本功能和接口，不涉及实际的 LLM 调用。
实际的 LLM 集成测试应该在集成测试中进行。
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime
from typing import List

# Mock circular import dependencies before importing
import sys
sys.modules['app.services.model_service'] = MagicMock()
sys.modules['app.repositories.model_repository'] = MagicMock()

from app.core.memory.models.message_models import (
    DialogData,
    ConversationContext,
    ConversationMessage,
    Chunk,
    Statement,
    TemporalValidityRange,
)
from app.core.memory.models.triplet_models import (
    TripletExtractionResponse,
    Triplet,
    Entity,
)
from app.core.memory.models.graph_models import MemorySummaryNode
from app.core.memory.models.config_models import ChunkerConfig
from app.core.memory.utils.ontology import StatementType, TemporalInfo, RelevenceInfo


class TestDialogueChunker:
    """测试对话分块器"""
    
    def test_chunker_initialization(self):
        """测试分块器初始化"""
        from app.core.memory.storage_services.extraction_engine.knowledge_extraction.chunk_extraction import DialogueChunker
        
        chunker = DialogueChunker(chunker_strategy="RecursiveChunker")
        assert chunker.chunker_strategy == "RecursiveChunker"
        assert chunker.chunker_config is not None
    
    def test_chunker_import(self):
        """测试分块器可以正确导入"""
        from app.core.memory.storage_services.extraction_engine.knowledge_extraction.chunk_extraction import DialogueChunker
        assert DialogueChunker is not None


class TestStatementExtractor:
    """测试陈述句提取器"""
    
    def test_extractor_initialization(self):
        """测试陈述句提取器初始化"""
        from app.core.memory.storage_services.extraction_engine.knowledge_extraction.statement_extraction import StatementExtractor
        
        mock_llm_client = Mock()
        extractor = StatementExtractor(llm_client=mock_llm_client)
        assert extractor.llm_client is not None
        assert extractor.config is not None
    
    def test_extractor_import(self):
        """测试陈述句提取器可以正确导入"""
        from app.core.memory.storage_services.extraction_engine.knowledge_extraction.statement_extraction import (
            StatementExtractor,
            ExtractedStatement,
            StatementExtractionResponse,
        )
        assert StatementExtractor is not None
        assert ExtractedStatement is not None
        assert StatementExtractionResponse is not None


class TestTripletExtractor:
    """测试三元组提取器"""
    
    def test_extractor_initialization(self):
        """测试三元组提取器初始化"""
        from app.core.memory.storage_services.extraction_engine.knowledge_extraction.triplet_extraction import TripletExtractor
        
        mock_llm_client = Mock()
        extractor = TripletExtractor(llm_client=mock_llm_client)
        assert extractor.llm_client is not None
    
    def test_extractor_import(self):
        """测试三元组提取器可以正确导入"""
        from app.core.memory.storage_services.extraction_engine.knowledge_extraction.triplet_extraction import TripletExtractor
        assert TripletExtractor is not None


class TestTemporalExtractor:
    """测试时间信息提取器"""
    
    def test_extractor_initialization(self):
        """测试时间信息提取器初始化"""
        from app.core.memory.storage_services.extraction_engine.knowledge_extraction.temporal_extraction import TemporalExtractor
        
        mock_llm_client = Mock()
        extractor = TemporalExtractor(llm_client=mock_llm_client)
        assert extractor.llm_client is not None
    
    def test_extractor_import(self):
        """测试时间信息提取器可以正确导入"""
        from app.core.memory.storage_services.extraction_engine.knowledge_extraction.temporal_extraction import (
            TemporalExtractor,
            RawTemporalRange,
        )
        assert TemporalExtractor is not None
        assert RawTemporalRange is not None


class TestEmbeddingGenerator:
    """测试嵌入向量生成器"""
    
    def test_generator_import(self):
        """测试嵌入向量生成器可以正确导入"""
        from app.core.memory.storage_services.extraction_engine.knowledge_extraction.embedding_generation import (
            EmbeddingGenerator,
            embedding_generation,
            generate_entity_embeddings_from_triplets,
            embedding_generation_all,
        )
        assert EmbeddingGenerator is not None
        assert embedding_generation is not None
        assert generate_entity_embeddings_from_triplets is not None
        assert embedding_generation_all is not None


class TestMemorySummaryGenerator:
    """测试记忆摘要生成器"""
    
    def test_generator_import(self):
        """测试记忆摘要生成器可以正确导入"""
        from app.core.memory.storage_services.extraction_engine.knowledge_extraction.memory_summary import MemorySummaryGenerator
        assert MemorySummaryGenerator is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
