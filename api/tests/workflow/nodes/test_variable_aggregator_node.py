# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/6
import pytest

from app.core.workflow.nodes import VariableAggregatorNode
from app.core.workflow.variable.base_variable import VariableType
from tests.workflow.nodes.base import simple_state, simple_vairable_pool


# 非分组模式配置 - 返回第一个非空变量
NON_GROUP_CONFIG = {
    "id": "aggregator_test",
    "type": "var-aggregator",
    "name": "变量聚合测试节点",
    "config": {
        "group": False,
        "group_variables": [
            "{{conv.var1}}",
            "{{conv.var2}}",
            "{{conv.var3}}"
        ]
    }
}

# 非分组模式配置 - 带类型定义
NON_GROUP_WITH_TYPE_CONFIG = {
    "id": "aggregator_test",
    "type": "var-aggregator",
    "name": "变量聚合测试节点",
    "config": {
        "group": False,
        "group_variables": [
            "{{conv.var1}}",
            "{{conv.var2}}"
        ],
        "group_type": {
            "output": "string"
        }
    }
}

# 分组模式配置
GROUP_CONFIG = {
    "id": "aggregator_test",
    "type": "var-aggregator",
    "name": "变量聚合测试节点",
    "config": {
        "group": True,
        "group_variables": {
            "user_message": [
                "{{conv.msg1}}",
                "{{conv.msg2}}"
            ],
            "user_name": [
                "{{conv.name1}}",
                "{{conv.name2}}"
            ]
        }
    }
}

# 分组模式配置 - 带类型定义
GROUP_WITH_TYPE_CONFIG = {
    "id": "aggregator_test",
    "type": "var-aggregator",
    "name": "变量聚合测试节点",
    "config": {
        "group": True,
        "group_variables": {
            "count": [
                "{{conv.count1}}",
                "{{conv.count2}}"
            ],
            "enabled": [
                "{{conv.flag1}}",
                "{{conv.flag2}}"
            ]
        },
        "group_type": {
            "count": "number",
            "enabled": "boolean"
        }
    }
}


# ==================== 非分组模式测试 ====================
@pytest.mark.asyncio
async def test_non_group_first_variable():
    """测试非分组模式返回第一个非空变量"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    # 设置变量
    await variable_pool.new("conv", "var1", "first_value", VariableType.STRING, mut=True)
    await variable_pool.new("conv", "var2", "second_value", VariableType.STRING, mut=True)
    await variable_pool.new("conv", "var3", "third_value", VariableType.STRING, mut=True)
    
    result = await VariableAggregatorNode(NON_GROUP_CONFIG, {}).execute(state, variable_pool)
    
    assert result == "first_value"


@pytest.mark.asyncio
async def test_non_group_skip_none():
    """测试非分组模式跳过 None 值"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    # 第一个变量不存在，第二个存在
    await variable_pool.new("conv", "var2", "second_value", VariableType.STRING, mut=True)
    await variable_pool.new("conv", "var3", "third_value", VariableType.STRING, mut=True)
    
    result = await VariableAggregatorNode(NON_GROUP_CONFIG, {}).execute(state, variable_pool)
    
    assert result == "second_value"


@pytest.mark.asyncio
async def test_non_group_all_none():
    """测试非分组模式所有变量都不存在"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    # 不创建任何变量
    result = await VariableAggregatorNode(NON_GROUP_CONFIG, {}).execute(state, variable_pool)
    
    assert result == ""


@pytest.mark.asyncio
async def test_non_group_with_type_all_none():
    """测试非分组模式带类型定义，所有变量都不存在"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    # 不创建任何变量
    result = await VariableAggregatorNode(NON_GROUP_WITH_TYPE_CONFIG, {}).execute(state, variable_pool)
    
    # 应该返回类型的默认值
    assert result == ""


