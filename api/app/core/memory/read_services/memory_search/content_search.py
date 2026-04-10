# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/4/9 16:48
from app.core.memory.llm_tools import OpenAIEmbedderClient
from app.core.memory.memory_service import MemoryContext


class ContentSearch:
    def __init__(self, ctx: MemoryContext):
        self.ctx = ctx

    async def search(self, query):
        pass