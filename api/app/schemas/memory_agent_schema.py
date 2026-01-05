from typing import List, Optional
from pydantic import BaseModel, Field

class MessageInput(BaseModel):
    """单条消息输入"""
    role: str = Field(..., description="消息角色: 'user' 表示用户, 'assistant' 表示模型/AI")
    content: str = Field(..., description="消息内容")


class UserInput(BaseModel):
    message: str
    history: list[dict]
    search_switch: str
    group_id: str
    config_id: Optional[str] = None


class Write_UserInput(BaseModel):
    """写入请求输入 - 多条消息模式，可以区分用户和模型的消息"""
    messages: List[MessageInput] = Field(..., description="消息列表，包含角色信息")
    group_id: str = Field(..., description="群组ID")
    config_id: Optional[str] = Field(None, description="配置ID（可选）")

class End_User_Information(BaseModel):
    end_user_name: str  # 这是要更新的用户名
    id: str  # 宿主ID，用于匹配条件
