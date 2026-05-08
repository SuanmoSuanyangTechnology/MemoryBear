import uuid
from abc import ABC
from typing import Optional, List

from pydantic import BaseModel, Field

from app.schemas.app_schema import FileInput


class UserInput(BaseModel):
    message: str
    search_switch: str
    end_user_id: str
    session_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    config_id: Optional[str] = None


class WriteMessageItem(BaseModel):
    """写入记忆的单条消息"""
    role: str = Field(..., description="消息角色: user 或 assistant")
    content: str = Field(..., description="消息内容")
    files: Optional[List[FileInput]] = Field(default=None, description="附带的文件列表（图片/文档/音频/视频）")


class Write_UserInput(BaseModel):
    messages: List[WriteMessageItem] = Field(..., description="消息列表")
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
