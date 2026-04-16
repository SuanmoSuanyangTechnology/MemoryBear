"""Models for user metadata extraction.

Independent from triplet_models.py - these models are used by the
standalone metadata extraction pipeline (post-dedup async Celery task).
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class UserMetadataProfile(BaseModel):
    """用户画像信息"""

    model_config = ConfigDict(extra="ignore")
    role: List[str] = Field(default_factory=list, description="用户职业或角色")
    domain: List[str] = Field(default_factory=list, description="用户所在领域")
    expertise: List[str] = Field(
        default_factory=list, description="用户擅长的技能或工具"
    )
    interests: List[str] = Field(
        default_factory=list, description="用户关注的话题或领域标签"
    )


class UserMetadata(BaseModel):
    """用户元数据顶层结构"""

    model_config = ConfigDict(extra="ignore")
    profile: UserMetadataProfile = Field(default_factory=UserMetadataProfile)


class MetadataFieldChange(BaseModel):
    """单个元数据字段的变更操作"""

    model_config = ConfigDict(extra="ignore")
    field_path: str = Field(
        description="字段路径，用点号分隔，如 'profile.role'、'profile.expertise'"
    )
    action: Literal["set", "remove"] = Field(
        description="操作类型：'set' 表示新增或修改，'remove' 表示移除"
    )
    value: Optional[str] = Field(
        default=None,
        description="字段的新值（action='set' 时必填）。标量字段直接填值，列表字段填单个要新增的元素"
    )


class MetadataExtractionResponse(BaseModel):
    """元数据提取 LLM 响应结构（增量模式）"""

    model_config = ConfigDict(extra="ignore")
    metadata_changes: List[MetadataFieldChange] = Field(
        default_factory=list,
        description="元数据的增量变更列表，每项描述一个字段的新增、修改或移除操作",
    )
    aliases_to_add: List[str] = Field(
        default_factory=list,
        description="本次新发现的用户别名（用户自我介绍或他人对用户的称呼）",
    )
    aliases_to_remove: List[str] = Field(
        default_factory=list, description="用户明确否认的别名（如'我不叫XX了'）"
    )
