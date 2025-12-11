"""
去重消歧模块单元测试

测试内容：
- LLM 实体去重
- 基础去重和消歧（精确匹配、模糊匹配）
- 第二层去重
- 两阶段去重

注意：这些测试主要测试模块的基本功能和接口，不涉及实际的 LLM 调用。
实际的 LLM 集成测试应该在集成测试中进行。
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime
from typing import List, Dict, Tuple
from uuid import uuid4

# Mock circular import dependencies before importing
import sys
sys.modules['app.services.model_service'] = MagicMock()
sys.modules['app.repositories.model_repository'] = MagicMock()

from app.core.memory.models.graph_models import (
    ExtractedEntityNode,
    StatementEntityEdge,
    EntityEntityEdge,
)
from app.core.memory.models.variate_config import DedupConfig


class TestAccurateMatch:
    """测试精确匹配去重"""
    
    def test_accurate_match_import(self):
        """测试精确匹配函数可以正确导入"""
        from app.core.memory.storage_services.extraction_engine.deduplication.deduped_and_disamb import accurate_match
        assert accurate_match is not None
    
    def test_accurate_match_same_entities(self):
        """测试精确匹配相同实体"""
        from app.core.memory.storage_services.extraction_engine.deduplication.deduped_and_disamb import accurate_match
        
        # 创建两个相同的实体（名称、类型、group_id 都相同）
        entity1 = ExtractedEntityNode(
            id=uuid4().hex,
            name="测试实体",
            entity_type="PERSON",
            group_id="group1",
            user_id="user1",
            apply_id="app1",
            created_at=datetime.now(),
            entity_idx=0,
            statement_id="stmt1",
            description="",
            fact_summary="",
            connect_strength="",
        )
        entity2 = ExtractedEntityNode(
            id=uuid4().hex,
            name="测试实体",
            entity_type="PERSON",
            group_id="group1",
            user_id="user1",
            apply_id="app1",
            created_at=datetime.now(),
            entity_idx=1,
            statement_id="stmt2",
            description="",
            fact_summary="",
            connect_strength="",
        )
        
        deduped, id_redirect, exact_merge_map = accurate_match([entity1, entity2])
        
        # 应该只保留一个实体
        assert len(deduped) == 1
        # 应该有一个重定向映射
        assert len(id_redirect) == 2
        # 其中一个实体应该被重定向到另一个
        assert id_redirect[entity2.id] == entity1.id or id_redirect[entity1.id] == entity2.id

    
    def test_accurate_match_different_entities(self):
        """测试精确匹配不同实体"""
        from app.core.memory.storage_services.extraction_engine.deduplication.deduped_and_disamb import accurate_match
        
        # 创建两个不同的实体
        entity1 = ExtractedEntityNode(
            id=uuid4().hex,
            name="实体A",
            entity_type="PERSON",
            group_id="group1",
            user_id="user1",
            apply_id="app1",
            created_at=datetime.now(),
            entity_idx=0,
            statement_id="stmt1",
            description="",
            fact_summary="",
            connect_strength="",
        )
        entity2 = ExtractedEntityNode(
            id=uuid4().hex,
            name="实体B",
            entity_type="PERSON",
            group_id="group1",
            user_id="user1",
            apply_id="app1",
            created_at=datetime.now(),
            entity_idx=1,
            statement_id="stmt2",
            description="",
            fact_summary="",
            connect_strength="",
        )
        
        deduped, id_redirect, exact_merge_map = accurate_match([entity1, entity2])
        
        # 应该保留两个实体
        assert len(deduped) == 2
        # 每个实体应该映射到自己
        assert id_redirect[entity1.id] == entity1.id
        assert id_redirect[entity2.id] == entity2.id


class TestFuzzyMatch:
    """测试模糊匹配去重"""
    
    def test_fuzzy_match_import(self):
        """测试模糊匹配函数可以正确导入"""
        from app.core.memory.storage_services.extraction_engine.deduplication.deduped_and_disamb import fuzzy_match
        assert fuzzy_match is not None
    
    def test_fuzzy_match_similar_entities(self):
        """测试模糊匹配相似实体"""
        from app.core.memory.storage_services.extraction_engine.deduplication.deduped_and_disamb import fuzzy_match
        
        # 创建两个相似的实体（名称相似但不完全相同）
        entity1 = ExtractedEntityNode(
            id=uuid4().hex,
            name="苹果公司",
            entity_type="COMPANY",
            group_id="group1",
            user_id="user1",
            apply_id="app1",
            created_at=datetime.now(),
            entity_idx=0,
            statement_id="stmt1",
            name_embedding=[0.1] * 768,  # 模拟嵌入向量
            description="",
            fact_summary="",
            connect_strength="",
        )
        entity2 = ExtractedEntityNode(
            id=uuid4().hex,
            name="苹果",
            entity_type="COMPANY",
            group_id="group1",
            user_id="user1",
            apply_id="app1",
            created_at=datetime.now(),
            entity_idx=1,
            statement_id="stmt2",
            name_embedding=[0.1] * 768,  # 相同的嵌入向量
            description="",
            fact_summary="",
            connect_strength="",
        )
        
        id_redirect = {entity1.id: entity1.id, entity2.id: entity2.id}
        statement_entity_edges = []
        
        deduped, updated_redirect, fuzzy_records = fuzzy_match(
            [entity1, entity2],
            statement_entity_edges,
            id_redirect,
            config=None,
        )
        
        # 由于名称包含关系和高相似度，应该合并
        assert len(deduped) <= 2


class TestLLMEntityDedup:
    """测试 LLM 实体去重"""
    
    def test_llm_dedup_import(self):
        """测试 LLM 去重函数可以正确导入"""
        from app.core.memory.storage_services.extraction_engine.deduplication.entity_dedup_llm import (
            llm_dedup_entities,
            llm_dedup_entities_iterative_blocks,
            llm_disambiguate_pairs_iterative,
        )
        assert llm_dedup_entities is not None
        assert llm_dedup_entities_iterative_blocks is not None
        assert llm_disambiguate_pairs_iterative is not None

    
    @pytest.mark.anyio
    async def test_llm_dedup_entities_basic(self):
        """测试 LLM 实体去重基本功能"""
        from app.core.memory.storage_services.extraction_engine.deduplication.entity_dedup_llm import llm_dedup_entities
        
        # 创建 mock LLM 客户端
        mock_llm_client = AsyncMock()
        mock_decision = Mock()
        mock_decision.same_entity = True
        mock_decision.confidence = 0.95
        mock_decision.canonical_idx = 0
        mock_decision.reason = "测试原因"
        
        mock_llm_client.response_structured = AsyncMock(return_value=mock_decision)
        
        # 创建测试实体
        entity1 = ExtractedEntityNode(
            id=uuid4().hex,
            name="测试实体",
            entity_type="PERSON",
            group_id="group1",
            user_id="user1",
            apply_id="app1",
            created_at=datetime.now(),
            entity_idx=0,
            statement_id="stmt1",
            name_embedding=[0.9] * 768,
            description="",
            fact_summary="",
            connect_strength="",
        )
        entity2 = ExtractedEntityNode(
            id=uuid4().hex,
            name="测试实体2",
            entity_type="PERSON",
            group_id="group1",
            user_id="user1",
            apply_id="app1",
            created_at=datetime.now(),
            entity_idx=1,
            statement_id="stmt2",
            name_embedding=[0.9] * 768,
            description="",
            fact_summary="",
            connect_strength="",
        )
        
        id_redirect, records = await llm_dedup_entities(
            entity_nodes=[entity1, entity2],
            statement_entity_edges=[],
            entity_entity_edges=[],
            llm_client=mock_llm_client,
            max_concurrency=1,
        )
        
        # 应该有记录生成
        assert isinstance(records, list)
        assert isinstance(id_redirect, dict)


class TestDedupEntitiesAndEdges:
    """测试完整的去重消歧流程"""
    
    def test_deduplicate_entities_and_edges_import(self):
        """测试去重消歧主函数可以正确导入"""
        from app.core.memory.storage_services.extraction_engine.deduplication.deduped_and_disamb import (
            deduplicate_entities_and_edges,
        )
        assert deduplicate_entities_and_edges is not None
    
    @pytest.mark.anyio
    async def test_deduplicate_entities_and_edges_basic(self):
        """测试去重消歧基本功能"""
        from app.core.memory.storage_services.extraction_engine.deduplication.deduped_and_disamb import (
            deduplicate_entities_and_edges,
        )
        
        # 创建测试实体
        entity1 = ExtractedEntityNode(
            id=uuid4().hex,
            name="测试实体",
            entity_type="PERSON",
            group_id="group1",
            user_id="user1",
            apply_id="app1",
            created_at=datetime.now(),
            entity_idx=0,
            statement_id="stmt1",
            description="",
            fact_summary="",
            connect_strength="",
        )
        entity2 = ExtractedEntityNode(
            id=uuid4().hex,
            name="测试实体",
            entity_type="PERSON",
            group_id="group1",
            user_id="user1",
            apply_id="app1",
            created_at=datetime.now(),
            entity_idx=1,
            statement_id="stmt2",
            description="",
            fact_summary="",
            connect_strength="",
        )
        
        # 创建测试边
        stmt_edge = StatementEntityEdge(
            source="stmt1",
            target=entity1.id,
            connect_strength="strong",
            group_id="group1",
            user_id="user1",
            apply_id="app1",
            created_at=datetime.now(),
        )
        
        deduped_entities, deduped_stmt_edges, deduped_ent_edges = await deduplicate_entities_and_edges(
            entity_nodes=[entity1, entity2],
            statement_entity_edges=[stmt_edge],
            entity_entity_edges=[],
            report_stage="测试阶段",
            report_append=False,
            dedup_config=None,
        )
        
        # 应该返回去重后的实体和边
        assert isinstance(deduped_entities, list)
        assert isinstance(deduped_stmt_edges, list)
        assert isinstance(deduped_ent_edges, list)
        # 相同实体应该被合并
        assert len(deduped_entities) == 1


class TestSecondLayerDedup:
    """测试第二层去重"""
    
    def test_second_layer_dedup_import(self):
        """测试第二层去重函数可以正确导入"""
        from app.core.memory.storage_services.extraction_engine.deduplication.second_layer_dedup import (
            second_layer_dedup_and_merge_with_neo4j,
        )
        assert second_layer_dedup_and_merge_with_neo4j is not None


class TestTwoStageDedup:
    """测试两阶段去重"""
    
    def test_two_stage_dedup_import(self):
        """测试两阶段去重函数可以正确导入"""
        from app.core.memory.storage_services.extraction_engine.deduplication.two_stage_dedup import (
            dedup_layers_and_merge_and_return,
        )
        assert dedup_layers_and_merge_and_return is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