@pytest.mark.asyncio
async def test_non_group_different_types():
    """测试非分组模式不同类型的变量"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    config = {
        "id": "aggregator_test",
        "type": "var-aggregator",
        "name": "变量聚合测试节点",
        "config": {
            "group": False,
            "group_variables": [
                "{{conv.num}}",
                "{{conv.str}}",
                "{{conv.bool}}"
            ]
        }
    }
    
    # 设置不同类型的变量
    await variable_pool.new("conv", "num", 123, VariableType.NUMBER, mut=True)
    await variable_pool.new("conv", "str", "text", VariableType.STRING, mut=True)
    await variable_pool.new("conv", "bool", True, VariableType.BOOLEAN, mut=True)
    
    result = await VariableAggregatorNode(config, {}).execute(state, variable_pool)
    
    assert result == 123


@pytest.mark.asyncio
async def test_non_group_zero_and_false():
    """测试非分组模式零值和 False 值（不应被视为 None）"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    config = {
        "id": "aggregator_test",
        "type": "var-aggregator",
        "name": "变量聚合测试节点",
        "config": {
            "group": False,
            "group_variables": [
                "{{conv.zero}}",
                "{{conv.text}}"
            ]
        }
    }
    
    # 设置零值
    await variable_pool.new("conv", "zero", 0, VariableType.NUMBER, mut=True)
    await variable_pool.new("conv", "text", "fallback", VariableType.STRING, mut=True)
    
    result = await VariableAggregatorNode(config, {}).execute(state, variable_pool)
    
    # 0 不应被视为 None，应该返回 0
    assert result == 0


@pytest.mark.asyncio
async def test_non_group_false_value():
    """测试非分组模式 False 值"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    config = {
        "id": "aggregator_test",
        "type": "var-aggregator",
        "name": "变量聚合测试节点",
        "config": {
            "group": False,
            "group_variables": [
                "{{conv.flag}}",
                "{{conv.text}}"
            ]
        }
    }
    
    # 设置 False 值
    await variable_pool.new("conv", "flag", False, VariableType.BOOLEAN, mut=True)
    await variable_pool.new("conv", "text", "fallback", VariableType.STRING, mut=True)
    
    result = await VariableAggregatorNode(config, {}).execute(state, variable_pool)
    
    # False 不应被视为 None，应该返回 False
    assert result is False


# ==================== 分组模式测试 ====================
@pytest.mark.asyncio
async def test_group_mode_all_groups():
    """测试分组模式所有分组都有值"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    # 设置变量
    await variable_pool.new("conv", "msg1", "Hello", VariableType.STRING, mut=True)
    await variable_pool.new("conv", "name1", "Alice", VariableType.STRING, mut=True)
    
    result = await VariableAggregatorNode(GROUP_CONFIG, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert result["user_message"] == "Hello"
    assert result["user_name"] == "Alice"


@pytest.mark.asyncio
async def test_group_mode_fallback():
    """测试分组模式使用备用变量"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    # 第一个变量不存在，使用第二个
    await variable_pool.new("conv", "msg2", "Fallback message", VariableType.STRING, mut=True)
    await variable_pool.new("conv", "name2", "Bob", VariableType.STRING, mut=True)
    
    result = await VariableAggregatorNode(GROUP_CONFIG, {}).execute(state, variable_pool)
    
    assert result["user_message"] == "Fallback message"
    assert result["user_name"] == "Bob"


@pytest.mark.asyncio
async def test_group_mode_partial_none():
    """测试分组模式部分分组没有值"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    # 只设置一个分组的变量
    await variable_pool.new("conv", "msg1", "Hello", VariableType.STRING, mut=True)
    
    result = await VariableAggregatorNode(GROUP_CONFIG, {}).execute(state, variable_pool)
    
    assert result["user_message"] == "Hello"
    assert result["user_name"] == ""  # 没有值的分组返回空字符串


@pytest.mark.asyncio
async def test_group_mode_all_none():
    """测试分组模式所有分组都没有值"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    # 不创建任何变量
    result = await VariableAggregatorNode(GROUP_CONFIG, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert result["user_message"] == ""
    assert result["user_name"] == ""


@pytest.mark.asyncio
async def test_group_mode_with_type():
    """测试分组模式带类型定义"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    # 设置变量
    await variable_pool.new("conv", "count1", 100, VariableType.NUMBER, mut=True)
    await variable_pool.new("conv", "flag1", True, VariableType.BOOLEAN, mut=True)
    
    result = await VariableAggregatorNode(GROUP_WITH_TYPE_CONFIG, {}).execute(state, variable_pool)
    
    assert result["count"] == 100
    assert result["enabled"] is True


@pytest.mark.asyncio
async def test_group_mode_with_type_defaults():
    """测试分组模式带类型定义，使用默认值"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    # 不创建任何变量
    result = await VariableAggregatorNode(GROUP_WITH_TYPE_CONFIG, {}).execute(state, variable_pool)
    
    # 应该返回类型的默认值
    assert result["count"] == 0  # number 的默认值
    assert result["enabled"] is False  # boolean 的默认值


@pytest.mark.asyncio
async def test_group_mode_mixed_values():
    """测试分组模式混合有值和无值的情况"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    # 只设置 count2
    await variable_pool.new("conv", "count2", 200, VariableType.NUMBER, mut=True)
    
    result = await VariableAggregatorNode(GROUP_WITH_TYPE_CONFIG, {}).execute(state, variable_pool)
    
    assert result["count"] == 200  # 使用第二个变量
    assert result["enabled"] is False  # 没有值，使用默认值


@pytest.mark.asyncio
async def test_group_mode_multiple_groups():
    """测试分组模式多个分组"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    config = {
        "id": "aggregator_test",
        "type": "var-aggregator",
        "name": "变量聚合测试节点",
        "config": {
            "group": True,
            "group_variables": {
                "group1": ["{{conv.g1_v1}}", "{{conv.g1_v2}}"],
                "group2": ["{{conv.g2_v1}}", "{{conv.g2_v2}}"],
                "group3": ["{{conv.g3_v1}}", "{{conv.g3_v2}}"]
            }
        }
    }
    
    # 设置不同分组的变量
    await variable_pool.new("conv", "g1_v1", "value1", VariableType.STRING, mut=True)
    await variable_pool.new("conv", "g2_v2", "value2", VariableType.STRING, mut=True)
    await variable_pool.new("conv", "g3_v1", "value3", VariableType.STRING, mut=True)
    
    result = await VariableAggregatorNode(config, {}).execute(state, variable_pool)
    
    assert result["group1"] == "value1"
    assert result["group2"] == "value2"
    assert result["group3"] == "value3"


# ==================== 复杂场景测试 ====================
@pytest.mark.asyncio
async def test_aggregator_with_array():
    """测试聚合数组变量"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    config = {
        "id": "aggregator_test",
        "type": "var-aggregator",
        "name": "变量聚合测试节点",
        "config": {
            "group": False,
            "group_variables": [
                "{{conv.arr1}}",
                "{{conv.arr2}}"
            ]
        }
    }
    
    # 设置数组变量
    await variable_pool.new("conv", "arr1", [1, 2, 3], VariableType.ARRAY_NUMBER, mut=True)
    await variable_pool.new("conv", "arr2", [4, 5, 6], VariableType.ARRAY_NUMBER, mut=True)
    
    result = await VariableAggregatorNode(config, {}).execute(state, variable_pool)
    
    assert result == [1, 2, 3]


