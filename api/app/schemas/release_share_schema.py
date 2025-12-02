import uuid
import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict, field_serializer


# ---------- Input Schemas ----------

class ReleaseShareCreate(BaseModel):
    """创建/启用分享配置"""
    is_enabled: bool = Field(default=True, description="是否启用公开分享")
    require_password: bool = Field(default=False, description="是否需要密码访问")
    password: Optional[str] = Field(None, min_length=4, max_length=50, description="访问密码（4-50字符）")
    allow_embed: bool = Field(default=False, description="是否允许嵌入")
    embed_domains: Optional[List[str]] = Field(default=None, description="允许嵌入的域名白名单，空表示不限制")


class ReleaseShareUpdate(BaseModel):
    """更新分享配置"""
    is_enabled: Optional[bool] = Field(None, description="是否启用公开分享")
    require_password: Optional[bool] = Field(None, description="是否需要密码访问")
    password: Optional[str] = Field(None, min_length=4, max_length=50, description="访问密码")
    allow_embed: Optional[bool] = Field(None, description="是否允许嵌入")
    embed_domains: Optional[List[str]] = Field(None, description="允许嵌入的域名白名单")


class PasswordVerifyRequest(BaseModel):
    """密码验证请求"""
    password: str = Field(..., description="访问密码")


class TokenRequest(BaseModel):
    """获取访问 token 请求"""
    user_id: Optional[str] = Field(None, description="用户 ID（可选，不提供则自动生成）")
    password: Optional[str] = Field(None, description="访问密码（如果需要）")


# ---------- Output Schemas ----------

class ReleaseShare(BaseModel):
    """分享配置输出"""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    release_id: uuid.UUID
    app_id: uuid.UUID
    is_enabled: bool
    share_token: str
    share_url: str  # 完整的公开访问 URL
    require_password: bool
    allow_embed: bool
    embed_domains: List[str] = []
    view_count: int
    last_accessed_at: Optional[datetime.datetime] = None
    created_at: datetime.datetime
    updated_at: datetime.datetime

    @field_serializer("created_at", when_used="json")
    def _serialize_created_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None
    
    @field_serializer("updated_at", when_used="json")
    def _serialize_updated_at(self, dt: datetime.datetime):
        return int(dt.timestamp() * 1000) if dt else None
    
    @field_serializer("last_accessed_at", when_used="json")
    def _serialize_last_accessed_at(self, dt: Optional[datetime.datetime]):
        return int(dt.timestamp() * 1000) if dt else None


class SharedReleaseInfo(BaseModel):
    """公开访问返回的应用信息"""
    app_name: str
    app_description: Optional[str] = None
    app_icon: Optional[str] = None
    app_type: str
    version: int
    release_notes: Optional[str] = None
    published_at: int
    
    # 根据应用类型返回不同配置
    config: Dict[str, Any] = {}
    
    # 访问控制信息
    require_password: bool
    is_password_verified: bool = False  # 当前是否已验证密码
    
    # 嵌入配置
    allow_embed: bool


class EmbedCode(BaseModel):
    """嵌入代码"""
    iframe_code: str = Field(..., description="iframe 嵌入代码")
    preview_url: str = Field(..., description="预览 URL")
    width: str = Field(default="100%", description="宽度")
    height: str = Field(default="600px", description="高度")


class ShareStats(BaseModel):
    """分享统计"""
    view_count: int
    last_accessed_at: Optional[int] = None
    created_at: int
