# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/6
import pytest

from app.core.workflow.nodes import StartNode
from app.core.workflow.variable.base_variable import VariableType
from app.core.workflow.variable_pool import VariablePool
from tests.workflow.nodes.base import (
    simple_state, 
    simple_vairable_pool,
    TEST_EXECUTION_ID,
    TEST_WORKSPACE_ID,
    TEST_USER_ID,
    TEST_CONVERSATION_ID,
    TEST_FILE
)


async def create_variable_pool_with_inputs(message: str, input_variables: dict = None):
    """创建带有自定义输入变量的变量池"""
    variable_pool = VariablePool()
    
    sys_vars = {
        "message": (message, VariableType.STRING),
        "conversation_id": (TEST_CONVERSATION_ID, VariableType.STRING),
        "execution_id": (TEST_EXECUTION_ID, VariableType.STRING),
        "workspace_id": (TEST_WORKSPACE_ID, VariableType.STRING),
        "user_id": (TEST_USER_ID, VariableType.STRING),
        "input_variables": (input_variables or {}, VariableType.OBJECT),
        "files": ([TEST_FILE], VariableType.ARRAY_FILE)
    }
    
    for key, var_def in sys_vars.items():
        value = var_def[0]
        var_type = var_def[1]
        await variable_pool.new(
            namespace='sys',
            key=key,
            value=value,
            var_type=VariableType(var_type),
            mut=False  # 系统变量不可变
        )
    
    return variable_pool


# 基础配置 - 无自定义变量
BASIC_CONFIG = {
    "id": "start_test",
    "type": "start",
    "name": "开始节点",
    "config": {
        "variables": []
    }
}

# 带单个自定义变量的配置
SINGLE_VARIABLE_CONFIG = {
    "id": "start_test",
    "type": "start",
    "name": "开始节点",
    "config": {
        "variables": [
            {
                "name": "language",
                "type": "string",
                "required": False,
                "default": "zh-CN",
                "description": "语言设置"
            }
        ]
    }
}

# 带多个自定义变量的配置
MULTI_VARIABLES_CONFIG = {
    "id": "start_test",
    "type": "start",
    "name": "开始节点",
    "config": {
        "variables": [
            {
                "name": "language",
                "type": "string",
                "required": False,
                "default": "zh-CN",
                "description": "语言设置"
            },
            {
                "name": "max_length",
                "type": "number",
                "required": False,
                "default": 1000,
                "description": "最大长度"
            },
            {
                "name": "enable_cache",
                "type": "boolean",
                "required": False,
                "default": True,
                "description": "是否启用缓存"
            }
        ]
    }
}

# 带必需变量的配置
REQUIRED_VARIABLE_CONFIG = {
    "id": "start_test",
    "type": "start",
    "name": "开始节点",
    "config": {
        "variables": [
            {
                "name": "api_key",
                "type": "string",
                "required": True,
                "description": "API密钥"
            }
        ]
    }
}

# 混合必需和可选变量的配置
MIXED_VARIABLES_CONFIG = {
    "id": "start_test",
    "type": "start",
    "name": "开始节点",
    "config": {
        "variables": [
            {
                "name": "user_id",
                "type": "string",
                "required": True,
                "description": "用户ID"
            },
            {
                "name": "timeout",
                "type": "number",
                "required": False,
                "default": 30,
                "description": "超时时间（秒）"
            }
        ]
    }
}


# 不同类型变量的配置
DIFFERENT_TYPES_CONFIG = {
    "id": "start_test",
    "type": "start",
    "name": "开始节点",
    "config": {
        "variables": [
            {
                "name": "name",
                "type": "string",
                "required": False,
                "default": "default_name",
                "description": "名称"
            },
            {
                "name": "count",
                "type": "number",
                "required": False,
                "default": 0,
                "description": "计数"
            },
            {
                "name": "enabled",
                "type": "boolean",
                "required": False,
                "default": False,
                "description": "是否启用"
            },
            {
                "name": "tags",
                "type": "array[string]",
                "required": False,
                "default": [],
                "description": "标签列表"
            },
            {
                "name": "config",
                "type": "object",
                "required": False,
                "default": {},
                "description": "配置对象"
            }
        ]
    }
}


# ==================== 基础功能测试 ====================
@pytest.mark.asyncio
async def test_start_node_basic():
    """测试基础 Start 节点（无自定义变量）"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test message")
    
    result = await StartNode(BASIC_CONFIG, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert "message" in result
    assert "execution_id" in result
    assert "conversation_id" in result
    assert "workspace_id" in result
    assert "user_id" in result
    assert result["message"] == "test message"


@pytest.mark.asyncio
async def test_start_node_system_variables():
    """测试系统变量输出"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("hello world")
    
    result = await StartNode(BASIC_CONFIG, {}).execute(state, variable_pool)
    
    assert result["message"] == "hello world"
    assert result["execution_id"] == state["execution_id"]
    assert result["workspace_id"] == state["workspace_id"]
    assert result["user_id"] == state["user_id"]


