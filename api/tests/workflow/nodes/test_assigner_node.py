# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/5 18:54
import pytest

from app.core.workflow.nodes import AssignerNode
from app.core.workflow.variable.base_variable import VariableType
from tests.workflow.nodes.base import simple_state, simple_vairable_pool


@pytest.mark.asyncio
async def test_assigner_number_add():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 1, VariableType.NUMBER, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "add",
                    "value": 3
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") == 4


@pytest.mark.asyncio
async def test_assigner_number_subtract():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 1, VariableType.NUMBER, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "subtract",
                    "value": 3
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") == -2


@pytest.mark.asyncio
async def test_assigner_number_multiply():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 2, VariableType.NUMBER, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "multiply",
                    "value": 3
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") == 6


@pytest.mark.asyncio
async def test_assigner_number_divide():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 6, VariableType.NUMBER, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "divide",
                    "value": 2
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") == 3


@pytest.mark.asyncio
async def test_assigner_number_assign():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 1, VariableType.NUMBER, mut=True)
    await variable_pool.new("conv", "test1", 4, VariableType.NUMBER, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "assign",
                    "value": "{{conv.test1}}"
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") == 4


@pytest.mark.asyncio
async def test_assigner_number_cover():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 1, VariableType.NUMBER, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "cover",
                    "value": 4
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") == 4


@pytest.mark.asyncio
async def test_assigner_number_clear():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 1, VariableType.NUMBER, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "clear",
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") == 0


@pytest.mark.asyncio
async def test_assigner_number_append():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 1, VariableType.NUMBER, mut=True)
    with pytest.raises(AttributeError) as exc_info:
        config = {
            "id": "assigner_test",
            "type": "assigner",
            "name": "赋值测试节点",
            "config": {
                "assignments": [
                    {
                        "variable_selector": "{{conv.test}}",
                        "operation": "append",
                        "value": 3
                    }
                ]
            }
        }
        await AssignerNode(config, {}).execute(state, variable_pool)
    assert "'NumberOperator' object has no attribute 'append'" in str(exc_info.value)


@pytest.mark.asyncio
async def test_assigner_number_remove_last():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 1, VariableType.NUMBER, mut=True)
    with pytest.raises(AttributeError) as exc_info:
        config = {
            "id": "assigner_test",
            "type": "assigner",
            "name": "赋值测试节点",
            "config": {
                "assignments": [
                    {
                        "variable_selector": "{{conv.test}}",
                        "operation": "remove_last"
                    }
                ]
            }
        }
        await AssignerNode(config, {}).execute(state, variable_pool)
    assert "'NumberOperator' object has no attribute 'remove_last'" in str(exc_info.value)


@pytest.mark.asyncio
async def test_assigner_number_remove_first():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 1, VariableType.NUMBER, mut=True)
    with pytest.raises(AttributeError) as exc_info:
        config = {
            "id": "assigner_test",
            "type": "assigner",
            "name": "赋值测试节点",
            "config": {
                "assignments": [
                    {
                        "variable_selector": "{{conv.test}}",
                        "operation": "remove_first"
                    }
                ]
            }
        }
        await AssignerNode(config, {}).execute(state, variable_pool)
    assert "'NumberOperator' object has no attribute 'remove_first'" in str(exc_info.value)


@pytest.mark.asyncio
async def test_assigner_array_append():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", [1, 2], VariableType.ARRAY_NUMBER, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "append",
                    "value": 3
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") == [1, 2, 3]


@pytest.mark.asyncio
async def test_assigner_array_remove_last():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", [1, 2], VariableType.ARRAY_NUMBER, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "remove_last"
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") == [1]


@pytest.mark.asyncio
async def test_assigner_array_remove_first():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", [1, 2], VariableType.ARRAY_NUMBER, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "remove_first"
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") == [2]


# String tests
@pytest.mark.asyncio
async def test_assigner_string_assign():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", "hello", VariableType.STRING, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "assign",
                    "value": "world"
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") == "world"


@pytest.mark.asyncio
async def test_assigner_string_cover():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", "hello", VariableType.STRING, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "cover",
                    "value": "world"
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") == "world"


@pytest.mark.asyncio
async def test_assigner_string_clear():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", "hello", VariableType.STRING, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "clear"
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") == ""


@pytest.mark.asyncio
async def test_assigner_string_invalid_operation():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", "hello", VariableType.STRING, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "add",
                    "value": "world"
                }
            ]
        }
    }
    with pytest.raises(AttributeError) as exc_info:
        await AssignerNode(config, {}).execute(state, variable_pool)
    assert "'StringOperator' object has no attribute 'add'" in str(exc_info.value)


# Boolean tests
@pytest.mark.asyncio
async def test_assigner_boolean_assign():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", True, VariableType.BOOLEAN, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "assign",
                    "value": False
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") is False


