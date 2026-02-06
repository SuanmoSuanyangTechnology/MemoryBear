# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/6 09:59
import pytest

from app.core.workflow.nodes.code import CodeNode
from app.core.workflow.variable.base_variable import VariableType
from tests.workflow.nodes.base import simple_state, simple_vairable_pool


@pytest.mark.asyncio
async def test_code_python_complex_output():
    node_config = {
        "id": "code_test",
        "type": "code",
        "name": "代码执行",
        "config": {
            "code": "ZGVmJTIwbWFpbih4JTJDJTIweSklM0ElMEElMjAlMjAlMjAlMjByZXR1cm4lMjAlN0IlMEElMjAlMjAlMjAlMjAlMjAlMjAlMjAlMjAlMjJudW1iZXIlMjIlM0ElMjB4JTIwJTJCJTIweSUyQyUwQSUyMCUyMCUyMCUyMCUyMCUyMCUyMCUyMCUyMnN0cmluZyUyMiUzQSUyMHN0cih4JTIwJTJCJTIweSklMkMlMEElMjAlMjAlMjAlMjAlMjAlMjAlMjAlMjAlMjJib29sZWFuJTIyJTNBJTIwYm9vbCh4JTIwJTJCJTIweSklMkMlMEElMjAlMjAlMjAlMjAlMjAlMjAlMjAlMjAlMjJkaWN0JTIyJTNBJTIwJTdCJTIyc3VtJTIyJTNBJTIweCUyMCUyQiUyMHklN0QlMkMlMEElMjAlMjAlMjAlMjAlMjAlMjAlMjAlMjAlMjJhcnJheV9zdHJpbmclMjIlM0ElMjAlNUJzdHIoeCUyMCUyQiUyMHkpJTVEJTJDJTBBJTIwJTIwJTIwJTIwJTIwJTIwJTIwJTIwJTIyYXJyYXlfbnVtYmVyJTIyJTNBJTIwJTVCeCUyMCUyQiUyMHklNUQlMkMlMEElMjAlMjAlMjAlMjAlMjAlMjAlMjAlMjAlMjJhcnJheV9vYmplY3QlMjIlM0ElMjAlNUIlN0IlMjJzdW0lMjIlM0ElMjB4JTIwJTJCJTIweSU3RCU1RCUyQyUwQSUyMCUyMCUyMCUyMCUyMCUyMCUyMCUyMCUyMmFycmF5X2Jvb2xlYW4lMjIlM0ElMjAlNUJib29sKHglMjAlMkIlMjB5KSU1RCUwQSUyMCUyMCUyMCUyMCU3RA==",
            "language": "python3",
            "input_variables": [
                {
                    "name": "x",
                    "variable": "{{conv.x}}"
                },
                {
                    "name": "y",
                    "variable": "{{conv.y}}"
                }
            ],
            "output_variables": [
                {
                    "name": "number",
                    "type": VariableType.NUMBER
                },
                {
                    "name": "string",
                    "type": VariableType.STRING
                },
                {
                    "name": "boolean",
                    "type": VariableType.BOOLEAN
                },
                {
                    "name": "dict",
                    "type": VariableType.OBJECT
                },
                {
                    "name": "array_string",
                    "type": VariableType.ARRAY_STRING
                },
                {
                    "name": "array_number",
                    "type": VariableType.ARRAY_NUMBER
                },
                {
                    "name": "array_object",
                    "type": VariableType.ARRAY_OBJECT
                },
                {
                    "name": "array_boolean",
                    "type": VariableType.ARRAY_BOOLEAN
                },
            ]
        }
    }
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "x", 1, VariableType.NUMBER, mut=True)
    await variable_pool.new("conv", "y", 2, VariableType.NUMBER, mut=True)
    result = await CodeNode(node_config, {}).execute(state, variable_pool)
    assert result == {'number': 3, 'string': '3', 'boolean': True, 'dict': {'sum': 3}, 'array_string': ['3'],
                      'array_number': [3], 'array_object': [{'sum': 3}], 'array_boolean': [True]}


@pytest.mark.asyncio
async def test_code_javascript_complex_output():
    node_config = {
        "id": "code_test",
        "type": "code",
        "name": "代码执行",
        "config": {
            "code": "ZnVuY3Rpb24gbWFpbih7eCwgeX0pIHsKICBjb25zdCBzdW0gPSB4ICsgeTsKCiAgcmV0dXJuIHsKICAgIG51bWJlcjogc3VtLAogICAgc3RyaW5nOiBTdHJpbmcoc3VtKSwKICAgIGJvb2xlYW46IEJvb2xlYW4oc3VtKSwKICAgIGRpY3Q6IHsgc3VtIH0sCiAgICBhcnJheV9zdHJpbmc6IFtTdHJpbmcoc3VtKV0sCiAgICBhcnJheV9udW1iZXI6IFtzdW1dLAogICAgYXJyYXlfb2JqZWN0OiBbeyBzdW0gfV0sCiAgICBhcnJheV9ib29sZWFuOiBbQm9vbGVhbihzdW0pXSwKICB9Owp9",
            "language": "javascript",
            "input_variables": [
                {
                    "name": "x",
                    "variable": "{{conv.x}}"
                },
                {
                    "name": "y",
                    "variable": "{{conv.y}}"
                }
            ],
            "output_variables": [
                {
                    "name": "number",
                    "type": VariableType.NUMBER
                },
                {
                    "name": "string",
                    "type": VariableType.STRING
                },
                {
                    "name": "boolean",
                    "type": VariableType.BOOLEAN
                },
                {
                    "name": "dict",
                    "type": VariableType.OBJECT
                },
                {
                    "name": "array_string",
                    "type": VariableType.ARRAY_STRING
                },
                {
                    "name": "array_number",
                    "type": VariableType.ARRAY_NUMBER
                },
                {
                    "name": "array_object",
                    "type": VariableType.ARRAY_OBJECT
                },
                {
                    "name": "array_boolean",
                    "type": VariableType.ARRAY_BOOLEAN
                },
            ]
        }
    }
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "x", 1, VariableType.NUMBER, mut=True)
    await variable_pool.new("conv", "y", 2, VariableType.NUMBER, mut=True)
    result = await CodeNode(node_config, {}).execute(state, variable_pool)
    assert result == {'number': 3, 'string': '3', 'boolean': True, 'dict': {'sum': 3}, 'array_string': ['3'],
                      'array_number': [3], 'array_object': [{'sum': 3}], 'array_boolean': [True]}