# ==================== 自定义变量测试 ====================
@pytest.mark.asyncio
async def test_start_node_single_variable_with_default():
    """测试单个自定义变量使用默认值"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    result = await StartNode(SINGLE_VARIABLE_CONFIG, {}).execute(state, variable_pool)
    
    assert "language" in result
    assert result["language"] == "zh-CN"


@pytest.mark.asyncio
async def test_start_node_single_variable_with_input():
    """测试单个自定义变量使用输入值"""
    state = simple_state()
    
    # 使用带输入变量的变量池
    input_vars = {"language": "en-US"}
    variable_pool = await create_variable_pool_with_inputs("test", input_vars)
    
    result = await StartNode(SINGLE_VARIABLE_CONFIG, {}).execute(state, variable_pool)
    
    assert "language" in result
    assert result["language"] == "en-US"


@pytest.mark.asyncio
async def test_start_node_multi_variables_with_defaults():
    """测试多个自定义变量使用默认值"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    result = await StartNode(MULTI_VARIABLES_CONFIG, {}).execute(state, variable_pool)
    
    assert "language" in result
    assert "max_length" in result
    assert "enable_cache" in result
    assert result["language"] == "zh-CN"
    assert result["max_length"] == 1000
    assert result["enable_cache"] is True


@pytest.mark.asyncio
async def test_start_node_multi_variables_with_inputs():
    """测试多个自定义变量使用输入值"""
    state = simple_state()
    
    # 使用带输入变量的变量池
    input_vars = {
        "language": "ja-JP",
        "max_length": 2000,
        "enable_cache": False
    }
    variable_pool = await create_variable_pool_with_inputs("test", input_vars)
    
    result = await StartNode(MULTI_VARIABLES_CONFIG, {}).execute(state, variable_pool)
    
    assert result["language"] == "ja-JP"
    assert result["max_length"] == 2000
    assert result["enable_cache"] is False


@pytest.mark.asyncio
async def test_start_node_partial_inputs():
    """测试部分输入变量，其他使用默认值"""
    state = simple_state()
    
    # 只设置部分输入变量
    input_vars = {
        "language": "fr-FR"
    }
    variable_pool = await create_variable_pool_with_inputs("test", input_vars)
    
    result = await StartNode(MULTI_VARIABLES_CONFIG, {}).execute(state, variable_pool)
    
    assert result["language"] == "fr-FR"  # 使用输入值
    assert result["max_length"] == 1000  # 使用默认值
    assert result["enable_cache"] is True  # 使用默认值


# ==================== 必需变量测试 ====================
@pytest.mark.asyncio
async def test_start_node_required_variable_provided():
    """测试提供必需变量"""
    state = simple_state()
    
    # 提供必需变量
    input_vars = {
        "api_key": "test_api_key_12345"
    }
    variable_pool = await create_variable_pool_with_inputs("test", input_vars)
    
    result = await StartNode(REQUIRED_VARIABLE_CONFIG, {}).execute(state, variable_pool)
    
    assert "api_key" in result
    assert result["api_key"] == "test_api_key_12345"


@pytest.mark.asyncio
async def test_start_node_required_variable_missing():
    """测试缺少必需变量"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    # 不提供必需变量
    with pytest.raises(ValueError) as exc_info:
        await StartNode(REQUIRED_VARIABLE_CONFIG, {}).execute(state, variable_pool)
    
    assert "缺少必需的输入变量" in str(exc_info.value)
    assert "api_key" in str(exc_info.value)


@pytest.mark.asyncio
async def test_start_node_mixed_variables():
    """测试混合必需和可选变量"""
    state = simple_state()
    
    # 只提供必需变量
    input_vars = {
        "user_id": "user_123"
    }
    variable_pool = await create_variable_pool_with_inputs("test", input_vars)
    
    result = await StartNode(MIXED_VARIABLES_CONFIG, {}).execute(state, variable_pool)
    
    assert result["user_id"] == "user_123"  # 必需变量
    assert result["timeout"] == 30  # 可选变量使用默认值


@pytest.mark.asyncio
async def test_start_node_mixed_variables_all_provided():
    """测试混合变量全部提供"""
    state = simple_state()
    
    # 提供所有变量
    input_vars = {
        "user_id": "user_456",
        "timeout": 60
    }
    variable_pool = await create_variable_pool_with_inputs("test", input_vars)
    
    result = await StartNode(MIXED_VARIABLES_CONFIG, {}).execute(state, variable_pool)
    
    assert result["user_id"] == "user_456"
    assert result["timeout"] == 60


# ==================== 不同类型变量测试 ====================
@pytest.mark.asyncio
async def test_start_node_different_types_defaults():
    """测试不同类型变量的默认值"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    result = await StartNode(DIFFERENT_TYPES_CONFIG, {}).execute(state, variable_pool)
    
    assert result["name"] == "default_name"
    assert result["count"] == 0
    assert result["enabled"] is False
    assert result["tags"] == []
    assert result["config"] == {}


