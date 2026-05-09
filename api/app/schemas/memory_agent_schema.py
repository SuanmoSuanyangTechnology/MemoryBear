import uuid
from abc import ABC
from enum import Enum
from typing import Any
from typing import Optional, List

from pydantic import BaseModel, Field

from app.schemas.app_schema import FileInput


class StorageType(str, Enum):
    """记忆存储后端类型"""
    NEO4J = "neo4j"
    RAG = "rag"


class Language(str, Enum): # 没有传递到聚类的celery任务中去，任务会回退失败用默认值，考虑统一语言问题
    """支持的语言"""
    ZH = "zh"
    EN = "en"


class MessageItem(BaseModel):
    """单条消息结构"""
    role: str
    content: str
    dialog_at: Optional[str] = Field(
        None,
        description="该条消息发生的绝对时间（ISO 8601 格式），不传则使用服务端当前时间",
    )
    files: Optional[list[dict]] = None
    file_content: Optional[list[Any]] = None

    model_config = {"extra": "allow"}


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


class WriteMemoryRequest(BaseModel):
    """write_memory() 的参数封装"""
    end_user_id: str
    messages: list[MessageItem]
    config_id: Optional[Any] = None
    storage_type: StorageType = StorageType.NEO4J
    user_rag_memory_id: str = ""
    language: Language = Language.ZH


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
