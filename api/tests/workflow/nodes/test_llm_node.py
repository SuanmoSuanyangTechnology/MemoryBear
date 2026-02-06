# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/5 15:39
import pytest

from app.core.workflow.nodes import LLMNode
from tests.workflow.nodes.base import TEST_MODEL_ID, simple_state, simple_vairable_pool


@pytest.mark.asyncio
async def test_llm_memory_no_stream():
    node_config = {
        "id": "llm_test",
        "type": "llm",
        "name": "LLM 问答",
        "config": {
            "messages": [
                {
                    "role": "system",
                    "content": "你是一个专业、友好且乐于助人的 AI 助手。"
                               "你的职责：- "
                               "准确理解用户的问题并提供有价值的回答"
                               "- 保持回答的专业性和准确性"
                               "- 如果不确定答案，诚实地告知用户"
                               "- 使用清晰、易懂的语言进行交流"
                               "回答风格："
                               "- 简洁明了，直击要点"
                               "- 必要时提供详细解释和示例"
                               "- 使用友好、礼貌的语气"
                               "- 适当使用格式化（如列表、段落）提高可读性"
                },
                {
                    "role": "user",
                    "content": "{{ sys.message }}"
                }
            ],
            "model_id": TEST_MODEL_ID,
            "temperature": 0.7,
            "max_tokens": 1000,
            "memory": {
                "enable": True,
                "enable_window": True,
                "window_size": 5
            },
            "vision": False,
            "vision_input": "{{sys.files}}"
        }
    }
    state = simple_state()
    variable_pool = await simple_vairable_pool("输出上一句话")
    result = await LLMNode(node_config, {}).execute(state, variable_pool)
    assert '123456' in result.content


@pytest.mark.asyncio
async def test_llm_memory_stream():
    node_config = {
        "id": "llm_test",
        "type": "llm",
        "name": "LLM 问答",
        "config": {
            "messages": [
                {
                    "role": "system",
                    "content": "你是一个专业、友好且乐于助人的 AI 助手。"
                               "你的职责：- "
                               "准确理解用户的问题并提供有价值的回答"
                               "- 保持回答的专业性和准确性"
                               "- 如果不确定答案，诚实地告知用户"
                               "- 使用清晰、易懂的语言进行交流"
                               "回答风格："
                               "- 简洁明了，直击要点"
                               "- 必要时提供详细解释和示例"
                               "- 使用友好、礼貌的语气"
                               "- 适当使用格式化（如列表、段落）提高可读性"
                },
                {
                    "role": "user",
                    "content": "{{ sys.message }}"
                }
            ],
            "model_id": TEST_MODEL_ID,
            "temperature": 0.7,
            "max_tokens": 1000,
            "memory": {
                "enable": True,
                "enable_window": True,
                "window_size": 5
            },
            "vision": False,
            "vision_input": "{{sys.files}}"
        }
    }
    state = simple_state()
    variable_pool = await simple_vairable_pool("输出上一句话")
    async for event in LLMNode(node_config, {}).execute_stream(state, variable_pool):
        if event.get("__final__"):
            assert '123456' in event.get("result").content


@pytest.mark.asyncio
async def test_llm_vision():
    node_config = {
        "id": "llm_test",
        "type": "llm",
        "name": "LLM 问答",
        "config": {
            "messages": [
                {
                    "role": "system",
                    "content": "你是一个专业、友好且乐于助人的 AI 助手。"
                               "你的职责：- "
                               "准确理解用户的问题并提供有价值的回答"
                               "- 保持回答的专业性和准确性"
                               "- 如果不确定答案，诚实地告知用户"
                               "- 使用清晰、易懂的语言进行交流"
                               "回答风格："
                               "- 简洁明了，直击要点"
                               "- 必要时提供详细解释和示例"
                               "- 使用友好、礼貌的语气"
                               "- 适当使用格式化（如列表、段落）提高可读性"
                },
                {
                    "role": "user",
                    "content": "{{ sys.message }}"
                }
            ],
            "model_id": TEST_MODEL_ID,
            "temperature": 0.7,
            "max_tokens": 1000,
            "memory": {
                "enable": True,
                "enable_window": True,
                "window_size": 5
            },
            "vision": True,
            "vision_input": "{{sys.files}}"
        }
    }
    state = simple_state()
    variable_pool = await simple_vairable_pool("图片里面有什么")
    async for event in LLMNode(node_config, {}).execute_stream(state, variable_pool):
        if event.get("__final__"):
            assert '花' in event.get("result").content