@pytest.mark.asyncio
async def test_start_node_different_types_inputs():
    """测试不同类型变量的输入值"""
    state = simple_state()
    
    # 提供不同类型的输入值
    input_vars = {
        "name": "custom_name",
        "count": 100,
        "enabled": True,
        "tags": ["tag1", "tag2", "tag3"],
        "config": {"key": "value", "nested": {"data": 123}}
    }
    variable_pool = await create_variable_pool_with_inputs("test", input_vars)
    
    result = await StartNode(DIFFERENT_TYPES_CONFIG, {}).execute(state, variable_pool)
    
    assert result["name"] == "custom_name"
    assert result["count"] == 100
    assert result["enabled"] is True
    assert result["tags"] == ["tag1", "tag2", "tag3"]
    assert result["config"] == {"key": "value", "nested": {"data": 123}}


# ==================== 边界情况测试 ====================
@pytest.mark.asyncio
async def test_start_node_empty_message():
    """测试空消息"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("")
    
    result = await StartNode(BASIC_CONFIG, {}).execute(state, variable_pool)
    
    assert result["message"] == ""


@pytest.mark.asyncio
async def test_start_node_no_input_variables():
    """测试没有输入变量的情况"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    # 不设置 input_variables
    result = await StartNode(SINGLE_VARIABLE_CONFIG, {}).execute(state, variable_pool)
    
    # 应该使用默认值
    assert result["language"] == "zh-CN"


@pytest.mark.asyncio
async def test_start_node_empty_input_variables():
    """测试空的输入变量字典"""
    state = simple_state()
    
    # 设置空的输入变量字典
    variable_pool = await create_variable_pool_with_inputs("test", {})
    
    result = await StartNode(SINGLE_VARIABLE_CONFIG, {}).execute(state, variable_pool)
    
    # 应该使用默认值
    assert result["language"] == "zh-CN"


@pytest.mark.asyncio
async def test_start_node_extra_input_variables():
    """测试额外的输入变量（未在配置中定义）"""
    state = simple_state()
    
    # 提供额外的未定义变量
    input_vars = {
        "language": "de-DE",
        "extra_var": "should_be_ignored"
    }
    variable_pool = await create_variable_pool_with_inputs("test", input_vars)
    
    result = await StartNode(SINGLE_VARIABLE_CONFIG, {}).execute(state, variable_pool)
    
    assert result["language"] == "de-DE"
    assert "extra_var" not in result  # 额外变量不应该出现在输出中


# ==================== 数组类型变量测试 ====================
@pytest.mark.asyncio
async def test_start_node_array_string_variable():
    """测试字符串数组变量"""
    state = simple_state()
    
    config = {
        "id": "start_test",
        "type": "start",
        "name": "开始节点",
        "config": {
            "variables": [
                {
                    "name": "categories",
                    "type": "array[string]",
                    "required": False,
                    "default": ["default1", "default2"],
                    "description": "分类列表"
                }
            ]
        }
    }
    
    input_vars = {
        "categories": ["cat1", "cat2", "cat3"]
    }
    variable_pool = await create_variable_pool_with_inputs("test", input_vars)
    
    result = await StartNode(config, {}).execute(state, variable_pool)
    
    assert result["categories"] == ["cat1", "cat2", "cat3"]


@pytest.mark.asyncio
async def test_start_node_array_number_variable():
    """测试数字数组变量"""
    state = simple_state()
    
    config = {
        "id": "start_test",
        "type": "start",
        "name": "开始节点",
        "config": {
            "variables": [
                {
                    "name": "scores",
                    "type": "array[number]",
                    "required": False,
                    "default": [0, 0, 0],
                    "description": "分数列表"
                }
            ]
        }
    }
    
    input_vars = {
        "scores": [85, 90, 95]
    }
    variable_pool = await create_variable_pool_with_inputs("test", input_vars)
    
    result = await StartNode(config, {}).execute(state, variable_pool)
    
    assert result["scores"] == [85, 90, 95]


