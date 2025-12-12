"""
Memory 模块工具函数单元测试（简化版）

测试独立的工具函数，避免循环导入问题
"""
import pytest
from datetime import datetime
import json


# 文本处理工具测试
class TestTextUtils:
    """文本处理工具测试类"""
    
    def test_escape_lucene_query_basic(self):
        """测试基本的 Lucene 查询转义"""
        # 直接导入函数以避免循环导入
        from app.core.memory.utils import text_utils
        
        query = "user:admin AND status:active"
        escaped = text_utils.escape_lucene_query(query)
        assert "\\:" in escaped
        assert "AND" in escaped
    
    def test_escape_lucene_query_special_chars(self):
        """测试特殊字符转义"""
        from app.core.memory.utils import text_utils
        
        query = "test && query || with (special) [chars]"
        escaped = text_utils.escape_lucene_query(query)
        assert "\\&&" in escaped or "\\&\\&" in escaped
        assert "\\||" in escaped or "\\|\\|" in escaped
    
    def test_escape_lucene_query_none(self):
        """测试 None 输入"""
        from app.core.memory.utils import text_utils
        
        result = text_utils.escape_lucene_query(None)
        assert result == ""
    
    def test_extract_plain_query_simple(self):
        """测试简单文本提取"""
        from app.core.memory.utils import text_utils
        
        query = "  simple query  "
        result = text_utils.extract_plain_query(query)
        assert result == "simple query"
    
    def test_extract_plain_query_with_quotes(self):
        """测试带引号的文本提取"""
        from app.core.memory.utils import text_utils
        
        query = '"quoted query"'
        result = text_utils.extract_plain_query(query)
        assert result == "quoted query"
    
    def test_extract_plain_query_dict(self):
        """测试字典输入"""
        from app.core.memory.utils import text_utils
        
        query = {"original": "test query", "context": {}}
        result = text_utils.extract_plain_query(query)
        assert result == "test query"


# 时间处理工具测试
class TestTimeUtils:
    """时间处理工具测试类"""
    
    def test_validate_date_format_valid(self):
        """测试有效的日期格式"""
        from app.core.memory.utils import time_utils
        
        assert time_utils.validate_date_format("2025-10-28") is True
        assert time_utils.validate_date_format("2025-1-1") is True
    
    def test_validate_date_format_invalid(self):
        """测试无效的日期格式"""
        from app.core.memory.utils import time_utils
        
        assert time_utils.validate_date_format("2025/10/28") is False
        assert time_utils.validate_date_format("28-10-2025") is False
        assert time_utils.validate_date_format("invalid") is False
    
    def test_normalize_date_slash(self):
        """测试斜杠分隔的日期"""
        from app.core.memory.utils import time_utils
        
        result = time_utils.normalize_date("2025/10/28")
        assert result == "2025-10-28"
    
    def test_normalize_date_dot(self):
        """测试点分隔的日期"""
        from app.core.memory.utils import time_utils
        
        result = time_utils.normalize_date("2025.10.28")
        assert result == "2025-10-28"
    
    def test_normalize_date_no_separator(self):
        """测试无分隔符的日期"""
        from app.core.memory.utils import time_utils
        
        result = time_utils.normalize_date("20251028")
        assert result == "2025-10-28"
    
    def test_normalize_date_safe_with_default(self):
        """测试带默认值的安全日期标准化"""
        from app.core.memory.utils import time_utils
        
        result = time_utils.normalize_date_safe("invalid_date", default="2025-01-01")
        assert result == "2025-01-01"


# 本体定义测试
class TestOntology:
    """本体定义测试类"""
    
    def test_predicate_definitions_exist(self):
        """测试谓语定义存在"""
        from app.core.memory.utils import ontology
        
        assert isinstance(ontology.PREDICATE_DEFINITIONS, dict)
        assert len(ontology.PREDICATE_DEFINITIONS) > 0
        assert "IS_A" in ontology.PREDICATE_DEFINITIONS
        assert "HAS_A" in ontology.PREDICATE_DEFINITIONS
    
    def test_label_definitions_exist(self):
        """测试标签定义存在"""
        from app.core.memory.utils import ontology
        
        assert isinstance(ontology.LABEL_DEFINITIONS, dict)
        assert "statement_labelling" in ontology.LABEL_DEFINITIONS
        assert "temporal_labelling" in ontology.LABEL_DEFINITIONS
    
    def test_predicate_enum(self):
        """测试谓语枚举"""
        from app.core.memory.utils import ontology
        
        assert ontology.Predicate.IS_A == "IS_A"
        assert ontology.Predicate.HAS_A == "HAS_A"
        assert ontology.Predicate.LOCATED_IN == "LOCATED_IN"
    
    def test_statement_type_enum(self):
        """测试陈述句类型枚举"""
        from app.core.memory.utils import ontology
        
        assert ontology.StatementType.FACT == "FACT"
        assert ontology.StatementType.OPINION == "OPINION"
        assert ontology.StatementType.PREDICTION == "PREDICTION"
    
    def test_temporal_info_enum(self):
        """测试时间信息枚举"""
        from app.core.memory.utils import ontology
        
        assert ontology.TemporalInfo.STATIC == "STATIC"
        assert ontology.TemporalInfo.DYNAMIC == "DYNAMIC"
        assert ontology.TemporalInfo.ATEMPORAL == "ATEMPORAL"


