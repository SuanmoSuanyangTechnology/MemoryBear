import uuid
from abc import ABC
from typing import Optional

from pydantic import BaseModel, Field


class UserInput(BaseModel):
    message: str
    search_switch: str
    end_user_id: str
    session_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    config_id: Optional[str] = None


class Write_UserInput(BaseModel):
    messages: list[dict]
    end_user_id: str
    config_id: Optional[str] = None


class AgentMemory_Long_Term(ABC):
    """长期记忆配置常量"""
    STORAGE_NEO4J = "neo4j"
    STORAGE_RAG = "rag"
    STRATEGY_AGGREGATE = "aggregate"
    STRATEGY_CHUNK = "chunk"
    STRATEGY_TIME = "time"
    DEFAULT_SCOPE = 6
    TIME_SCOPE = 5


class AgentMemoryDataset(ABC):
    PRONOUN = ['我', '本人', '在下', '自己', '咱', '鄙人', '吴', '余']
    NAME = '用户'
