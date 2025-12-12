"""
Memory 模块工具函数单元测试

测试 app/core/memory/utils/ 中的各类工具函数
"""
import pytest
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock
import json

# 文本处理工具测试
# 直接导入模块以避免循环导入
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.core.memory.utils.text_utils import escape_lucene_query, extract_plain_query


class TestTextUtils:
    """文本处理工具测试类"""
    
    def test_escape_lucene_query_basic(self):
        """测试基本的 Lucene 查询转义"""
        query = "user:admin AND status:active"
        escaped = escape_lucene_query(query)
        assert "\\:" in escaped
        assert "AND" in escaped
    
    def test_escape_lucene_query_special_chars(self):
        """测试特殊字符转义"""
        query = "test && query || with (special) [chars]"
        escaped = escape_lucene_query(query)
        assert "\\&&" in escaped
        assert "\\||" in escaped
        assert "\\(" in escaped
        assert "\\)" in escaped
        assert "\\[" in escaped
        assert "\\]" in escaped
    
    def test_escape_lucene_query_none(self):
        """测试 None 输入"""
        result = escape_lucene_query(None)
        assert result == ""
    
    def test_extract_plain_query_simple(self):
        """测试简单文本提取"""
        query = "  simple query  "
        result = extract_plain_query(query)
        assert result == "simple query"
    
    def test_extract_plain_query_with_quotes(self):
        """测试带引号的文本提取"""
        query = '"quoted query"'
        result = extract_plain_query(query)
        assert result == "quoted query"
    
    def test_extract_plain_query_dict(self):
        """测试字典输入"""
        query = {"original": "test query", "context": {}}
        result = extract_plain_query(query)
        assert result == "test query"
    
    def test_extract_plain_query_none(self):
        """测试 None 输入"""
        result = extract_plain_query(None)
        assert result == ""


# 时间处理工具测试
from app.core.memory.utils.time_utils import (
    validate_date_format,
    normalize_date,
    normalize_date_safe,
)


class TestTimeUtils:
    """时间处理工具测试类"""
    
    def test_validate_date_format_valid(self):
        """测试有效的日期格式"""
        assert validate_date_format("2025-10-28") is True
        assert validate_date_format("2025-1-1") is True
    
    def test_validate_date_format_invalid(self):
        """测试无效的日期格式"""
        assert validate_date_format("2025/10/28") is False
        assert validate_date_format("28-10-2025") is False
        assert validate_date_format("invalid") is False
    
    def test_normalize_date_slash(self):
        """测试斜杠分隔的日期"""
        result = normalize_date("2025/10/28")
        assert result == "2025-10-28"
    
    def test_normalize_date_dot(self):
        """测试点分隔的日期"""
        result = normalize_date("2025.10.28")
        assert result == "2025-10-28"
    
    def test_normalize_date_no_separator(self):
        """测试无分隔符的日期"""
        result = normalize_date("20251028")
        assert result == "2025-10-28"
    
    def test_normalize_date_safe_with_default(self):
        """测试带默认值的安全日期标准化"""
        result = normalize_date_safe("invalid_date", default="2025-01-01")
        assert result == "2025-01-01"
    
    def test_normalize_date_safe_valid(self):
        """测试有效日期的安全标准化"""
        result = normalize_date_safe("2025/10/28")
        assert result == "2025-10-28"


# 本体定义测试
from app.core.memory.utils.ontology import (
    PREDICATE_DEFINITIONS,
    LABEL_DEFINITIONS,
    Predicate,
    StatementType,
    TemporalInfo,
)


class TestOntology:
    """本体定义测试类"""
    
    def test_predicate_definitions_exist(self):
        """测试谓语定义存在"""
        assert isinstance(PREDICATE_DEFINITIONS, dict)
        assert len(PREDICATE_DEFINITIONS) > 0
        assert "IS_A" in PREDICATE_DEFINITIONS
        assert "HAS_A" in PREDICATE_DEFINITIONS
    
    def test_label_definitions_exist(self):
        """测试标签定义存在"""
        assert isinstance(LABEL_DEFINITIONS, dict)
        assert "statement_labelling" in LABEL_DEFINITIONS
        assert "temporal_labelling" in LABEL_DEFINITIONS
    
    def test_predicate_enum(self):
        """测试谓语枚举"""
        assert Predicate.IS_A == "IS_A"
        assert Predicate.HAS_A == "HAS_A"
        assert Predicate.LOCATED_IN == "LOCATED_IN"
    
    def test_statement_type_enum(self):
        """测试陈述句类型枚举"""
        assert StatementType.FACT == "FACT"
        assert StatementType.OPINION == "OPINION"
        assert StatementType.PREDICTION == "PREDICTION"
    
    def test_temporal_info_enum(self):
        """测试时间信息枚举"""
        assert TemporalInfo.STATIC == "STATIC"
        assert TemporalInfo.DYNAMIC == "DYNAMIC"
        assert TemporalInfo.ATEMPORAL == "ATEMPORAL"


