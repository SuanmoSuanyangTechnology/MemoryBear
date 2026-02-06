# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/6
import pytest

from app.core.workflow.nodes import IfElseNode
from app.core.workflow.variable.base_variable import VariableType
from tests.workflow.nodes.base import simple_state, simple_vairable_pool


# 字符串比较测试配置
STRING_EQ_CONFIG = {
    "id": "ifelse_test",
    "type": "if-else",
    "name": "条件测试节点",
    "config": {
        "cases": [
            {
                "logical_operator": "and",
                "expressions": [
                    {
                        "left": "{{conv.test}}",
                        "operator": "eq",
                        "right": "hello",
                        "input_type": "constant"
                    }
                ]
            }
        ]
    }
}

STRING_CONTAINS_CONFIG = {
    "id": "ifelse_test",
    "type": "if-else",
    "name": "条件测试节点",
    "config": {
        "cases": [
            {
                "logical_operator": "and",
                "expressions": [
                    {
                        "left": "{{conv.test}}",
                        "operator": "contains",
                        "right": "world",
                        "input_type": "constant"
                    }
                ]
            }
        ]
    }
}

STRING_STARTSWITH_CONFIG = {
    "id": "ifelse_test",
    "type": "if-else",
    "name": "条件测试节点",
    "config": {
        "cases": [
            {
                "logical_operator": "and",
                "expressions": [
                    {
                        "left": "{{conv.test}}",
                        "operator": "startwith",
                        "right": "hello",
                        "input_type": "constant"
                    }
                ]
            }
        ]
    }
}

STRING_ENDSWITH_CONFIG = {
    "id": "ifelse_test",
    "type": "if-else",
    "name": "条件测试节点",
    "config": {
        "cases": [
            {
                "logical_operator": "and",
                "expressions": [
                    {
                        "left": "{{conv.test}}",
                        "operator": "endwith",
                        "right": "world",
                        "input_type": "constant"
                    }
                ]
            }
        ]
    }
}

STRING_EMPTY_CONFIG = {
    "id": "ifelse_test",
    "type": "if-else",
    "name": "条件测试节点",
    "config": {
        "cases": [
            {
                "logical_operator": "and",
                "expressions": [
                    {
                        "left": "{{conv.test}}",
                        "operator": "empty",
                        "right": "",
                        "input_type": "constant"
                    }
                ]
            }
        ]
    }
}

STRING_NOT_EMPTY_CONFIG = {
    "id": "ifelse_test",
    "type": "if-else",
    "name": "条件测试节点",
    "config": {
        "cases": [
            {
                "logical_operator": "and",
                "expressions": [
                    {
                        "left": "{{conv.test}}",
                        "operator": "not_empty",
                        "right": "",
                        "input_type": "constant"
                    }
                ]
            }
        ]
    }
}

# 数字比较测试配置
NUMBER_EQ_CONFIG = {
    "id": "ifelse_test",
    "type": "if-else",
    "name": "条件测试节点",
    "config": {
        "cases": [
            {
                "logical_operator": "and",
                "expressions": [
                    {
                        "left": "{{conv.test}}",
                        "operator": "eq",
                        "right": 10,
                        "input_type": "constant"
                    }
                ]
            }
        ]
    }
}

NUMBER_LT_CONFIG = {
    "id": "ifelse_test",
    "type": "if-else",
    "name": "条件测试节点",
    "config": {
        "cases": [
            {
                "logical_operator": "and",
                "expressions": [
                    {
                        "left": "{{conv.test}}",
                        "operator": "lt",
                        "right": 10,
                        "input_type": "constant"
                    }
                ]
            }
        ]
    }
}

NUMBER_GT_CONFIG = {
    "id": "ifelse_test",
    "type": "if-else",
    "name": "条件测试节点",
    "config": {
        "cases": [
            {
                "logical_operator": "and",
                "expressions": [
                    {
                        "left": "{{conv.test}}",
                        "operator": "gt",
                        "right": 10,
                        "input_type": "constant"
                    }
                ]
            }
        ]
    }
}

NUMBER_LE_CONFIG = {
    "id": "ifelse_test",
    "type": "if-else",
    "name": "条件测试节点",
    "config": {
        "cases": [
            {
                "logical_operator": "and",
                "expressions": [
                    {
                        "left": "{{conv.test}}",
                        "operator": "le",
                        "right": 10,
                        "input_type": "constant"
                    }
                ]
            }
        ]
    }
}