@pytest.mark.asyncio
async def test_aggregator_with_object():
    """测试聚合对象变量"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    config = {
        "id": "aggregator_test",
        "type": "var-aggregator",
        "name": "变量聚合测试节点",
        "config": {
            "group": False,
            "group_variables": [
                "{{conv.obj1}}",
                "{{conv.obj2}}"
            ]
        }
    }
    
    # 设置对象变量
    await variable_pool.new("conv", "obj1", {"key": "value1"}, VariableType.OBJECT, mut=True)
    await variable_pool.new("conv", "obj2", {"key": "value2"}, VariableType.OBJECT, mut=True)
    
    result = await VariableAggregatorNode(config, {}).execute(state, variable_pool)
    
    assert result == {"key": "value1"}


@pytest.mark.asyncio
async def test_aggregator_empty_string():
    """测试空字符串不被视为 None"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    config = {
        "id": "aggregator_test",
        "type": "var-aggregator",
        "name": "变量聚合测试节点",
        "config": {
            "group": False,
            "group_variables": [
                "{{conv.empty}}",
                "{{conv.text}}"
            ]
        }
    }
    
    # 设置空字符串
    await variable_pool.new("conv", "empty", "", VariableType.STRING, mut=True)
    await variable_pool.new("conv", "text", "fallback", VariableType.STRING, mut=True)
    
    result = await VariableAggregatorNode(config, {}).execute(state, variable_pool)
    
    # 空字符串不应被视为 None，应该返回空字符串
    assert result == ""


