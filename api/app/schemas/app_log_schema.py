"""应用日志（消息记录）Schema"""
import uuid
import datetime
from typing import Optional, Dict, Any, List

from pydantic import BaseModel, Field, ConfigDict, field_serializer

from app.core.utils.datetime_utils import to_timestamp_ms

class LogFileInfo(BaseModel):
    """日志中用户上传的文件信息"""
    type: str = Field(description="文件类型: image / document / audio / video")
    url: str = Field(description="文件访问 URL")
    name: Optional[str] = Field(default=None, description="文件名")
    size: Optional[int] = Field(default=None, description="文件大小（字节）")
    file_type: Optional[str] = Field(default=None, description="MIME 类型，如 image/jpeg")


class AppLogMessage(BaseModel):
    """单条消息记录"""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str = Field(description="角色: user / assistant / system")
    content: str
    status: Optional[str] = Field(default=None, description="消息状态: completed / failed")
    meta_data: Optional[Dict[str, Any]] = None
    files: List[LogFileInfo] = Field(default_factory=list, description="用户上传的文件列表")
    created_at: datetime.datetime

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("meta_data", when_used="json")
    def _serialize_meta_data(self, data: Optional[Dict[str, Any]]):
        return data or {}


class AppLogConversation(BaseModel):
    """会话摘要（用于列表）"""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    app_id: uuid.UUID
    user_id: Optional[str] = None
    title: Optional[str] = None
    message_count: int = 0
    is_draft: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)


class AppLogNodeExecution(BaseModel):
    """工作流节点执行记录"""
    node_id: str
    node_type: str
    node_name: Optional[str] = None
    status: str = "pending"
    error: Optional[str] = None
    input: Optional[Any] = None
    process: Optional[Any] = None
    output: Optional[Any] = None
    cycle_items: Optional[List[Any]] = None
    elapsed_time: Optional[float] = None
    token_usage: Optional[Dict[str, Any]] = None
    meta: Optional[Dict[str, Any]] = None


class AppLogConversationDetail(AppLogConversation):
    """会话详情（包含消息列表）"""
    messages: List[AppLogMessage] = Field(default_factory=list)
    node_executions_map: Dict[str, List[AppLogNodeExecution]] = Field(default_factory=dict, description="按消息ID分组的节点执行记录")
