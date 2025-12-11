"""Unit tests for Memory module data models.

This module tests the validation logic, serialization, and deserialization
of all Pydantic models in the memory module.
"""

import pytest
from datetime import datetime
from uuid import uuid4
from pydantic import ValidationError

# Import all models
from app.core.memory.models import (
    # Base response
    RobustLLMResponse,
    # Configuration
    LLMConfig,
    ChunkerConfig,
    PruningConfig,
    TemporalSearchParams,
    # Deduplication
    EntityDedupDecision,
    EntityDisambDecision,
    # Graph edges
    Edge,
    ChunkEdge,
    ChunkDialogEdge,
    StatementEntityEdge,
    EntityEntityEdge,
    # Graph nodes
    Node,
    DialogueNode,
    StatementNode,
    ChunkNode,
    ExtractedEntityNode,
    MemorySummaryNode,
    # Messages and dialogues
    ConversationMessage,
    TemporalValidityRange,
    Statement,
    ConversationContext,
    Chunk,
    DialogData,
    # Triplets and entities
    Entity,
    Triplet,
    TripletExtractionResponse,
    # Variable configuration
    StatementExtractionConfig,
    ForgettingEngineConfig,
    TripletExtractionConfig,
    TemporalExtractionConfig,
    DedupConfig,
    ExtractionPipelineConfig,
)
from app.core.memory.utils.ontology import StatementType, TemporalInfo, RelevenceInfo


class TestConfigModels:
    """Tests for configuration models."""
    
    def test_llm_config_valid(self):
        """测试 LLMConfig 的有效配置"""
        config = LLMConfig(llm_name="gpt-4", api_base="https://api.openai.com", max_retries=5)
        assert config.llm_name == "gpt-4"
        assert config.api_base == "https://api.openai.com"
        assert config.max_retries == 5
    
    def test_llm_config_defaults(self):
        """测试 LLMConfig 的默认值"""
        config = LLMConfig(llm_name="gpt-4")
        assert config.api_base is None
        assert config.max_retries == 3
    
    def test_llm_config_invalid_retries(self):
        """测试 LLMConfig 的无效重试次数"""
        with pytest.raises(ValidationError):
            LLMConfig(llm_name="gpt-4", max_retries=-1)
    
    def test_chunker_config_valid(self):
        """测试 ChunkerConfig 的有效配置"""
        config = ChunkerConfig(
            chunker_strategy="SemanticChunker",
            embedding_model="text-embedding-ada-002",
            chunk_size=1024,
            threshold=0.85
        )
        assert config.chunker_strategy == "SemanticChunker"
        assert config.chunk_size == 1024
        assert config.threshold == 0.85
    
    def test_pruning_config_valid(self):
        """测试 PruningConfig 的有效配置"""
        config = PruningConfig(
            pruning_switch=True,
            pruning_scene="education",
            pruning_threshold=0.6
        )
        assert config.pruning_switch is True
        assert config.pruning_threshold == 0.6
    
    def test_pruning_config_invalid_threshold(self):
        """测试 PruningConfig 的无效阈值"""
        with pytest.raises(ValidationError):
            PruningConfig(pruning_threshold=1.5)  # 超出范围
    
    def test_temporal_search_params(self):
        """测试 TemporalSearchParams"""
        params = TemporalSearchParams(
            group_id="group1",
            user_id="user1",
            start_date="2024-01-01",
            end_date="2024-12-31",
            limit=10
        )
        assert params.group_id == "group1"
        assert params.limit == 10


class TestDedupModels:
    """Tests for deduplication decision models."""
    
    def test_entity_dedup_decision_valid(self):
        """测试 EntityDedupDecision 的有效决策"""
        decision = EntityDedupDecision(
            same_entity=True,
            confidence=0.95,
            canonical_idx=0,
            reason="Same person with different spellings"
        )
        assert decision.same_entity is True
        assert decision.confidence == 0.95
        assert decision.canonical_idx == 0
    
    def test_entity_dedup_decision_invalid_confidence(self):
        """测试 EntityDedupDecision 的无效置信度"""
        with pytest.raises(ValidationError):
            EntityDedupDecision(
                same_entity=True,
                confidence=1.5,  # 超出范围
                canonical_idx=0,
                reason="Test"
            )
    
    def test_entity_disamb_decision_valid(self):
        """测试 EntityDisambDecision 的有效决策"""
        decision = EntityDisambDecision(
            should_merge=True,
            confidence=0.88,
            canonical_idx=1,
            block_pair=False,
            suggested_type="Person",
            reason="Same entity despite type difference"
        )
        assert decision.should_merge is True
        assert decision.suggested_type == "Person"


