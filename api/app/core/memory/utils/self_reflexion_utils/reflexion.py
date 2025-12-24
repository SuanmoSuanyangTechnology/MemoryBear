# -*- coding: utf-8 -*-
"""反思执行模块

本模块提供反思执行功能，使用LLM对冲突记忆进行反思和解决。
从 app.core.memory.src.data_config_api.reflexion 迁移而来。
"""

import logging
import time
from typing import Any, List

from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.core.memory.utils.prompt.template_render import render_reflexion_prompt
from app.db import get_db_context
from app.schemas.memory_storage_schema import ReflexionResultSchema
from pydantic import BaseModel


async def reflexion(ref_data: List[Any]) -> List[Any]:
    """
    Reflexes on the given reference data using the reflexion.jinja2 template.

    Args:
        ref_data: 反思数据列表。
    Returns:
        反思结果列表（JSON 数组）。
    """
    from app.core.memory.utils.config import definitions as config_defs
    with get_db_context() as db:
        factory = MemoryClientFactory(db)
        client = factory.get_llm_client(config_defs.SELECTED_LLM_ID)
    rendered_prompt = await render_reflexion_prompt(ref_data, ReflexionResultSchema)
    messages = [{"role": "user", "content": rendered_prompt}]
    print(f"提示词长度: {len(rendered_prompt)}")

    print(f"====== 反思开始 ======\n")
    start_time = time.time()
    response = await client.response_structured(messages, ReflexionResultSchema)
    end_time = time.time()
    print(f"反思耗时: {end_time - start_time} 秒")
    print(f"反思原始输出:(type={type(response)})\n{response}")

    if not response:
        logging.error("LLM 反思输出解析失败，返回空列表以继续流程。")
        return []
    # 统一返回为列表[dict]，便于自我反思主流程更新数据库
    try:
        return [response.model_dump()] if isinstance(response, BaseModel) else [response]
    except Exception:
        try:
            return [response.dict()]
        except Exception:
            logging.warning("无法标准化反思返回类型，尝试直接封装为列表。")
            return [response]