@pytest.mark.asyncio
async def test_code_python_operation_permissions():
    node_config = {
        "id": "code_test",
        "type": "code",
        "name": "代码执行",
        "config": {
            "code": "ZGVmJTIwbWFpbih4JTJDJTIweSklM0ElMEElMjAlMjAlMjAlMjBpbXBvcnQlMjBvcyUwQSUyMCUyMCUyMCUyMG9zLmdldGN3ZCgpJTBBJTIwJTIwJTIwJTIwcmV0dXJuJTIwJTdCJTIycmVzdWx0JTIyJTNBJTIweCUyMCUyQiUyMHklN0QlMEE=",
            "language": "python3",
            "input_variables": [
                {
                    "name": "x",
                    "variable": "{{conv.x}}"
                },
                {
                    "name": "y",
                    "variable": "{{conv.y}}"
                }
            ],
            "output_variables": [
                {
                    "name": "result",
                    "type": "number"
                }
            ]
        }
    }
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "x", 1, VariableType.NUMBER, mut=True)
    await variable_pool.new("conv", "y", 2, VariableType.NUMBER, mut=True)
    with pytest.raises(RuntimeError, match="Operation not permitted"):
        await CodeNode(node_config, {}).execute(state, variable_pool)


@pytest.mark.asyncio
async def test_code_javascript_operation_permissions():
    node_config = {
        "id": "code_test",
        "type": "code",
        "name": "代码执行",
        "config": {
            "code": "Y29uc29sZS5sb2cocHJvY2Vzcy5nZXRldWlkKCkpOw==",
            "language": "javascript",
            "input_variables": [
                {
                    "name": "x",
                    "variable": "{{conv.x}}"
                },
                {
                    "name": "y",
                    "variable": "{{conv.y}}"
                }
            ],
            "output_variables": [
                {
                    "name": "result",
                    "type": "number"
                }
            ]
        }
    }
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "x", 1, VariableType.NUMBER, mut=True)
    await variable_pool.new("conv", "y", 2, VariableType.NUMBER, mut=True)
    with pytest.raises(RuntimeError, match="Operation not permitted"):
        await CodeNode(node_config, {}).execute(state, variable_pool)


@pytest.mark.asyncio
async def test_code_python_run_error():
    node_config = {
        "id": "code_test",
        "type": "code",
        "name": "代码执行",
        "config": {
            "code": "ZGVmJTIwbWFpbih4JTJDJTIweSUzQSUwQSUyMCUyMCUyMCUyMHJldHVybiUyMCU3QiUyMnJlc3VsdCUyMiUzQSUyMHglMjAlMkIlMjB5JTdEJTBB",
            "language": "python3",
            "input_variables": [
                {
                    "name": "x",
                    "variable": "{{conv.x}}"
                },
                {
                    "name": "y",
                    "variable": "{{conv.y}}"
                }
            ],
            "output_variables": [
                {
                    "name": "result",
                    "type": "number"
                }
            ]
        }
    }
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "x", 1, VariableType.NUMBER, mut=True)
    await variable_pool.new("conv", "y", 2, VariableType.NUMBER, mut=True)
    with pytest.raises(Exception) as exc_info:
        await CodeNode(node_config, {}).execute(state, variable_pool)
    assert "'(' was never closed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_code_javascript_run_error():
    node_config = {
        "id": "code_test",
        "type": "code",
        "name": "代码执行",
        "config": {
            "code": "Y29uc29sZS5sb2co",
            "language": "javascript",
            "input_variables": [
                {
                    "name": "x",
                    "variable": "{{conv.x}}"
                },
                {
                    "name": "y",
                    "variable": "{{conv.y}}"
                }
            ],
            "output_variables": [
                {
                    "name": "result",
                    "type": "number"
                }
            ]
        }
    }
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "x", 1, VariableType.NUMBER, mut=True)
    await variable_pool.new("conv", "y", 2, VariableType.NUMBER, mut=True)
    with pytest.raises(Exception) as exc_info:
        await CodeNode(node_config, {}).execute(state, variable_pool)
    assert "SyntaxError" in str(exc_info.value)
