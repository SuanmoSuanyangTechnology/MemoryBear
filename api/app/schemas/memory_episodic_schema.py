"""
情景记忆的请求和响应模型
"""
from abc import ABC
from pydantic import BaseModel, Field
from typing import Optional

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
class EmotionType(ABC):
    JOY_TYPE = "joy"
    SURPRISE_TYPE = "surprise"
    SANDROWNESS_TYPE = "sadness"
    FEAR_TYPE = "fear"
    ANGET_TYPE="anger"
    NEUTRAL_TYPE="neutral"
    EMOTION_MAPPING={
        "joy":"愉快",
        "surprise":"惊喜",
        "sadness":"悲伤",
        "fear":"恐惧",
        "anger":"生气",
        "neutral":"中性"
    }
class EmotionSubject(ABC):
    SUBJECT_MAPPING={
        "self":"自己",
        "other":"别人",
        "object":"事物对象"
    }

class EpisodicMemoryOverviewRequest(BaseModel):
    """情景记忆总览查询请求"""
    
    end_user_id: str = Field(..., description="终端用户ID")
    time_range: str = Field(
        default="all",
        description="时间范围筛选，可选值：all, today, this_week, this_month"
    )
    episodic_type: str = Field(
        default="all",
        description="情景类型筛选，可选值：all, conversation, project_work, learning, decision, important_event"
    )
    title_keyword: Optional[str] = Field(
        default=None,
        description="标题关键词，用于模糊搜索（可选）"
    )


class EpisodicMemoryDetailsRequest(BaseModel):
    """情景记忆详情查询请求"""
    
    end_user_id: str = Field(..., description="终端用户ID")
    summary_id: str = Field(..., description="情景记忆摘要ID")
