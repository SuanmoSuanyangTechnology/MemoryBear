# -*- coding: utf-8 -*-
"""记忆冲突判定模块

本模块提供记忆冲突判定功能，使用LLM判断记忆数据中是否存在冲突。
从 app.core.memory.src.data_config_api.evaluate 迁移而来。
"""

import logging
import time
from typing import Any, List

from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.core.memory.utils.prompt.template_render import render_evaluate_prompt
from app.db import get_db_context
from app.schemas.memory_storage_schema import ConflictResultSchema
from pydantic import BaseModel


async def conflict(evaluate_data: List[Any]) -> List[Any]:
    """
    Evaluates memory conflict using the evaluate.jinja2 template.

    Args:
        evaluate_data: 反思数据列表。
    Returns:
        冲突记忆列表（JSON 数组）。
    """
    from app.core.memory.utils.config import definitions as config_defs
    with get_db_context() as db:
        factory = MemoryClientFactory(db)
        client = factory.get_llm_client(config_defs.SELECTED_LLM_ID)
    rendered_prompt = await render_evaluate_prompt(evaluate_data, ConflictResultSchema)
    messages = [{"role": "user", "content": rendered_prompt}]
    print(f"提示词长度: {len(rendered_prompt)}")
    print(f"====== 冲突判定开始 ======\n")
    start_time = time.time()
    response = await client.response_structured(messages, ConflictResultSchema)
    end_time = time.time()
    print(f"冲突判定耗时: {end_time - start_time} 秒")
    print(f"冲突判定原始输出:(type={type(response)})\n{response}")

    if not response:
        logging.error("LLM 冲突判定输出解析失败，返回空列表以继续流程。")
        return []
    try:
        return [response.model_dump()] if isinstance(response, BaseModel) else [response]
    except Exception:
        try:
            return [response.dict()]
        except Exception:
            logging.warning("无法标准化冲突判定返回类型，尝试直接封装为列表。")
            return [response]
