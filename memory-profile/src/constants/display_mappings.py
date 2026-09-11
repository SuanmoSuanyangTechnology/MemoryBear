"""图数据响应中的展示名映射。

取自老单体 ``api/app/schemas/memory_episodic_schema.py``：
``type_mapping`` / ``EmotionType.EMOTION_MAPPING`` / ``EmotionSubject.SUBJECT_MAPPING``。
原文件还含情景记忆的请求模型与 ``EPISODIC_TYPE_MAPPING``，与本服务 graph_data
无关，未搬。

用途：``ExtractedEntity`` 节点的 ``entity_type``、``Statement`` 节点的
``emotion_type`` / ``emotion_subject`` 在写入侧存的是英文枚举，响应时转中文展示名。
**未命中映射时返回 ``None``**（不是原值）——与老单体一致，前端据此判定"未知类型"。
"""

# ExtractedEntity.entity_type → 中文展示名
ENTITY_TYPE_MAPPING: dict[str, str] = {
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
    "Numeric": "数值实体节点",
}

# Statement.emotion_type → 中文展示名
EMOTION_TYPE_MAPPING: dict[str, str] = {
    "joy": "愉快",
    "surprise": "惊喜",
    "sadness": "悲伤",
    "fear": "恐惧",
    "anger": "生气",
    "neutral": "中性",
}

# Statement.emotion_subject → 中文展示名
EMOTION_SUBJECT_MAPPING: dict[str, str] = {
    "self": "自己",
    "other": "别人",
    "object": "事物对象",
}

__all__ = [
    "EMOTION_SUBJECT_MAPPING",
    "EMOTION_TYPE_MAPPING",
    "ENTITY_TYPE_MAPPING",
]