class TestGraphModels:
    """Tests for graph node and edge models."""
    
    def test_edge_creation(self):
        """测试 Edge 的创建"""
        now = datetime.now()
        edge = Edge(
            source="node1",
            target="node2",
            group_id="group1",
            user_id="user1",
            apply_id="app1",
            created_at=now
        )
        assert edge.source == "node1"
        assert edge.target == "node2"
        assert edge.created_at == now
        assert edge.id is not None  # 自动生成
    
    def test_entity_entity_edge(self):
        """测试 EntityEntityEdge"""
        edge = EntityEntityEdge(
            source="entity1",
            target="entity2",
            group_id="group1",
            user_id="user1",
            apply_id="app1",
            created_at=datetime.now(),
            relation_type="WORKS_FOR",
            statement="John works for Microsoft",
            source_statement_id="stmt1"
        )
        assert edge.relation_type == "WORKS_FOR"
        assert edge.statement == "John works for Microsoft"
    
    def test_dialogue_node(self):
        """测试 DialogueNode"""
        node = DialogueNode(
            id="dialog1",
            name="Conversation 1",
            group_id="group1",
            user_id="user1",
            apply_id="app1",
            created_at=datetime.now(),
            ref_id="ref1",
            content="User: Hello\nAI: Hi there!"
        )
        assert node.ref_id == "ref1"
        assert "Hello" in node.content
    
    def test_statement_node(self):
        """测试 StatementNode"""
        temporal_info = TemporalInfo.STATIC
        node = StatementNode(
            id="stmt1",
            name="Statement 1",
            group_id="group1",
            user_id="user1",
            apply_id="app1",
            created_at=datetime.now(),
            chunk_id="chunk1",
            stmt_type="FACT",
            temporal_info=temporal_info,
            statement="The sky is blue",
            connect_strength="Strong"
        )
        assert node.chunk_id == "chunk1"
        assert node.statement == "The sky is blue"
    
    def test_extracted_entity_node(self):
        """测试 ExtractedEntityNode"""
        node = ExtractedEntityNode(
            id="entity1",
            name="John Doe",
            group_id="group1",
            user_id="user1",
            apply_id="app1",
            created_at=datetime.now(),
            entity_idx=1,
            statement_id="stmt1",
            entity_type="Person",
            description="A software engineer",
            fact_summary="John is a software engineer",
            connect_strength="Strong"
        )
        assert node.entity_type == "Person"
        assert node.entity_idx == 1