@pytest.mark.asyncio
async def test_start_node_array_object_variable():
    """测试对象数组变量"""
    state = simple_state()
    
    config = {
        "id": "start_test",
        "type": "start",
        "name": "开始节点",
        "config": {
            "variables": [
                {
                    "name": "users",
                    "type": "array[object]",
                    "required": False,
                    "default": [],
                    "description": "用户列表"
                }
            ]
        }
    }
    
    input_vars = {
        "users": [
            {"name": "Alice", "age": 25},
            {"name": "Bob", "age": 30}
        ]
    }
    variable_pool = await create_variable_pool_with_inputs("test", input_vars)
    
    result = await StartNode(config, {}).execute(state, variable_pool)
    
    assert len(result["users"]) == 2
    assert result["users"][0]["name"] == "Alice"
    assert result["users"][1]["age"] == 30


# ==================== 复杂场景测试 ====================
@pytest.mark.asyncio
async def test_start_node_complex_object():
    """测试复杂对象变量"""
    state = simple_state()
    
    config = {
        "id": "start_test",
        "type": "start",
        "name": "开始节点",
        "config": {
            "variables": [
                {
                    "name": "settings",
                    "type": "object",
                    "required": False,
                    "default": {"theme": "light"},
                    "description": "设置对象"
                }
            ]
        }
    }
    
    input_vars = {
        "settings": {
            "theme": "dark",
            "language": "zh-CN",
            "notifications": {
                "email": True,
                "sms": False
            },
            "features": ["feature1", "feature2"]
        }
    }
    variable_pool = await create_variable_pool_with_inputs("test", input_vars)
    
    result = await StartNode(config, {}).execute(state, variable_pool)
    
    assert result["settings"]["theme"] == "dark"
    assert result["settings"]["language"] == "zh-CN"
    assert result["settings"]["notifications"]["email"] is True
    assert result["settings"]["features"] == ["feature1", "feature2"]


@pytest.mark.asyncio
async def test_start_node_zero_and_false_values():
    """测试零值和 False 值（确保不被当作空值）"""
    state = simple_state()
    
    config = {
        "id": "start_test",
        "type": "start",
        "name": "开始节点",
        "config": {
            "variables": [
                {
                    "name": "count",
                    "type": "number",
                    "required": False,
                    "default": 10,
                    "description": "计数"
                },
                {
                    "name": "enabled",
                    "type": "boolean",
                    "required": False,
                    "default": True,
                    "description": "是否启用"
                }
            ]
        }
    }
    
    input_vars = {
        "count": 0,
        "enabled": False
    }
    variable_pool = await create_variable_pool_with_inputs("test", input_vars)
    
    result = await StartNode(config, {}).execute(state, variable_pool)
    
    # 0 和 False 应该被正确识别，而不是使用默认值
    assert result["count"] == 0
    assert result["enabled"] is False


@pytest.mark.asyncio
async def test_start_node_output_types():
    """测试输出类型定义"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    node = StartNode(MULTI_VARIABLES_CONFIG, {})
    await node.execute(state, variable_pool)
    
    output_types = node._output_types()
    
    # 验证系统变量类型
    assert output_types["message"] == VariableType.STRING
    assert output_types["execution_id"] == VariableType.STRING
    assert output_types["conversation_id"] == VariableType.STRING
    assert output_types["workspace_id"] == VariableType.STRING
    assert output_types["user_id"] == VariableType.STRING
    
    # 验证自定义变量类型
    assert output_types["language"] == VariableType.STRING
    assert output_types["max_length"] == VariableType.NUMBER
    assert output_types["enable_cache"] == VariableType.BOOLEAN


@pytest.mark.asyncio
async def test_start_node_multiple_executions():
    """测试多次执行 Start 节点"""
    state = simple_state()
    
    node = StartNode(SINGLE_VARIABLE_CONFIG, {})
    
    # 第一次执行
    variable_pool1 = await create_variable_pool_with_inputs("first message", {})
    result1 = await node.execute(state, variable_pool1)
    assert result1["message"] == "first message"
    assert result1["language"] == "zh-CN"
    
    # 第二次执行（使用新的变量池）
    variable_pool2 = await create_variable_pool_with_inputs("second message", {})
    result2 = await node.execute(state, variable_pool2)
    assert result2["message"] == "second message"
    assert result2["language"] == "zh-CN"


@pytest.mark.asyncio
async def test_start_node_with_description():
    """测试带描述的变量"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    config = {
        "id": "start_test",
        "type": "start",
        "name": "开始节点",
        "config": {
            "variables": [
                {
                    "name": "api_endpoint",
                    "type": "string",
                    "required": True,
                    "description": "API 端点 URL，用于连接外部服务"
                }
            ]
        }
    }
    
    # 测试缺少必需变量时，错误信息包含描述
    with pytest.raises(ValueError) as exc_info:
        await StartNode(config, {}).execute(state, variable_pool)
    
    assert "api_endpoint" in str(exc_info.value)
    assert "API 端点 URL" in str(exc_info.value)