NUMBER_GE_CONFIG = {
    "id": "ifelse_test",
    "type": "if-else",
    "name": "条件测试节点",
    "config": {
        "cases": [
            {
                "logical_operator": "and",
                "expressions": [
                    {
                        "left": "{{conv.test}}",
                        "operator": "ge",
                        "right": 10,
                        "input_type": "constant"
                    }
                ]
            }
        ]
    }
}

# 布尔比较测试配置
BOOLEAN_EQ_CONFIG = {
    "id": "ifelse_test",
    "type": "if-else",
    "name": "条件测试节点",
    "config": {
        "cases": [
            {
                "logical_operator": "and",
                "expressions": [
                    {
                        "left": "{{conv.test}}",
                        "operator": "eq",
                        "right": True,
                        "input_type": "constant"
                    }
                ]
            }
        ]
    }
}

# 数组比较测试配置
ARRAY_CONTAINS_CONFIG = {
    "id": "ifelse_test",
    "type": "if-else",
    "name": "条件测试节点",
    "config": {
        "cases": [
            {
                "logical_operator": "and",
                "expressions": [
                    {
                        "left": "{{conv.test}}",
                        "operator": "contains",
                        "right": 2,
                        "input_type": "constant"
                    }
                ]
            }
        ]
    }
}

ARRAY_EMPTY_CONFIG = {
    "id": "ifelse_test",
    "type": "if-else",
    "name": "条件测试节点",
    "config": {
        "cases": [
            {
                "logical_operator": "and",
                "expressions": [
                    {
                        "left": "{{conv.test}}",
                        "operator": "empty",
                        "right": "",
                        "input_type": "constant"
                    }
                ]
            }
        ]
    }
}

# 对象比较测试配置
OBJECT_EMPTY_CONFIG = {
    "id": "ifelse_test",
    "type": "if-else",
    "name": "条件测试节点",
    "config": {
        "cases": [
            {
                "logical_operator": "and",
                "expressions": [
                    {
                        "left": "{{conv.test}}",
                        "operator": "empty",
                        "right": "",
                        "input_type": "constant"
                    }
                ]
            }
        ]
    }
}

# 多条件测试配置
MULTI_CONDITION_AND_CONFIG = {
    "id": "ifelse_test",
    "type": "if-else",
    "name": "条件测试节点",
    "config": {
        "cases": [
            {
                "logical_operator": "and",
                "expressions": [
                    {
                        "left": "{{conv.test1}}",
                        "operator": "eq",
                        "right": 10,
                        "input_type": "constant"
                    },
                    {
                        "left": "{{conv.test2}}",
                        "operator": "eq",
                        "right": "hello",
                        "input_type": "constant"
                    }
                ]
            }
        ]
    }
}

MULTI_CONDITION_OR_CONFIG = {
    "id": "ifelse_test",
    "type": "if-else",
    "name": "条件测试节点",
    "config": {
        "cases": [
            {
                "logical_operator": "or",
                "expressions": [
                    {
                        "left": "{{conv.test1}}",
                        "operator": "eq",
                        "right": 10,
                        "input_type": "constant"
                    },
                    {
                        "left": "{{conv.test2}}",
                        "operator": "eq",
                        "right": "hello",
                        "input_type": "constant"
                    }
                ]
            }
        ]
    }
}

# 多分支测试配置
MULTI_BRANCH_CONFIG = {
    "id": "ifelse_test",
    "type": "if-else",
    "name": "条件测试节点",
    "config": {
        "cases": [
            {
                "logical_operator": "and",
                "expressions": [
                    {
                        "left": "{{conv.test}}",
                        "operator": "eq",
                        "right": 1,
                        "input_type": "constant"
                    }
                ]
            },
            {
                "logical_operator": "and",
                "expressions": [
                    {
                        "left": "{{conv.test}}",
                        "operator": "eq",
                        "right": 2,
                        "input_type": "constant"
                    }
                ]
            },
            {
                "logical_operator": "and",
                "expressions": [
                    {
                        "left": "{{conv.test}}",
                        "operator": "eq",
                        "right": 3,
                        "input_type": "constant"
                    }
                ]
            }
        ]
    }
}

