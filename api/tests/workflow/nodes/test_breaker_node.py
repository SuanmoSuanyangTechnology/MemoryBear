# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/5 19:15
import pytest

from app.core.workflow.nodes.breaker import BreakNode
from tests.workflow.nodes.base import simple_state, simple_vairable_pool


@pytest.mark.asyncio
async def test_loop_breaker():
    node_config = {
        "id": "breaker_test",
        "type": "breaker",
        "name": "breaker",
        "config": {
        }
    }
    state = simple_state()
    variable_pool = await simple_vairable_pool("test")
    await BreakNode(node_config, {}).execute(state, variable_pool)
    assert state["looping"] == 2
