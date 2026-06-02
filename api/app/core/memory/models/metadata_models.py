"""Models for user metadata extraction.

Independent from triplet_models.py - these models are used by the
standalone metadata extraction pipeline (post-dedup async Celery task).

The field definitions align with the Jinja2 prompt template
``extract_user_metadata.jinja2``.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class MetadataExtractionResponse(BaseModel):
    """LLM 元数据提取响应结构。

    字段与 extract_user_metadata.jinja2 模板的输出 JSON 一一对应。
    每个字段都是字符串数组，表示本次新增的元数据条目。
    """

    model_config = ConfigDict(extra="ignore")

    aliases: List[str] = Field(
        default_factory=list,
        description="用户别名、昵称、称呼",
    )
    core_facts: List[str] = Field(
        default_factory=list,
        description="用户稳定的基础事实（身份、年龄、国籍、所在地等）",
    )
    traits: List[str] = Field(
        default_factory=list,
        description="用户稳定的人格特质、风格、行为倾向",
    )
    relations: List[str] = Field(
        default_factory=list,
        description="用户与他人/群体/宠物/重要对象之间的长期关系",
    )
    goals: List[str] = Field(
        default_factory=list,
        description="用户明确、稳定的长期目标或计划",
    )
    interests: List[str] = Field(
        default_factory=list,
        description="用户稳定的兴趣、偏好、长期爱好",
    )
    beliefs_or_stances: List[str] = Field(
        default_factory=list,
        description="用户稳定的信念、价值立场",
    )
    anchors: List[str] = Field(
        default_factory=list,
        description="对用户有长期意义的物品、收藏、纪念物",
    )
    events: List[str] = Field(
        default_factory=list,
        description="对用户画像有长期价值的个人经历、事件、里程碑",
    )

    # ── 便捷属性 ──

    METADATA_FIELDS: List[str] = [
        "core_facts", "traits", "relations", "goals",
        "interests", "beliefs_or_stances", "anchors", "events",
    ]

    def has_any_metadata(self) -> bool:
        """是否提取到了任何元数据（不含 aliases）。"""
        return any(
            bool(getattr(self, field, []))
            for field in self.METADATA_FIELDS
        )

    def to_metadata_dict(self) -> dict:
        """返回 8 个元数据字段的字典（不含 aliases），用于 Neo4j 回写。"""
        return {
            field: getattr(self, field, [])
            for field in self.METADATA_FIELDS
        }
