# -*- coding: utf-8 -*-
"""事件分类（event_timeline category）数据模型模块

反思引擎事件时间线（event_timeline）中每条事件带一个分类，分类有稳定 ID
（category_id，机读）与中文展示名（category，人读）两个维度，一一对应，共 13 类。

本模块是该分类枚举的**唯一权威来源（Single Source of Truth）**，供以下场景复用：
- 数据层写入兜底：校验 LLM 输出的 category 是否合法（``EVENT_CATEGORY_NAME_SET``）
- 接口层统计补全：按固定顺序补全 category_stats（``EVENT_CATEGORY_NAMES``）
- category_id ↔ category 的相互查询（``EventCategory``）

注意：LLM prompt 中的「带语义描述的分类指引」仍维护在 prompt 模板内（描述用于
辅助 LLM 分类，不适合机械注入），但其 13 个中文分类名必须与本模块保持一致。

Classes:
    EventCategory: 事件分类枚举，成员值为 category_id，附带中文展示名

Module constants:
    EVENT_CATEGORY_NAMES: 有序的中文分类名列表（用于补全 / 排序兜底）
    EVENT_CATEGORY_NAME_SET: 中文分类名集合（用于合法性校验）
    EVENT_CATEGORY_IDS: 有序的 category_id 列表
"""

from enum import Enum
from typing import List, Optional


class EventCategory(Enum):
    """事件分类枚举

    成员值为 ``category_id``（稳定机读 ID），``label`` 为中文展示名。
    枚举定义顺序即业务固定顺序（与 prompt 分类池顺序一致），
    用于 category_stats 在「无数据分类」补全时的排列。

    Attributes:
        label: 中文展示名，如「教育学习」
    """

    EDUCATION_LEARNING = ("education_learning", "教育学习")
    CAREER_WORK = ("career_work", "职业工作")
    PROJECT_MILESTONE = ("project_milestone", "项目里程碑")
    RESIDENCE_RELOCATION = ("residence_relocation", "居住迁移")
    RELATIONSHIP_FAMILY = ("relationship_family", "关系家庭")
    PET_CARE = ("pet_care", "宠物照护")
    HEALTH_MEDICAL = ("health_medical", "健康医疗")
    TRAVEL_VISIT = ("travel_visit", "旅行到访")
    PURCHASE_ASSET = ("purchase_asset", "购买资产")
    CREATION_PUBLICATION = ("creation_publication", "创作发布")
    ACHIEVEMENT_AWARD = ("achievement_award", "成就荣誉")
    FINANCE_LEGAL_ADMIN = ("finance_legal_admin", "财务法务行政")
    OTHER_LIFE_EVENT = ("other_life_event", "其他生活事件")

    def __init__(self, category_id: str, label: str):
        self._value_ = category_id
        self.label = label

    @property
    def category_id(self) -> str:
        """稳定机读 ID（等于枚举成员值）"""
        return self._value_

    @classmethod
    def names(cls) -> List[str]:
        """返回有序的中文分类名列表"""
        return [member.label for member in cls]

    @classmethod
    def ids(cls) -> List[str]:
        """返回有序的 category_id 列表"""
        return [member.category_id for member in cls]

    @classmethod
    def is_valid_name(cls, name: Optional[str]) -> bool:
        """判断中文分类名是否合法"""
        return name in EVENT_CATEGORY_NAME_SET

    @classmethod
    def name_by_id(cls, category_id: Optional[str]) -> Optional[str]:
        """根据 category_id 查中文分类名；不存在返回 None"""
        for member in cls:
            if member.category_id == category_id:
                return member.label
        return None


# 有序中文分类名（用于补全 / 排序兜底）
EVENT_CATEGORY_NAMES: List[str] = EventCategory.names()

# 中文分类名集合（用于合法性校验）
EVENT_CATEGORY_NAME_SET = frozenset(EVENT_CATEGORY_NAMES)

# 有序 category_id
EVENT_CATEGORY_IDS: List[str] = EventCategory.ids()
