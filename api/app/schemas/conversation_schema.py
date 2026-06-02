"""会话和消息相关的 Schema"""
import uuid
import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, ConfigDict, field_serializer, model_serializer

from app.core.utils.datetime_utils import to_timestamp_ms
# 导入 FileInput（用于体验运行）
from app.schemas.app_schema import FileInput


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
    message: Optional[str] = Field(default=None, description="用户消息，pure_workflow 可不传")
    conversation_id: Optional[uuid.UUID] = Field(None, description="会话ID（多轮对话）")
    user_id: Optional[str] = Field(None, description="用户ID（外部系统）")
    variables: Optional[Dict[str, Any]] = Field(None, description="变量参数")
    stream: bool = Field(default=False, description="是否流式返回")
    web_search: bool = Field(default=False, description="是否启用网络搜索")
    memory: bool = Field(default=True, description="是否启用记忆功能")
    thinking: bool = Field(default=False, description="是否启用深度思考（需Agent配置支持）")
    files: List[FileInput] = Field(default_factory=list, description="附件列表（支持多文件）")


# ---------- Output Schemas ----------

class Message(BaseModel):
    """消息输出"""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    content: str
    status: str
    meta_data: Optional[Dict[str, Any]] = None
    created_at: datetime.datetime
    # 反馈信息（仅助手消息有效，由 controller 注入）
    feedback_type: Optional[str] = Field(None, description="反馈类型: like/dislike")
    feedback_content: Optional[str] = Field(None, description="反馈内容")
    # 多版本支持
    version: Optional[int] = Field(1, description="消息版本号")
    is_current: Optional[bool] = Field(True, description="是否当前版本")
    parent_message_id: Optional[uuid.UUID] = Field(None, description="父消息ID（assistant消息指向user消息）")

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("meta_data", when_used="json")
    def _serialize_meta_data(self, data: Optional[Dict[str, Any]]):
        return data or {}


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
        return to_timestamp_ms(dt)

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)


class ConversationWithMessages(Conversation):
    """会话详情（包含消息列表）"""
    messages: List[Message] = []


class ChatResponse(BaseModel):
    """聊天响应（非流式）"""
    conversation_id: uuid.UUID
    message: str
    message_id: str
    usage: Optional[Dict[str, Any]] = None
    elapsed_time: Optional[float] = None
    reasoning_content: Optional[str] = None
    suggested_questions: Optional[List[str]] = None
    citations: Optional[List[Dict[str, Any]]] = None
    audio_url: Optional[str] = None
    audio_status: Optional[str] = None

    @model_serializer(mode="wrap")
    def _serialize(self, handler):
        data = handler(self)
        if not data.get("reasoning_content"):
            data.pop("reasoning_content", None)
        return data


# ---------- Conversation Summary Schemas ----------
class ConversationOut(BaseModel):
    theme: str
    question: list[str]
    summary: str
    takeaways: list[str]
    info_score: int
