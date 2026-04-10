# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/4/8 18:11
import re

from app.schemas.memory_agent_schema import AgentMemoryDataset


class QueryPreprocessor:
    @staticmethod
    def process(query: str) -> str:
        text = query.strip()
        if not text:
            return text

        text = re.sub(rf"{"|".join(AgentMemoryDataset.PRONOUN)}", AgentMemoryDataset.NAME, text)
        return text