@pytest.mark.asyncio
async def test_assigner_boolean_cover():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", False, VariableType.BOOLEAN, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "cover",
                    "value": True
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") is True


@pytest.mark.asyncio
async def test_assigner_boolean_clear():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", True, VariableType.BOOLEAN, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "clear"
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") is False


# Object tests
@pytest.mark.asyncio
async def test_assigner_object_assign():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", {"key": "value"}, VariableType.OBJECT, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "assign",
                    "value": {"new_key": "new_value"}
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") == {"new_key": "new_value"}


@pytest.mark.asyncio
async def test_assigner_object_cover():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", {"key": "value"}, VariableType.OBJECT, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "cover",
                    "value": {"new_key": "new_value"}
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") == {"new_key": "new_value"}


@pytest.mark.asyncio
async def test_assigner_object_clear():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", {"key": "value"}, VariableType.OBJECT, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "clear"
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") == {}


# Array string tests
@pytest.mark.asyncio
async def test_assigner_array_string_append():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", ["a", "b"], VariableType.ARRAY_STRING, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "append",
                    "value": "c"
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_assigner_array_string_clear():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", ["a", "b"], VariableType.ARRAY_STRING, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "clear"
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") == []


@pytest.mark.asyncio
async def test_assigner_array_object_append():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", [{"id": 1}], VariableType.ARRAY_OBJECT, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "append",
                    "value": {"id": 2}
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") == [{"id": 1}, {"id": 2}]


@pytest.mark.asyncio
async def test_assigner_array_assign():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", [1, 2], VariableType.ARRAY_NUMBER, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "assign",
                    "value": [3, 4, 5]
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") == [3, 4, 5]


@pytest.mark.asyncio
async def test_assigner_array_cover():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", [1, 2], VariableType.ARRAY_NUMBER, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "cover",
                    "value": [3, 4, 5]
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") == [3, 4, 5]


# Multiple assignments test
@pytest.mark.asyncio
async def test_assigner_multiple_assignments():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test1", 10, VariableType.NUMBER, mut=True)
    await variable_pool.new("conv", "test2", "hello", VariableType.STRING, mut=True)
    await variable_pool.new("conv", "test3", [1, 2], VariableType.ARRAY_NUMBER, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test1}}",
                    "operation": "add",
                    "value": 5
                },
                {
                    "variable_selector": "{{conv.test2}}",
                    "operation": "assign",
                    "value": "world"
                },
                {
                    "variable_selector": "{{conv.test3}}",
                    "operation": "append",
                    "value": 3
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test1") == 15
    assert variable_pool.get_value("conv.test2") == "world"
    assert variable_pool.get_value("conv.test3") == [1, 2, 3]


# Variable reference test
@pytest.mark.asyncio
async def test_assigner_variable_reference():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "source", 100, VariableType.NUMBER, mut=True)
    await variable_pool.new("conv", "target", 0, VariableType.NUMBER, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.target}}",
                    "operation": "assign",
                    "value": "{{conv.source}}"
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.target") == 100


# Edge cases
@pytest.mark.asyncio
async def test_assigner_divide_by_zero():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 10, VariableType.NUMBER, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "divide",
                    "value": 0
                }
            ]
        }
    }
    with pytest.raises(ZeroDivisionError):
        await AssignerNode(config, {}).execute(state, variable_pool)


@pytest.mark.asyncio
async def test_assigner_invalid_namespace():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("sys", "test", 10, VariableType.NUMBER, mut=False)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{sys.test}}",
                    "operation": "add",
                    "value": 5
                }
            ]
        }
    }
    with pytest.raises(ValueError) as exc_info:
        await AssignerNode(config, {}).execute(state, variable_pool)
    assert "Only conversation or cycle variables can be assigned" in str(exc_info.value)


@pytest.mark.asyncio
async def test_assigner_empty_array_operations():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", [], VariableType.ARRAY_NUMBER, mut=True)

    # Test append on empty array
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "append",
                    "value": 1
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") == [1]


@pytest.mark.asyncio
async def test_assigner_remove_from_single_element_array():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", [1], VariableType.ARRAY_NUMBER, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "remove_last"
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") == []


@pytest.mark.asyncio
async def test_assigner_float_operations():
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "test", 10.5, VariableType.NUMBER, mut=True)
    config = {
        "id": "assigner_test",
        "type": "assigner",
        "name": "赋值测试节点",
        "config": {
            "assignments": [
                {
                    "variable_selector": "{{conv.test}}",
                    "operation": "multiply",
                    "value": 2.0
                }
            ]
        }
    }
    await AssignerNode(config, {}).execute(state, variable_pool)
    assert variable_pool.get_value("conv.test") == 21.0
