"""Models for user metadata extraction.

Independent from triplet_models.py - these models are used by the
standalone metadata extraction pipeline (post-dedup async Celery task).
"""

from typing import List

from pydantic import BaseModel, ConfigDict, Field


class UserMetadataProfile(BaseModel):
    """用户画像信息"""
    model_config = ConfigDict(extra='ignore')
    role: str = Field(default="", description="用户职业或角色，如 teacher, doctor, software_engineer")
    domain: str = Field(default="", description="用户所在领域，如 education, healthcare, software_development")
    expertise: List[str] = Field(default_factory=list, description="用户擅长的技能或工具")
    interests: List[str] = Field(default_factory=list, description="用户关注的话题或领域标签")


class UserMetadataBehavioralHints(BaseModel):
    """行为偏好"""
    model_config = ConfigDict(extra='ignore')
    learning_stage: str = Field(default="", description="学习阶段")
    preferred_depth: str = Field(default="", description="偏好深度")
    tone_preference: str = Field(default="", description="语气偏好")


class UserMetadata(BaseModel):
    """用户元数据顶层结构"""
    model_config = ConfigDict(extra='ignore')
    profile: UserMetadataProfile = Field(default_factory=UserMetadataProfile)
    behavioral_hints: UserMetadataBehavioralHints = Field(default_factory=UserMetadataBehavioralHints)
    knowledge_tags: List[str] = Field(default_factory=list, description="知识标签")


class MetadataExtractionResponse(BaseModel):
    """元数据提取 LLM 响应结构"""
    model_config = ConfigDict(extra='ignore')
    user_metadata: UserMetadata = Field(default_factory=UserMetadata)
