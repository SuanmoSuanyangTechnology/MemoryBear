"""
情景记忆的请求和响应模型
"""
from abc import ABC
from pydantic import BaseModel, Field, field_validator
from typing import Literal


type_mapping = {
            "Person": "人物实体节点",
            "Organization": "组织实体节点",
            "ORG": "组织实体节点",
            "Location": "地点实体节点",
            "LOC": "地点实体节点",
            "Event": "事件实体节点",
            "Concept": "概念实体节点",
            "Time": "时间实体节点",
            "Position": "职位实体节点",
            "WorkRole": "职业实体节点",
            "System": "系统实体节点",
            "Policy": "政策实体节点",
            "HistoricalPeriod": "历史时期实体节点",
            "HistoricalState": "历史国家实体节点",
            "HistoricalEvent": "历史事件实体节点",
            "EconomicFactor": "经济因素实体节点",
            "Condition": "条件实体节点",
            "Numeric": "数值实体节点"
        }
class EmotionSubject(ABC):
    SUBJECT_MAPPING={
        "self":"自己",
        "other":"别人",
        "object":"事物对象"
    }

class EpisodicMemoryOverviewRequest(BaseModel):
    """情景记忆总览查询请求"""
    
    end_user_id: str = Field(..., description="终端用户 ID，UUID 字符串")
    category_l1: str | None = Field(None, min_length=1, description="事项类型代码，不传表示全部类型")
    keyword: str | None = Field(None, description="搜索社区名称、摘要及所属情景正文，最多 200 字符")
    sort: Literal['scene_count_desc', 'created_at_desc', 'updated_at_desc'] = Field('scene_count_desc', description='排序：scene_count_desc 情景数优先、created_at_desc 创建时间优先、updated_at_desc 更新时间优先')
    page: int = Field(1, ge=1, description="页码，从 1 开始")
    pagesize: int = Field(20, ge=1, le=100, description="每页条数，默认 20，最大 100")

    @field_validator('keyword')
    @classmethod
    def normalize_keyword(cls, value):
        value = value.strip() if value else None
        if value and len(value) > 200:
            raise ValueError('关键词最多 200 字符')
        return value


class EpisodicMemoryDetailsRequest(BaseModel):
    """情景记忆详情查询请求"""
    
    end_user_id: str = Field(..., description="终端用户 ID，UUID 字符串")
    scene_community_id: str = Field(..., min_length=1, description="overview 返回的社区业务 ID，不是 Neo4j elementId")

    page: int = Field(1, ge=1, description="情景页码，从 1 开始；下拉加载时递增")
    pagesize: int = Field(20, ge=1, le=100, description="每页情景数量，默认 20，最大 100")

    @field_validator('scene_community_id')
    @classmethod
    def validate_community_id(cls, value):
        if value != value.strip() or value.startswith('virtual:'):
            raise ValueError('无效社区 ID')
        return value
