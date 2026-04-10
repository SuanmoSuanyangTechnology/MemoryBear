# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/4/3 11:44
import uuid
from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy.orm import Session

from demo.memory_alpha import MemoryContext


class ModelClientMixin(ABC):
    def get_llm_client(self, db: Session, model_id: uuid.UUID):
        pass

    def get_embedding_client(self, db: Session, model_id: uuid.UUID):
        pass


class BasePipeline(ABC):
    def __init__(self, ctx: MemoryContext, db: Session):
        self.ctx = ctx
        self.db = db

    @abstractmethod
    async def run(self, *args, **kwargs) -> Any:
        pass
