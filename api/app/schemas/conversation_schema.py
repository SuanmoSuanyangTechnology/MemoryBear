"""会话和消息相关的 Schema"""
import uuid
import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, ConfigDict, field_serializer


# ---------- Input Schemas ----------

class ConversationCreate(BaseModel):
    """创建会话请求"""
    title: Optional[str] = Field(None, max_length=255, description="会话标题")
    user_id: Optional[str] = Field(None, description="用户ID（外部系统）")


class MessageCreate(BaseModel):
    """创建消息请求"""
    content: str = Field(..., description="消息内容")
    variables: Optional[Dict[str, Any]] = Field(None, description="变量参数")


class ChatRequest(BaseModel):
    """聊天请求（基于 share_token）"""
    message: str = Field(..., description="用户消息")
    conversation_id: Optional[uuid.UUID] = Field(None, description="会话ID（多轮对话）")
    user_id: Optional[str] = Field(None, description="用户ID（外部系统）")
    variables: Optional[Dict[str, Any]] = Field(None, description="变量参数")
    stream: bool = Field(default=False, description="是否流式返回")
    web_search: bool = Field(default=False, description="是否启用网络搜索")
    memory: bool = Field(default=True, description="是否启用记忆功能")


# ---------- Output Schemas ----------

class Message(BaseModel):
    """消息输出"""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    content: str
    meta_data: Optional[Dict[str, Any]] = None
    created_at: datetime.datetime

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None


class Conversation(BaseModel):
    """会话输出"""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    app_id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: Optional[str] = None
    title: Optional[str] = None
    summary: Optional[str] = None
    is_draft: bool
    message_count: int
    is_active: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None


class ConversationWithMessages(Conversation):
    """会话详情（包含消息列表）"""
    messages: List[Message] = []


class ChatResponse(BaseModel):
    """聊天响应（非流式）"""
    conversation_id: uuid.UUID
    message: str
    usage: Optional[Dict[str, Any]] = None
    elapsed_time: Optional[float] = None


# ---------- Conversation Summary Schemas ----------
class ConversationOut(BaseModel):
    theme: str
    theme_intro: str
    summary: str
    takeaways: list[str]
    info_score: int
