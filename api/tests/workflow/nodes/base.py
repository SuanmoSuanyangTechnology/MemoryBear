# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/5 18:19
import os

import pytest

from app.core.workflow.variable.base_variable import VariableType, DEFAULT_VALUE
from app.core.workflow.variable_pool import VariablePool

TEST_WORKSPACE_ID = "test_workspace_id"
TEST_USER_ID = "test_user_id"
TEST_EXECUTION_ID = "test_execution_id"
TEST_CONVERSATION_ID = "test_conversation_id"
TEST_MODEL_ID = "" or os.getenv("TEST_MODEL_ID")
TEST_FILE = {
    "type": "image",
    "url": "https://inews.gtimg.com/om_bt/Ojy0PdDIWWXRTAMh2QjsiumDZh-D1x7qCkDSmoaaX6INAAA/641",
    "__file": True
}
INPUT_DATA = {
    "message": "",
    "variables": [],
    "conversation_id": TEST_CONVERSATION_ID,
    "files": [TEST_FILE]
}


@pytest.fixture(scope="session", autouse=True)
def global_precheck():
    assert bool(TEST_MODEL_ID) is True, 'PLASE SET TEST_MODEL_ID FIRST'


def simple_state():
    return {
        "messages": [{"role": "user", "content": "123456"}],
        "node_outputs": {},
        "execution_id": TEST_EXECUTION_ID,
        "workspace_id": TEST_WORKSPACE_ID,
        "user_id": TEST_USER_ID,
        "error": None,
        "error_node": None,
        "cycle_nodes": [],  # loop, iteration node id
        "looping": 0,  # loop runing flag, only use in loop node,not use in main loop
        "activate": {}
    }


async def simple_vairable_pool(message):
    # Initialize system variables (sys namespace)
    variable_pool = VariablePool()
    user_message = message
    user_files = INPUT_DATA.get("files") or []

    # Initialize system variables (sys namespace)
    input_variables = INPUT_DATA.get("variables") or {}
    sys_vars = {
        "message": (user_message, VariableType.STRING),
        "conversation_id": (INPUT_DATA.get("conversation_id"), VariableType.STRING),
        "execution_id": (TEST_EXECUTION_ID, VariableType.STRING),
        "workspace_id": (TEST_WORKSPACE_ID, VariableType.STRING),
        "user_id": (TEST_USER_ID, VariableType.STRING),
        "input_variables": (input_variables, VariableType.OBJECT),
        "files": (user_files, VariableType.ARRAY_FILE)
    }
    for key, var_def in sys_vars.items():
        value = var_def[0]
        var_type = var_def[1]
        await variable_pool.new(
            namespace='sys',
            key=key,
            value=value,
            var_type=VariableType(var_type),
            mut=False
        )
    return variable_pool
