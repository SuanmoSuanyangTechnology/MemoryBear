"""
自我反思引擎单元测试

测试内容：
1. 基于时间的反思
2. 基于事实的反思
3. 综合反思
4. 反思结果应用
"""

import pytest
import asyncio
import uuid
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from typing import List, Dict, Any

from app.core.memory.storage_services.reflection_engine.self_reflexion import (
    ReflectionEngine,
    ReflectionConfig,
    ReflectionResult,
    ReflectionRange,
    ReflectionBaseline,
    create_reflection_engine,
)


class TestReflectionConfig:
    """测试反思配置"""
    
    def test_default_config(self):
        """测试默认配置"""
        config = ReflectionConfig()
        assert config.enabled is False
        assert config.iteration_period == "三小时"
        assert config.reflexion_range == ReflectionRange.RETRIEVAL
        assert config.baseline == ReflectionBaseline.TIME
        assert config.concurrency == 5
    
    def test_custom_config(self):
        """测试自定义配置"""
        config = ReflectionConfig(
            enabled=True,
            iteration_period="一小时",
            reflexion_range=ReflectionRange.DATABASE,
            baseline=ReflectionBaseline.FACT,
            concurrency=10
        )
        assert config.enabled is True
        assert config.iteration_period == "一小时"
        assert config.reflexion_range == ReflectionRange.DATABASE
        assert config.baseline == ReflectionBaseline.FACT
        assert config.concurrency == 10


class TestReflectionResult:
    """测试反思结果"""
    
    def test_result_creation(self):
        """测试结果创建"""
        result = ReflectionResult(
            success=True,
            message="反思完成",
            conflicts_found=5,
            conflicts_resolved=4,
            memories_updated=4,
            execution_time=1.5
        )
        assert result.success is True
        assert result.message == "反思完成"
        assert result.conflicts_found == 5
        assert result.conflicts_resolved == 4
        assert result.memories_updated == 4
        assert result.execution_time == 1.5