# 数据模型测试
class TestJsonSchema:
    """JSON Schema 数据模型测试类"""
    
    def test_base_data_schema_creation(self):
        """测试基础数据模型创建"""
        from app.core.memory.utils import json_schema
        
        data = json_schema.BaseDataSchema(
            id="test_id",
            statement="test statement",
            group_id="group_1",
            chunk_id="chunk_1",
            created_at="2025-10-28T10:00:00",
        )
        assert data.id == "test_id"
        assert data.statement == "test statement"
        assert data.group_id == "group_1"
    
    def test_base_data_schema_with_optional_fields(self):
        """测试带可选字段的基础数据模型"""
        from app.core.memory.utils import json_schema
        
        data = json_schema.BaseDataSchema(
            id="test_id",
            statement="test statement",
            group_id="group_1",
            chunk_id="chunk_1",
            created_at="2025-10-28T10:00:00",
            expired_at="2025-12-31T23:59:59",
            valid_at="2025-10-28T10:00:00",
            entity_ids=["entity_1", "entity_2"],
        )
        assert data.expired_at == "2025-12-31T23:59:59"
        assert len(data.entity_ids) == 2
    
    def test_reflexion_schema_creation(self):
        """测试反思模型创建"""
        from app.core.memory.utils import json_schema
        
        reflexion = json_schema.ReflexionSchema(
            reason="测试原因",
            solution="测试解决方案",
        )
        assert reflexion.reason == "测试原因"
        assert reflexion.solution == "测试解决方案"


# API 消息模型测试
class TestMessages:
    """API 消息模型测试类"""
    
    def test_config_key_creation(self):
        """测试配置键模型创建"""
        from app.core.memory.utils import messages
        
        key = messages.ConfigKey(
            config_id=123,
            user_id="user_1",
            apply_id="app_1",
        )
        assert key.config_id == 123
        assert key.user_id == "user_1"
        assert key.apply_id == "app_1"
    
    def test_api_response_ok(self):
        """测试成功响应"""
        from app.core.memory.utils import messages
        
        response = messages.ok(msg="操作成功", data={"result": "success"})
        assert response.code == 0
        assert response.msg == "操作成功"
        assert response.data == {"result": "success"}
        assert response.error == ""
    
    def test_api_response_fail(self):
        """测试失败响应"""
        from app.core.memory.utils import messages
        
        response = messages.fail(msg="操作失败", error_code="ERROR_001")
        assert response.code == 1
        assert response.msg == "操作失败"
        assert response.error == "ERROR_001"


# 运行时配置覆写测试
class TestRuntimeOverrides:
    """运行时配置覆写测试类"""
    
    def test_to_bool_from_bool(self):
        """测试从布尔值转换"""
        from app.core.memory.utils import runtime_overrides_unified
        
        assert runtime_overrides_unified._to_bool(True) is True
        assert runtime_overrides_unified._to_bool(False) is False
    
    def test_to_bool_from_int(self):
        """测试从整数转换"""
        from app.core.memory.utils import runtime_overrides_unified
        
        assert runtime_overrides_unified._to_bool(1) is True
        assert runtime_overrides_unified._to_bool(0) is False
        assert runtime_overrides_unified._to_bool(100) is True
    
    def test_to_bool_from_string(self):
        """测试从字符串转换"""
        from app.core.memory.utils import runtime_overrides_unified
        
        assert runtime_overrides_unified._to_bool("true") is True
        assert runtime_overrides_unified._to_bool("TRUE") is True
        assert runtime_overrides_unified._to_bool("1") is True
        assert runtime_overrides_unified._to_bool("yes") is True
        assert runtime_overrides_unified._to_bool("false") is False
        assert runtime_overrides_unified._to_bool("FALSE") is False
        assert runtime_overrides_unified._to_bool("0") is False
        assert runtime_overrides_unified._to_bool("no") is False
    
    def test_set_if_present_success(self):
        """测试成功设置值"""
        from app.core.memory.utils import runtime_overrides_unified
        
        target = {}
        source = {"key": "value"}
        runtime_overrides_unified._set_if_present(target, "target_key", source, "key", str)
        assert target["target_key"] == "value"
    
    def test_set_if_present_missing_key(self):
        """测试缺失键"""
        from app.core.memory.utils import runtime_overrides_unified
        
        target = {}
        source = {}
        runtime_overrides_unified._set_if_present(target, "target_key", source, "key", str)
        assert "target_key" not in target
    
    def test_set_if_present_none_value(self):
        """测试 None 值"""
        from app.core.memory.utils import runtime_overrides_unified
        
        target = {}
        source = {"key": None}
        runtime_overrides_unified._set_if_present(target, "target_key", source, "key", str)
        assert "target_key" not in target


# 输出路径测试
class TestOutputPaths:
    """输出路径测试类"""
    
    def test_get_memory_output_dir(self):
        """测试获取输出目录"""
        from app.core.memory.utils import output_paths
        
        output_dir = output_paths.get_memory_output_dir()
        assert "logs" in output_dir
        assert "memory-output" in output_dir
    
    def test_get_memory_output_path(self):
        """测试获取输出文件路径"""
        from app.core.memory.utils import output_paths
        
        file_path = output_paths.get_memory_output_path("test.txt")
        assert "logs" in file_path
        assert "memory-output" in file_path
        assert "test.txt" in file_path


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