class TestMessageModels:
    """Tests for message and dialogue models."""
    
    def test_conversation_message(self):
        """测试 ConversationMessage"""
        msg = ConversationMessage(role="用户", msg="你好")
        assert msg.role == "用户"
        assert msg.msg == "你好"
    
    def test_conversation_context(self):
        """测试 ConversationContext"""
        msgs = [
            ConversationMessage(role="用户", msg="你好"),
            ConversationMessage(role="AI", msg="你好！有什么可以帮助你的吗？")
        ]
        context = ConversationContext(msgs=msgs)
        content = context.content
        assert "用户: 你好" in content
        assert "AI: 你好！" in content
    
    def test_temporal_validity_range(self):
        """测试 TemporalValidityRange"""
        range_obj = TemporalValidityRange(
            valid_at="2024-01-01",
            invalid_at="2024-12-31"
        )
        assert range_obj.valid_at == "2024-01-01"
        assert range_obj.invalid_at == "2024-12-31"
    
    def test_statement_creation(self):
        """测试 Statement 的创建"""
        stmt = Statement(
            chunk_id="chunk1",
            statement="The weather is nice today",
            stmt_type=StatementType.FACT,
            temporal_info=TemporalInfo.DYNAMIC,
            relevence_info=RelevenceInfo.RELEVANT
        )
        assert stmt.statement == "The weather is nice today"
        assert stmt.id is not None  # 自动生成
    
    def test_chunk_from_messages(self):
        """测试 Chunk.from_messages 方法"""
        msgs = [
            ConversationMessage(role="用户", msg="你好"),
            ConversationMessage(role="AI", msg="你好！")
        ]
        chunk = Chunk.from_messages(msgs, metadata={"source": "test"})
        assert len(chunk.text) == 2
        assert "用户: 你好" in chunk.content
        assert chunk.metadata["source"] == "test"
    
    def test_dialog_data_creation(self):
        """测试 DialogData 的创建"""
        context = ConversationContext(msgs=[
            ConversationMessage(role="用户", msg="你好")
        ])
        dialog = DialogData(
            context=context,
            ref_id="ref1",
            group_id="group1",
            user_id="user1",
            apply_id="app1"
        )
        assert dialog.ref_id == "ref1"
        assert dialog.id is not None
        assert dialog.content == "用户: 你好"
    
    def test_dialog_data_get_all_statements(self):
        """测试 DialogData.get_all_statements 方法"""
        context = ConversationContext(msgs=[
            ConversationMessage(role="用户", msg="你好")
        ])
        dialog = DialogData(
            context=context,
            ref_id="ref1",
            group_id="group1",
            user_id="user1",
            apply_id="app1"
        )
        
        # 添加 chunks 和 statements
        stmt1 = Statement(
            chunk_id="chunk1",
            statement="Statement 1",
            stmt_type=StatementType.FACT,
            temporal_info=TemporalInfo.DYNAMIC
        )
        stmt2 = Statement(
            chunk_id="chunk1",
            statement="Statement 2",
            stmt_type=StatementType.FACT,
            temporal_info=TemporalInfo.STATIC
        )
        chunk = Chunk(content="Test", statements=[stmt1, stmt2])
        dialog.chunks = [chunk]
        
        all_stmts = dialog.get_all_statements()
        assert len(all_stmts) == 2
    
    def test_dialog_data_assign_group_id(self):
        """测试 DialogData.assign_group_id_to_statements 方法"""
        context = ConversationContext(msgs=[
            ConversationMessage(role="用户", msg="你好")
        ])
        dialog = DialogData(
            context=context,
            ref_id="ref1",
            group_id="test_group",
            user_id="user1",
            apply_id="app1"
        )
        
        stmt = Statement(
            chunk_id="chunk1",
            statement="Test",
            stmt_type=StatementType.FACT,
            temporal_info=TemporalInfo.ATEMPORAL
        )
        chunk = Chunk(content="Test", statements=[stmt])
        dialog.chunks = [chunk]
        
        dialog.assign_group_id_to_statements()
        assert dialog.chunks[0].statements[0].group_id == "test_group"


class TestTripletModels:
    """Tests for triplet and entity models."""
    
    def test_entity_creation(self):
        """测试 Entity 的创建"""
        entity = Entity(
            entity_idx=1,
            name="Microsoft",
            type="Organization",
            description="A technology company"
        )
        assert entity.name == "Microsoft"
        assert entity.type == "Organization"
        assert entity.id is not None
    
    def test_triplet_creation(self):
        """测试 Triplet 的创建"""
        triplet = Triplet(
            subject_name="John",
            subject_id=1,
            predicate="WORKS_FOR",
            object_name="Microsoft",
            object_id=2
        )
        assert triplet.subject_name == "John"
        assert triplet.predicate == "WORKS_FOR"
        assert triplet.object_name == "Microsoft"
    
    def test_triplet_extraction_response(self):
        """测试 TripletExtractionResponse"""
        entity1 = Entity(entity_idx=1, name="John", type="Person", description="A person")
        entity2 = Entity(entity_idx=2, name="Microsoft", type="Organization", description="A company")
        triplet = Triplet(
            subject_name="John",
            subject_id=1,
            predicate="WORKS_FOR",
            object_name="Microsoft",
            object_id=2
        )
        
        response = TripletExtractionResponse(
            entities=[entity1, entity2],
            triplets=[triplet]
        )
        assert len(response.entities) == 2
        assert len(response.triplets) == 1