# 变量引用测试配置
VARIABLE_REFERENCE_CONFIG = {
    "id": "ifelse_test",
    "type": "if-else",
    "name": "条件测试节点",
    "config": {
        "cases": [
            {
                "logical_operator": "and",
                "expressions": [
                    {
                        "left": "{{conv.test1}}",
                        "operator": "eq",
                        "right": "{{conv.test2}}",
                        "input_type": "variable"
                    }
                ]
            }
        ]
    }
}


# ==================== 字符串比较测试 ====================
@pytest.mark.asyncio
async def test_ifelse_string_eq_true():
    """测试字符串相等条件为真"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", "hello", VariableType.STRING, mut=True)
    result = await IfElseNode(STRING_EQ_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE1"


@pytest.mark.asyncio
async def test_ifelse_string_eq_false():
    """测试字符串相等条件为假"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", "world", VariableType.STRING, mut=True)
    result = await IfElseNode(STRING_EQ_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE2"


@pytest.mark.asyncio
async def test_ifelse_string_contains_true():
    """测试字符串包含条件为真"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", "hello world", VariableType.STRING, mut=True)
    result = await IfElseNode(STRING_CONTAINS_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE1"


@pytest.mark.asyncio
async def test_ifelse_string_contains_false():
    """测试字符串包含条件为假"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", "hello", VariableType.STRING, mut=True)
    result = await IfElseNode(STRING_CONTAINS_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE2"


@pytest.mark.asyncio
async def test_ifelse_string_startswith_true():
    """测试字符串开头匹配条件为真"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", "hello world", VariableType.STRING, mut=True)
    result = await IfElseNode(STRING_STARTSWITH_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE1"


@pytest.mark.asyncio
async def test_ifelse_string_startswith_false():
    """测试字符串开头匹配条件为假"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", "world hello", VariableType.STRING, mut=True)
    result = await IfElseNode(STRING_STARTSWITH_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE2"


@pytest.mark.asyncio
async def test_ifelse_string_endswith_true():
    """测试字符串结尾匹配条件为真"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", "hello world", VariableType.STRING, mut=True)
    result = await IfElseNode(STRING_ENDSWITH_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE1"


@pytest.mark.asyncio
async def test_ifelse_string_endswith_false():
    """测试字符串结尾匹配条件为假"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", "world hello", VariableType.STRING, mut=True)
    result = await IfElseNode(STRING_ENDSWITH_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE2"


@pytest.mark.asyncio
async def test_ifelse_string_empty_true():
    """测试字符串为空条件为真"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", "", VariableType.STRING, mut=True)
    result = await IfElseNode(STRING_EMPTY_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE1"


@pytest.mark.asyncio
async def test_ifelse_string_empty_false():
    """测试字符串为空条件为假"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", "hello", VariableType.STRING, mut=True)
    result = await IfElseNode(STRING_EMPTY_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE2"


@pytest.mark.asyncio
async def test_ifelse_string_not_empty_true():
    """测试字符串非空条件为真"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", "hello", VariableType.STRING, mut=True)
    result = await IfElseNode(STRING_NOT_EMPTY_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE1"


@pytest.mark.asyncio
async def test_ifelse_string_not_empty_false():
    """测试字符串非空条件为假"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", "", VariableType.STRING, mut=True)
    result = await IfElseNode(STRING_NOT_EMPTY_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE2"


# ==================== 数字比较测试 ====================
@pytest.mark.asyncio
async def test_ifelse_number_eq_true():
    """测试数字相等条件为真"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 10, VariableType.NUMBER, mut=True)
    result = await IfElseNode(NUMBER_EQ_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE1"


@pytest.mark.asyncio
async def test_ifelse_number_eq_false():
    """测试数字相等条件为假"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 5, VariableType.NUMBER, mut=True)
    result = await IfElseNode(NUMBER_EQ_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE2"


@pytest.mark.asyncio
async def test_ifelse_number_lt_true():
    """测试数字小于条件为真"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 5, VariableType.NUMBER, mut=True)
    result = await IfElseNode(NUMBER_LT_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE1"


@pytest.mark.asyncio
async def test_ifelse_number_lt_false():
    """测试数字小于条件为假"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 15, VariableType.NUMBER, mut=True)
    result = await IfElseNode(NUMBER_LT_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE2"


@pytest.mark.asyncio
async def test_ifelse_number_gt_true():
    """测试数字大于条件为真"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 15, VariableType.NUMBER, mut=True)
    result = await IfElseNode(NUMBER_GT_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE1"


@pytest.mark.asyncio
async def test_ifelse_number_gt_false():
    """测试数字大于条件为假"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 5, VariableType.NUMBER, mut=True)
    result = await IfElseNode(NUMBER_GT_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE2"


@pytest.mark.asyncio
async def test_ifelse_number_le_true():
    """测试数字小于等于条件为真"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 10, VariableType.NUMBER, mut=True)
    result = await IfElseNode(NUMBER_LE_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE1"


@pytest.mark.asyncio
async def test_ifelse_number_le_false():
    """测试数字小于等于条件为假"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 15, VariableType.NUMBER, mut=True)
    result = await IfElseNode(NUMBER_LE_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE2"


@pytest.mark.asyncio
async def test_ifelse_number_ge_true():
    """测试数字大于等于条件为真"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 10, VariableType.NUMBER, mut=True)
    result = await IfElseNode(NUMBER_GE_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE1"


@pytest.mark.asyncio
async def test_ifelse_number_ge_false():
    """测试数字大于等于条件为假"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 5, VariableType.NUMBER, mut=True)
    result = await IfElseNode(NUMBER_GE_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE2"


# ==================== 布尔比较测试 ====================
@pytest.mark.asyncio
async def test_ifelse_boolean_eq_true():
    """测试布尔值相等条件为真"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", True, VariableType.BOOLEAN, mut=True)
    result = await IfElseNode(BOOLEAN_EQ_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE1"


@pytest.mark.asyncio
async def test_ifelse_boolean_eq_false():
    """测试布尔值相等条件为假"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", False, VariableType.BOOLEAN, mut=True)
    result = await IfElseNode(BOOLEAN_EQ_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE2"


# ==================== 数组比较测试 ====================
@pytest.mark.asyncio
async def test_ifelse_array_contains_true():
    """测试数组包含条件为真"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", [1, 2, 3], VariableType.ARRAY_NUMBER, mut=True)
    result = await IfElseNode(ARRAY_CONTAINS_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE1"


@pytest.mark.asyncio
async def test_ifelse_array_contains_false():
    """测试数组包含条件为假"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", [1, 3, 4], VariableType.ARRAY_NUMBER, mut=True)
    result = await IfElseNode(ARRAY_CONTAINS_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE2"


@pytest.mark.asyncio
async def test_ifelse_array_empty_true():
    """测试数组为空条件为真"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", [], VariableType.ARRAY_NUMBER, mut=True)
    result = await IfElseNode(ARRAY_EMPTY_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE1"


@pytest.mark.asyncio
async def test_ifelse_array_empty_false():
    """测试数组为空条件为假"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", [1, 2], VariableType.ARRAY_NUMBER, mut=True)
    result = await IfElseNode(ARRAY_EMPTY_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE2"


# ==================== 对象比较测试 ====================
@pytest.mark.asyncio
async def test_ifelse_object_empty_true():
    """测试对象为空条件为真"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", {}, VariableType.OBJECT, mut=True)
    result = await IfElseNode(OBJECT_EMPTY_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE1"


@pytest.mark.asyncio
async def test_ifelse_object_empty_false():
    """测试对象为空条件为假"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", {"key": "value"}, VariableType.OBJECT, mut=True)
    result = await IfElseNode(OBJECT_EMPTY_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE2"


# ==================== 多条件测试 ====================
@pytest.mark.asyncio
async def test_ifelse_multi_condition_and_all_true():
    """测试多条件AND逻辑，所有条件为真"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test1", 10, VariableType.NUMBER, mut=True)
    await variable_pool.new("conv", "test2", "hello", VariableType.STRING, mut=True)
    result = await IfElseNode(MULTI_CONDITION_AND_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE1"


@pytest.mark.asyncio
async def test_ifelse_multi_condition_and_one_false():
    """测试多条件AND逻辑，一个条件为假"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test1", 10, VariableType.NUMBER, mut=True)
    await variable_pool.new("conv", "test2", "world", VariableType.STRING, mut=True)
    result = await IfElseNode(MULTI_CONDITION_AND_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE2"


@pytest.mark.asyncio
async def test_ifelse_multi_condition_and_all_false():
    """测试多条件AND逻辑，所有条件为假"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test1", 5, VariableType.NUMBER, mut=True)
    await variable_pool.new("conv", "test2", "world", VariableType.STRING, mut=True)
    result = await IfElseNode(MULTI_CONDITION_AND_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE2"


@pytest.mark.asyncio
async def test_ifelse_multi_condition_or_all_true():
    """测试多条件OR逻辑，所有条件为真"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test1", 10, VariableType.NUMBER, mut=True)
    await variable_pool.new("conv", "test2", "hello", VariableType.STRING, mut=True)
    result = await IfElseNode(MULTI_CONDITION_OR_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE1"


@pytest.mark.asyncio
async def test_ifelse_multi_condition_or_one_true():
    """测试多条件OR逻辑，一个条件为真"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test1", 10, VariableType.NUMBER, mut=True)
    await variable_pool.new("conv", "test2", "world", VariableType.STRING, mut=True)
    result = await IfElseNode(MULTI_CONDITION_OR_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE1"


@pytest.mark.asyncio
async def test_ifelse_multi_condition_or_all_false():
    """测试多条件OR逻辑，所有条件为假"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test1", 5, VariableType.NUMBER, mut=True)
    await variable_pool.new("conv", "test2", "world", VariableType.STRING, mut=True)
    result = await IfElseNode(MULTI_CONDITION_OR_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE2"


# ==================== 多分支测试 ====================
@pytest.mark.asyncio
async def test_ifelse_multi_branch_first():
    """测试多分支，匹配第一个分支"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 1, VariableType.NUMBER, mut=True)
    result = await IfElseNode(MULTI_BRANCH_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE1"


@pytest.mark.asyncio
async def test_ifelse_multi_branch_second():
    """测试多分支，匹配第二个分支"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 2, VariableType.NUMBER, mut=True)
    result = await IfElseNode(MULTI_BRANCH_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE2"


@pytest.mark.asyncio
async def test_ifelse_multi_branch_third():
    """测试多分支，匹配第三个分支"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 3, VariableType.NUMBER, mut=True)
    result = await IfElseNode(MULTI_BRANCH_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE3"


@pytest.mark.asyncio
async def test_ifelse_multi_branch_default():
    """测试多分支，匹配默认分支"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 4, VariableType.NUMBER, mut=True)
    result = await IfElseNode(MULTI_BRANCH_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE4"


# ==================== 变量引用测试 ====================
@pytest.mark.asyncio
async def test_ifelse_variable_reference_true():
    """测试变量引用条件为真"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test1", 10, VariableType.NUMBER, mut=True)
    await variable_pool.new("conv", "test2", 10, VariableType.NUMBER, mut=True)
    result = await IfElseNode(VARIABLE_REFERENCE_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE1"


@pytest.mark.asyncio
async def test_ifelse_variable_reference_false():
    """测试变量引用条件为假"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test1", 10, VariableType.NUMBER, mut=True)
    await variable_pool.new("conv", "test2", 20, VariableType.NUMBER, mut=True)
    result = await IfElseNode(VARIABLE_REFERENCE_CONFIG, {}).execute(state, variable_pool)
    assert result == "CASE2"


# ==================== 边界情况测试 ====================
@pytest.mark.asyncio
async def test_ifelse_none_variable():
    """测试变量不存在的情况"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    config = {
        "id": "ifelse_test",
        "type": "if-else",
        "name": "条件测试节点",
        "config": {
            "cases": [
                {
                    "logical_operator": "and",
                    "expressions": [
                        {
                            "left": "{{conv.nonexistent}}",
                            "operator": "eq",
                            "right": 10,
                            "input_type": "constant"
                        }
                    ]
                }
            ]
        }
    }
    result = await IfElseNode(config, {}).execute(state, variable_pool)
    assert result == "CASE2"


@pytest.mark.asyncio
async def test_ifelse_float_comparison():
    """测试浮点数比较"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 10.5, VariableType.NUMBER, mut=True)
    config = {
        "id": "ifelse_test",
        "type": "if-else",
        "name": "条件测试节点",
        "config": {
            "cases": [
                {
                    "logical_operator": "and",
                    "expressions": [
                        {
                            "left": "{{conv.test}}",
                            "operator": "gt",
                            "right": 10.0,
                            "input_type": "constant"
                        }
                    ]
                }
            ]
        }
    }
    result = await IfElseNode(config, {}).execute(state, variable_pool)
    assert result == "CASE1"


@pytest.mark.asyncio
async def test_ifelse_string_ne():
    """测试字符串不等于"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", "hello", VariableType.STRING, mut=True)
    config = {
        "id": "ifelse_test",
        "type": "if-else",
        "name": "条件测试节点",
        "config": {
            "cases": [
                {
                    "logical_operator": "and",
                    "expressions": [
                        {
                            "left": "{{conv.test}}",
                            "operator": "ne",
                            "right": "world",
                            "input_type": "constant"
                        }
                    ]
                }
            ]
        }
    }
    result = await IfElseNode(config, {}).execute(state, variable_pool)
    assert result == "CASE1"


@pytest.mark.asyncio
async def test_ifelse_number_ne():
    """测试数字不等于"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 10, VariableType.NUMBER, mut=True)
    config = {
        "id": "ifelse_test",
        "type": "if-else",
        "name": "条件测试节点",
        "config": {
            "cases": [
                {
                    "logical_operator": "and",
                    "expressions": [
                        {
                            "left": "{{conv.test}}",
                            "operator": "ne",
                            "right": 5,
                            "input_type": "constant"
                        }
                    ]
                }
            ]
        }
    }
    result = await IfElseNode(config, {}).execute(state, variable_pool)
    assert result == "CASE1"


@pytest.mark.asyncio
async def test_ifelse_array_not_contains():
    """测试数组不包含"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", [1, 2, 3], VariableType.ARRAY_NUMBER, mut=True)
    config = {
        "id": "ifelse_test",
        "type": "if-else",
        "name": "条件测试节点",
        "config": {
            "cases": [
                {
                    "logical_operator": "and",
                    "expressions": [
                        {
                            "left": "{{conv.test}}",
                            "operator": "not_contains",
                            "right": 5,
                            "input_type": "constant"
                        }
                    ]
                }
            ]
        }
    }
    result = await IfElseNode(config, {}).execute(state, variable_pool)
    assert result == "CASE1"


@pytest.mark.asyncio
async def test_ifelse_string_not_contains():
    """测试字符串不包含"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", "hello", VariableType.STRING, mut=True)
    config = {
        "id": "ifelse_test",
        "type": "if-else",
        "name": "条件测试节点",
        "config": {
            "cases": [
                {
                    "logical_operator": "and",
                    "expressions": [
                        {
                            "left": "{{conv.test}}",
                            "operator": "not_contains",
                            "right": "world",
                            "input_type": "constant"
                        }
                    ]
                }
            ]
        }
    }
    result = await IfElseNode(config, {}).execute(state, variable_pool)
    assert result == "CASE1"


@pytest.mark.asyncio
async def test_ifelse_object_not_empty():
    """测试对象非空"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", {"key": "value"}, VariableType.OBJECT, mut=True)
    config = {
        "id": "ifelse_test",
        "type": "if-else",
        "name": "条件测试节点",
        "config": {
            "cases": [
                {
                    "logical_operator": "and",
                    "expressions": [
                        {
                            "left": "{{conv.test}}",
                            "operator": "not_empty",
                            "right": "",
                            "input_type": "constant"
                        }
                    ]
                }
            ]
        }
    }
    result = await IfElseNode(config, {}).execute(state, variable_pool)
    assert result == "CASE1"


@pytest.mark.asyncio
async def test_ifelse_array_not_empty():
    """测试数组非空"""
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", [1, 2], VariableType.ARRAY_NUMBER, mut=True)
    config = {
        "id": "ifelse_test",
        "type": "if-else",
        "name": "条件测试节点",
        "config": {
            "cases": [
                {
                    "logical_operator": "and",
                    "expressions": [
                        {
                            "left": "{{conv.test}}",
                            "operator": "not_empty",
                            "right": "",
                            "input_type": "constant"
                        }
                    ]
                }
            ]
        }
    }
    result = await IfElseNode(config, {}).execute(state, variable_pool)
    assert result == "CASE1"
