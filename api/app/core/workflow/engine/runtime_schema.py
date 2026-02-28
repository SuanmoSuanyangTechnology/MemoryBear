# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/10 13:33
import uuid

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel


class ExecutionContext(BaseModel):
    execution_id: str
    workspace_id: str
    user_id: str
    checkpoint_config: RunnableConfig

    @classmethod
    def create(cls, execution_id: str, workspace_id: str, user_id: str):
        return cls(
            execution_id=execution_id,
            workspace_id=workspace_id,
            user_id=user_id,
            checkpoint_config=RunnableConfig(
                configurable={
                    "thread_id": uuid.uuid4(),
                }
            )
        )