class TestVariateConfigModels:
    """Tests for variable configuration models."""
    
    def test_statement_extraction_config(self):
        """测试 StatementExtractionConfig"""
        config = StatementExtractionConfig(
            statement_granularity=2,
            temperature=0.2,
            include_dialogue_context=True,
            max_dialogue_context_chars=1500
        )
        assert config.statement_granularity == 2
        assert config.temperature == 0.2
    
    def test_statement_extraction_config_invalid_granularity(self):
        """测试 StatementExtractionConfig 的无效粒度"""
        with pytest.raises(ValidationError):
            StatementExtractionConfig(statement_granularity=5)  # 超出范围
    
    def test_forgetting_engine_config(self):
        """测试 ForgettingEngineConfig"""
        config = ForgettingEngineConfig(
            offset=0.2,
            lambda_time=0.15,
            lambda_mem=1.5
        )
        assert config.offset == 0.2
        assert config.lambda_time == 0.15
    
    def test_triplet_extraction_config(self):
        """测试 TripletExtractionConfig"""
        config = TripletExtractionConfig(
            temperature=0.15,
            enable_entity_normalization=True,
            confidence_threshold=0.8
        )
        assert config.enable_entity_normalization is True
        assert config.confidence_threshold == 0.8
    
    def test_dedup_config(self):
        """测试 DedupConfig"""
        config = DedupConfig(
            enable_llm_dedup_blockwise=True,
            fuzzy_overall_threshold=0.85,
            llm_block_size=100
        )
        assert config.enable_llm_dedup_blockwise is True
        assert config.fuzzy_overall_threshold == 0.85
    
    def test_extraction_pipeline_config(self):
        """测试 ExtractionPipelineConfig"""
        config = ExtractionPipelineConfig()
        assert config.statement_extraction is not None
        assert config.triplet_extraction is not None
        assert config.temporal_extraction is not None
        assert config.deduplication is not None
        assert config.forgetting_engine is not None


class TestModelSerialization:
    """Tests for model serialization and deserialization."""
    
    def test_llm_config_serialization(self):
        """测试 LLMConfig 的序列化"""
        config = LLMConfig(llm_name="gpt-4", max_retries=5)
        data = config.model_dump()
        assert data["llm_name"] == "gpt-4"
        assert data["max_retries"] == 5
        
        # 反序列化
        config2 = LLMConfig(**data)
        assert config2.llm_name == config.llm_name
    
    def test_dialog_data_serialization(self):
        """测试 DialogData 的序列化"""
        context = ConversationContext(msgs=[
            ConversationMessage(role="用户", msg="你好")
        ])
        dialog = DialogData(
            context=context,
            ref_id="ref1",
            group_id="group1",
            user_id="user1",
            apply_id="app1"
        )
        
        data = dialog.model_dump()
        assert data["ref_id"] == "ref1"
        assert data["group_id"] == "group1"
        
        # 反序列化
        dialog2 = DialogData(**data)
        assert dialog2.ref_id == dialog.ref_id
    
    def test_entity_serialization(self):
        """测试 Entity 的序列化"""
        entity = Entity(
            entity_idx=1,
            name="Test",
            type="Person",
            description="A test entity"
        )
        
        data = entity.model_dump()
        assert data["name"] == "Test"
        
        # 反序列化
        entity2 = Entity(**data)
        assert entity2.name == entity.name


class TestRobustLLMResponse:
    """Tests for RobustLLMResponse base class."""
    
    def test_handle_list_input(self):
        """测试处理列表输入"""
        class TestResponse(RobustLLMResponse):
            field1: str
            field2: int
        
        # 测试列表包装的输入
        data = [{"field1": "test", "field2": 42}]
        response = TestResponse(**data[0])  # Pydantic 会自动调用 validator
        assert response.field1 == "test"
        assert response.field2 == 42
    
    def test_ignore_extra_fields(self):
        """测试忽略额外字段"""
        class TestResponse(RobustLLMResponse):
            field1: str
        
        # 包含额外字段
        data = {"field1": "test", "extra_field": "ignored"}
        response = TestResponse(**data)
        assert response.field1 == "test"
        assert not hasattr(response, "extra_field")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