@pytest.mark.asyncio
async def test_aggregator_empty_array():
    """测试空数组不被视为 None"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    config = {
        "id": "aggregator_test",
        "type": "var-aggregator",
        "name": "变量聚合测试节点",
        "config": {
            "group": False,
            "group_variables": [
                "{{conv.empty_arr}}",
                "{{conv.arr}}"
            ]
        }
    }
    
    # 设置空数组
    await variable_pool.new("conv", "empty_arr", [], VariableType.ARRAY_NUMBER, mut=True)
    await variable_pool.new("conv", "arr", [1, 2], VariableType.ARRAY_NUMBER, mut=True)
    
    result = await VariableAggregatorNode(config, {}).execute(state, variable_pool)
    
    # 空数组不应被视为 None，应该返回空数组
    assert result == []


@pytest.mark.asyncio
async def test_aggregator_empty_object():
    """测试空对象不被视为 None"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    config = {
        "id": "aggregator_test",
        "type": "var-aggregator",
        "name": "变量聚合测试节点",
        "config": {
            "group": False,
            "group_variables": [
                "{{conv.empty_obj}}",
                "{{conv.obj}}"
            ]
        }
    }
    
    # 设置空对象
    await variable_pool.new("conv", "empty_obj", {}, VariableType.OBJECT, mut=True)
    await variable_pool.new("conv", "obj", {"key": "value"}, VariableType.OBJECT, mut=True)
    
    result = await VariableAggregatorNode(config, {}).execute(state, variable_pool)
    
    # 空对象不应被视为 None，应该返回空对象
    assert result == {}


@pytest.mark.asyncio
async def test_group_mode_with_different_types():
    """测试分组模式不同类型的变量"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    config = {
        "id": "aggregator_test",
        "type": "var-aggregator",
        "name": "变量聚合测试节点",
        "config": {
            "group": True,
            "group_variables": {
                "text": ["{{conv.str1}}", "{{conv.str2}}"],
                "number": ["{{conv.num1}}", "{{conv.num2}}"],
                "array": ["{{conv.arr1}}", "{{conv.arr2}}"],
                "object": ["{{conv.obj1}}", "{{conv.obj2}}"]
            },
            "group_type": {
                "text": "string",
                "number": "number",
                "array": "array[number]",
                "object": "object"
            }
        }
    }
    
    # 设置不同类型的变量
    await variable_pool.new("conv", "str1", "hello", VariableType.STRING, mut=True)
    await variable_pool.new("conv", "num1", 42, VariableType.NUMBER, mut=True)
    await variable_pool.new("conv", "arr1", [1, 2, 3], VariableType.ARRAY_NUMBER, mut=True)
    await variable_pool.new("conv", "obj1", {"key": "value"}, VariableType.OBJECT, mut=True)
    
    result = await VariableAggregatorNode(config, {}).execute(state, variable_pool)
    
    assert result["text"] == "hello"
    assert result["number"] == 42
    assert result["array"] == [1, 2, 3]
    assert result["object"] == {"key": "value"}


@pytest.mark.asyncio
async def test_aggregator_output_types():
    """测试输出类型定义"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    node = VariableAggregatorNode(GROUP_WITH_TYPE_CONFIG, {})
    
    output_types = node._output_types()
    
    assert output_types["count"] == VariableType.NUMBER
    assert output_types["enabled"] == VariableType.BOOLEAN


@pytest.mark.asyncio
async def test_non_group_single_variable():
    """测试非分组模式只有一个变量"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    config = {
        "id": "aggregator_test",
        "type": "var-aggregator",
        "name": "变量聚合测试节点",
        "config": {
            "group": False,
            "group_variables": [
                "{{conv.only_var}}"
            ]
        }
    }
    
    await variable_pool.new("conv", "only_var", "single_value", VariableType.STRING, mut=True)
    
    result = await VariableAggregatorNode(config, {}).execute(state, variable_pool)
    
    assert result == "single_value"


@pytest.mark.asyncio
async def test_group_mode_single_group():
    """测试分组模式只有一个分组"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    
    config = {
        "id": "aggregator_test",
        "type": "var-aggregator",
        "name": "变量聚合测试节点",
        "config": {
            "group": True,
            "group_variables": {
                "only_group": ["{{conv.var1}}", "{{conv.var2}}"]
            }
        }
    }
    
    await variable_pool.new("conv", "var1", "value", VariableType.STRING, mut=True)
    
    result = await VariableAggregatorNode(config, {}).execute(state, variable_pool)
    
    assert isinstance(result, dict)
    assert result["only_group"] == "value"