# 数据模型测试
from app.schemas.memory_storage_schema import (
    BaseDataSchema,
    ConflictResultSchema,
    ReflexionSchema,
)


class TestJsonSchema:
    """JSON Schema 数据模型测试类"""
    
    def test_base_data_schema_creation(self):
        """测试基础数据模型创建"""
        data = BaseDataSchema(
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
        data = BaseDataSchema(
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
        reflexion = ReflexionSchema(
            reason="测试原因",
            solution="测试解决方案",
        )
        assert reflexion.reason == "测试原因"
        assert reflexion.solution == "测试解决方案"


# API 消息模型测试
from app.schemas.memory_storage_schema import (
    ConfigKey,
    ApiResponse,
    ok,
    fail,
)


class TestMessages:
    """API 消息模型测试类"""
    
    def test_config_key_creation(self):
        """测试配置键模型创建"""
        key = ConfigKey(
            config_id=123,
            user_id="user_1",
            apply_id="app_1",
        )
        assert key.config_id == 123
        assert key.user_id == "user_1"
        assert key.apply_id == "app_1"
    
    def test_api_response_ok(self):
        """测试成功响应"""
        response = ok(msg="操作成功", data={"result": "success"})
        assert response.code == 0
        assert response.msg == "操作成功"
        assert response.data == {"result": "success"}
        assert response.error == ""
    
    def test_api_response_fail(self):
        """测试失败响应"""
        response = fail(msg="操作失败", error_code="ERROR_001")
        assert response.code == 1
        assert response.msg == "操作失败"
        assert response.error == "ERROR_001"
    
    def test_api_response_with_time(self):
        """测试带时间戳的响应"""
        response = ok(msg="测试", time=1234567890)
        assert response.time == 1234567890


# 配置工具测试（需要 mock）
# 跳过配置工具测试以避免循环导入
# from app.core.memory.utils.config_utils import (
#     get_neo4j_config,
#     get_chunker_config,
# )


# 配置工具测试被跳过以避免循环导入
# class TestConfigUtils:
#     """配置工具测试类"""
#     pass


# 运行时配置覆写测试
from app.core.memory.utils.overrides import (
    _to_bool,
    _set_if_present,
)


class TestRuntimeOverrides:
    """运行时配置覆写测试类"""
    
    def test_to_bool_from_bool(self):
        """测试从布尔值转换"""
        assert _to_bool(True) is True
        assert _to_bool(False) is False
    
    def test_to_bool_from_int(self):
        """测试从整数转换"""
        assert _to_bool(1) is True
        assert _to_bool(0) is False
        assert _to_bool(100) is True
    
    def test_to_bool_from_string(self):
        """测试从字符串转换"""
        assert _to_bool("true") is True
        assert _to_bool("TRUE") is True
        assert _to_bool("1") is True
        assert _to_bool("yes") is True
        assert _to_bool("false") is False
        assert _to_bool("FALSE") is False
        assert _to_bool("0") is False
        assert _to_bool("no") is False
    
    def test_set_if_present_success(self):
        """测试成功设置值"""
        target = {}
        source = {"key": "value"}
        _set_if_present(target, "target_key", source, "key", str)
        assert target["target_key"] == "value"
    
    def test_set_if_present_missing_key(self):
        """测试缺失键"""
        target = {}
        source = {}
        _set_if_present(target, "target_key", source, "key", str)
        assert "target_key" not in target
    
    def test_set_if_present_none_value(self):
        """测试 None 值"""
        target = {}
        source = {"key": None}
        _set_if_present(target, "target_key", source, "key", str)
        assert "target_key" not in target


# 输出路径测试
from app.core.memory.utils.output_paths import (
    get_memory_output_dir,
    get_memory_output_path,
)


class TestOutputPaths:
    """输出路径测试类"""
    
    def test_get_memory_output_dir(self):
        """测试获取输出目录"""
        output_dir = get_memory_output_dir()
        assert "logs" in output_dir
        assert "memory-output" in output_dir
    
    def test_get_memory_output_path(self):
        """测试获取输出文件路径"""
        file_path = get_memory_output_path("test.txt")
        assert "logs" in file_path
        assert "memory-output" in file_path
        assert "test.txt" in file_path


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