class TestReflectionEngine:
    """测试反思引擎"""
    
    @pytest.fixture
    def mock_neo4j_connector(self):
        """模拟 Neo4j 连接器"""
        connector = AsyncMock()
        connector.execute_query = AsyncMock(return_value=[])
        return connector
    
    @pytest.fixture
    def mock_llm_client(self):
        """模拟 LLM 客户端"""
        client = AsyncMock()
        client.response_structured = AsyncMock()
        return client
    
    @pytest.fixture
    def reflection_config(self):
        """创建测试配置"""
        return ReflectionConfig(
            enabled=True,
            iteration_period="三小时",
            reflexion_range=ReflectionRange.RETRIEVAL,
            baseline=ReflectionBaseline.TIME,
            concurrency=2
        )
    
    @pytest.fixture
    def reflection_engine(self, reflection_config, mock_neo4j_connector, mock_llm_client):
        """创建反思引擎实例"""
        # 创建模拟的依赖函数
        mock_get_data = AsyncMock(return_value=[])
        mock_render_evaluate = AsyncMock(return_value="评估提示词")
        mock_render_reflexion = AsyncMock(return_value="反思提示词")
        
        return ReflectionEngine(
            config=reflection_config,
            neo4j_connector=mock_neo4j_connector,
            llm_client=mock_llm_client,
            get_data_func=mock_get_data,
            render_evaluate_prompt_func=mock_render_evaluate,
            render_reflexion_prompt_func=mock_render_reflexion,
            conflict_schema=Mock(),
            reflexion_schema=Mock(),
            update_query="UPDATE query"
        )
    
    @pytest.mark.asyncio
    async def test_engine_initialization(self, reflection_engine, reflection_config):
        """测试引擎初始化"""
        assert reflection_engine.config == reflection_config
        assert reflection_engine.neo4j_connector is not None
        assert reflection_engine.llm_client is not None
        assert reflection_engine.get_data_func is not None
    
    @pytest.mark.asyncio
    async def test_disabled_reflection(self, mock_neo4j_connector, mock_llm_client):
        """测试禁用反思"""
        config = ReflectionConfig(enabled=False)
        engine = ReflectionEngine(
            config,
            neo4j_connector=mock_neo4j_connector,
            llm_client=mock_llm_client,
            get_data_func=AsyncMock(),
            render_evaluate_prompt_func=AsyncMock(),
            render_reflexion_prompt_func=AsyncMock(),
            conflict_schema=Mock(),
            reflexion_schema=Mock(),
            update_query="UPDATE query"
        )
        
        host_id = uuid.uuid4()
        result = await engine.execute_reflection(host_id)
        
        assert result.success is False
        assert "未启用" in result.message
    
    @pytest.mark.asyncio
    async def test_no_reflection_data(self, reflection_engine):
        """测试无反思数据的情况"""
        host_id = uuid.uuid4()
        
        # 设置 get_data_func 返回空列表
        reflection_engine.get_data_func = AsyncMock(return_value=[])
        
        result = await reflection_engine.execute_reflection(host_id)
        
        assert result.success is True
        assert "无反思数据" in result.message
        assert result.conflicts_found == 0
    
    @pytest.mark.asyncio
    async def test_no_conflicts_detected(self, reflection_engine):
        """测试无冲突检测的情况"""
        host_id = uuid.uuid4()
        mock_data = [
            {"id": "1", "content": "记忆1"},
            {"id": "2", "content": "记忆2"}
        ]
        
        # 设置 get_data_func 返回数据
        reflection_engine.get_data_func = AsyncMock(return_value=mock_data)
        # 模拟冲突检测返回空列表
        reflection_engine._detect_conflicts = AsyncMock(return_value=[])
        
        result = await reflection_engine.execute_reflection(host_id)
        
        assert result.success is True
        assert "无冲突" in result.message
        assert result.conflicts_found == 0
    
    @pytest.mark.asyncio
    async def test_conflict_detection(self, reflection_engine, mock_llm_client):
        """测试冲突检测"""
        mock_data = [
            {"id": "1", "content": "今天是晴天"},
            {"id": "2", "content": "今天是雨天"}
        ]
        
        # 模拟 LLM 返回冲突
        mock_conflict_response = Mock()
        mock_conflict_response.model_dump = Mock(return_value={
            "conflicts": [
                {"memory_ids": ["1", "2"], "reason": "天气矛盾"}
            ]
        })
        
        reflection_engine.llm_client = mock_llm_client
        mock_llm_client.response_structured.return_value = mock_conflict_response
        reflection_engine.render_evaluate_prompt_func = AsyncMock(return_value="冲突检测提示词")
        
        conflicts = await reflection_engine._detect_conflicts(mock_data)
        
        assert len(conflicts) > 0
        assert mock_llm_client.response_structured.called
    
    @pytest.mark.asyncio
    async def test_conflict_resolution(self, reflection_engine, mock_llm_client):
        """测试冲突解决"""
        mock_conflicts = [
            {"memory_ids": ["1", "2"], "reason": "天气矛盾"}
        ]
        
        # 模拟 LLM 返回解决方案（直接返回字典）
        expected_solution = {
            "resolved": {
                "resolved_memory": {
                    "group_id": "test_group",
                    "id": "1",
                    "invalid_at": "2024-12-31T23:59:59"
                }
            }
        }
        
        # 创建一个 Mock 对象，模拟 BaseModel 的行为
        # 使用 MagicMock 并设置 isinstance 检查
        from pydantic import BaseModel as PydanticBaseModel
        mock_solution = MagicMock(spec=PydanticBaseModel)
        mock_solution.model_dump = Mock(return_value=expected_solution)
        
        reflection_engine.llm_client = mock_llm_client
        mock_llm_client.response_structured.return_value = mock_solution
        reflection_engine.render_reflexion_prompt_func = AsyncMock(return_value="反思提示词")
        
        solutions = await reflection_engine._resolve_conflicts(mock_conflicts)
        
        assert len(solutions) > 0
        # 检查返回的字典中包含 resolved 键
        assert isinstance(solutions[0], dict)
        assert "resolved" in solutions[0]
        assert solutions[0]["resolved"]["resolved_memory"]["group_id"] == "test_group"
    
    @pytest.mark.asyncio
    async def test_apply_reflection_results(self, reflection_engine, mock_neo4j_connector):
        """测试应用反思结果"""
        mock_solutions = [
            {
                "resolved": {
                    "resolved_memory": {
                        "group_id": "test_group",
                        "id": "1",
                        "invalid_at": "2024-12-31T23:59:59"
                    }
                }
            }
        ]
        
        with patch.object(
            reflection_engine,
            'neo4j_connector',
            mock_neo4j_connector
        ):
            updated_count = await reflection_engine._apply_reflection_results(
                mock_solutions
            )
            
            assert updated_count == 1
            assert mock_neo4j_connector.execute_query.called
    
    @pytest.mark.asyncio
    async def test_complete_reflection_flow(self, reflection_engine):
        """测试完整的反思流程"""
        host_id = uuid.uuid4()
        
        # 模拟数据
        mock_data = [
            {"id": "1", "content": "记忆1"},
            {"id": "2", "content": "记忆2"}
        ]
        
        mock_conflicts = [
            {"memory_ids": ["1", "2"], "reason": "冲突"}
        ]
        
        mock_solutions = [
            {
                "resolved": {
                    "resolved_memory": {
                        "group_id": "test_group",
                        "id": "1",
                        "invalid_at": "2024-12-31T23:59:59"
                    }
                }
            }
        ]
        
        reflection_engine.get_data_func = AsyncMock(return_value=mock_data)
        reflection_engine._detect_conflicts = AsyncMock(return_value=mock_conflicts)
        reflection_engine._resolve_conflicts = AsyncMock(return_value=mock_solutions)
        reflection_engine._apply_reflection_results = AsyncMock(return_value=1)
        reflection_engine._log_data = AsyncMock()
        
        result = await reflection_engine.execute_reflection(host_id)
        
        assert result.success is True
        assert result.conflicts_found == 1
        assert result.conflicts_resolved == 1
        assert result.memories_updated == 1
        assert result.execution_time >= 0  # 在测试环境中可能非常快
    
    @pytest.mark.asyncio
    async def test_time_based_reflection(self, reflection_engine):
        """测试基于时间的反思"""
        host_id = uuid.uuid4()
        
        with patch.object(
            reflection_engine,
            'execute_reflection',
            new_callable=AsyncMock
        ) as mock_execute:
            mock_execute.return_value = ReflectionResult(
                success=True,
                message="时间反思完成"
            )
            
            result = await reflection_engine.time_based_reflection(host_id)
            
            assert result.success is True
            assert mock_execute.called
    
    @pytest.mark.asyncio
    async def test_fact_based_reflection(self, reflection_engine):
        """测试基于事实的反思"""
        host_id = uuid.uuid4()
        
        with patch.object(
            reflection_engine,
            'execute_reflection',
            new_callable=AsyncMock
        ) as mock_execute:
            mock_execute.return_value = ReflectionResult(
                success=True,
                message="事实反思完成"
            )
            
            result = await reflection_engine.fact_based_reflection(host_id)
            
            assert result.success is True
            assert mock_execute.called
    
    @pytest.mark.asyncio
    async def test_comprehensive_reflection_time_baseline(self, reflection_engine):
        """测试综合反思 - 时间基线"""
        host_id = uuid.uuid4()
        reflection_engine.config.baseline = ReflectionBaseline.TIME
        
        with patch.object(
            reflection_engine,
            'time_based_reflection',
            new_callable=AsyncMock
        ) as mock_time:
            mock_time.return_value = ReflectionResult(
                success=True,
                message="时间反思完成"
            )
            
            result = await reflection_engine.comprehensive_reflection(host_id)
            
            assert result.success is True
            assert mock_time.called
    
    @pytest.mark.asyncio
    async def test_comprehensive_reflection_fact_baseline(self, reflection_engine):
        """测试综合反思 - 事实基线"""
        host_id = uuid.uuid4()
        reflection_engine.config.baseline = ReflectionBaseline.FACT
        
        with patch.object(
            reflection_engine,
            'fact_based_reflection',
            new_callable=AsyncMock
        ) as mock_fact:
            mock_fact.return_value = ReflectionResult(
                success=True,
                message="事实反思完成"
            )
            
            result = await reflection_engine.comprehensive_reflection(host_id)
            
            assert result.success is True
            assert mock_fact.called
    
    @pytest.mark.asyncio
    async def test_comprehensive_reflection_hybrid_baseline(self, reflection_engine):
        """测试综合反思 - 混合基线"""
        host_id = uuid.uuid4()
        reflection_engine.config.baseline = ReflectionBaseline.HYBRID
        
        time_result = ReflectionResult(
            success=True,
            message="时间反思完成",
            conflicts_found=2,
            conflicts_resolved=2,
            memories_updated=2,
            execution_time=1.0
        )
        
        fact_result = ReflectionResult(
            success=True,
            message="事实反思完成",
            conflicts_found=3,
            conflicts_resolved=3,
            memories_updated=3,
            execution_time=1.5
        )
        
        with patch.object(
            reflection_engine,
            'time_based_reflection',
            new_callable=AsyncMock,
            return_value=time_result
        ):
            with patch.object(
                reflection_engine,
                'fact_based_reflection',
                new_callable=AsyncMock,
                return_value=fact_result
            ):
                result = await reflection_engine.comprehensive_reflection(host_id)
                
                assert result.success is True
                assert result.conflicts_found == 5  # 2 + 3
                assert result.conflicts_resolved == 5  # 2 + 3
                assert result.memories_updated == 5  # 2 + 3
                assert result.execution_time == 2.5  # 1.0 + 1.5
    
    @pytest.mark.asyncio
    async def test_error_handling_in_conflict_detection(self, reflection_engine):
        """测试冲突检测中的错误处理"""
        mock_data = [{"id": "1", "content": "测试"}]
        
        reflection_engine.render_evaluate_prompt_func = AsyncMock(side_effect=Exception("渲染失败"))
        
        conflicts = await reflection_engine._detect_conflicts(mock_data)
        
        # 应该返回空列表而不是抛出异常
        assert conflicts == []
    
    @pytest.mark.asyncio
    async def test_error_handling_in_conflict_resolution(self, reflection_engine):
        """测试冲突解决中的错误处理"""
        mock_conflicts = [{"memory_ids": ["1", "2"]}]
        
        reflection_engine.render_reflexion_prompt_func = AsyncMock(side_effect=Exception("渲染失败"))
        
        solutions = await reflection_engine._resolve_conflicts(mock_conflicts)
        
        # 应该返回空列表而不是抛出异常
        assert solutions == []
    
    @pytest.mark.asyncio
    async def test_apply_results_with_invalid_data(self, reflection_engine):
        """测试应用结果时处理无效数据"""
        # 缺少必要字段的数据
        invalid_solutions = [
            {"resolved": {"resolved_memory": {}}},  # 缺少必要字段
            {"invalid": "data"},  # 格式错误
        ]
        
        updated_count = await reflection_engine._apply_reflection_results(
            invalid_solutions
        )
        
        # 应该跳过无效数据
        assert updated_count == 0


class TestCreateReflectionEngine:
    """测试便捷函数"""
    
    def test_create_default_engine(self):
        """测试创建默认引擎"""
        engine = create_reflection_engine()
        
        assert isinstance(engine, ReflectionEngine)
        assert engine.config.enabled is False
        assert engine.config.iteration_period == "三小时"
    
    def test_create_custom_engine(self):
        """测试创建自定义引擎"""
        engine = create_reflection_engine(
            enabled=True,
            iteration_period="一小时",
            reflexion_range="database",
            baseline="FACT",
            concurrency=10
        )
        
        assert isinstance(engine, ReflectionEngine)
        assert engine.config.enabled is True
        assert engine.config.iteration_period == "一小时"
        assert engine.config.reflexion_range == "database"
        assert engine.config.baseline == "FACT"
        assert engine.config.concurrency == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
