# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/6 12:22
import pytest

from app.core.workflow.nodes import EndNode
from app.core.workflow.variable.base_variable import VariableType
from tests.workflow.nodes.base import simple_state, simple_vairable_pool


@pytest.mark.asyncio
async def test_end_output():
    node_config = {
        "id": "end_test",
        "type": "end",
        "name": "end",
        "config": {
            "output": "{{conv.x}}{{sys.message}}"
        }
    }
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await variable_pool.new("conv", "x", 1, VariableType.NUMBER, mut=True)
    result = await EndNode(node_config, {}).execute(state, variable_pool)
    assert result == "1test"


@pytest.mark.asyncio
async def test_end_output_miss():
    node_config = {
        "id": "end_test",
        "type": "end",
        "name": "end",
        "config": {
            "output": "{{conv.x}}{{sys.message}}"
        }
    }
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    result = await EndNode(node_config, {}).execute(state, variable_pool)
    assert result == "test"
