"""
Memory 模块工具函数单元测试（直接导入版）

直接导入模块文件以避免循环导入问题
"""
import pytest
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# 文本处理工具测试
class TestTextUtils:
    """文本处理工具测试类"""
    
    def test_escape_lucene_query_basic(self):
        """测试基本的 Lucene 查询转义"""
        from app.core.memory.utils.text_utils import escape_lucene_query
        
        query = "user:admin AND status:active"
        escaped = escape_lucene_query(query)
        assert "\\:" in escaped
        assert "AND" in escaped
    
    def test_escape_lucene_query_none(self):
        """测试 None 输入"""
        from app.core.memory.utils.text_utils import escape_lucene_query
        
        result = escape_lucene_query(None)
        assert result == ""
    
    def test_extract_plain_query_simple(self):
        """测试简单文本提取"""
        from app.core.memory.utils.text_utils import extract_plain_query
        
        query = "  simple query  "
        result = extract_plain_query(query)
        assert result == "simple query"


# 时间处理工具测试
class TestTimeUtils:
    """时间处理工具测试类"""
    
    def test_validate_date_format_valid(self):
        """测试有效的日期格式"""
        from app.core.memory.utils.time_utils import validate_date_format
        
        assert validate_date_format("2025-10-28") is True
        assert validate_date_format("2025-1-1") is True
    
    def test_normalize_date_slash(self):
        """测试斜杠分隔的日期"""
        from app.core.memory.utils.time_utils import normalize_date
        
        result = normalize_date("2025/10/28")
        assert result == "2025-10-28"


# 本体定义测试
class TestOntology:
    """本体定义测试类"""
    
    def test_predicate_definitions_exist(self):
        """测试谓语定义存在"""
        from app.core.memory.utils.ontology import PREDICATE_DEFINITIONS
        
        assert isinstance(PREDICATE_DEFINITIONS, dict)
        assert len(PREDICATE_DEFINITIONS) > 0
        assert "IS_A" in PREDICATE_DEFINITIONS


# 数据模型测试
class TestJsonSchema:
    """JSON Schema 数据模型测试类"""
    
    def test_base_data_schema_creation(self):
        """测试基础数据模型创建"""
        from app.schemas.memory_storage_schema import BaseDataSchema
        
        data = BaseDataSchema(
            id="test_id",
            statement="test statement",
            group_id="group_1",
            chunk_id="chunk_1",
            created_at="2025-10-28T10:00:00",
        )
        assert data.id == "test_id"
        assert data.statement == "test statement"


# API 消息模型测试
class TestMessages:
    """API 消息模型测试类"""
    
    def test_api_response_ok(self):
        """测试成功响应"""
        from app.schemas.memory_storage_schema import ok
        
        response = ok(msg="操作成功", data={"result": "success"})
        assert response.code == 0
        assert response.msg == "操作成功"


# 运行时配置覆写测试
class TestRuntimeOverrides:
    """运行时配置覆写测试类"""
    
    def test_to_bool_from_bool(self):
        """测试从布尔值转换"""
        from app.core.memory.utils.overrides import _to_bool
        
        assert _to_bool(True) is True
        assert _to_bool(False) is False
    
    def test_to_bool_from_string(self):
        """测试从字符串转换"""
        from app.core.memory.utils.overrides import _to_bool
        
        assert _to_bool("true") is True
        assert _to_bool("false") is False


# 输出路径测试
class TestOutputPaths:
    """输出路径测试类"""
    
    def test_get_memory_output_dir(self):
        """测试获取输出目录"""
        from app.core.memory.utils.output_paths import get_memory_output_dir
        
        output_dir = get_memory_output_dir()
        assert "logs" in output_dir
        assert "memory-output" in output_dir


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
