# -*- coding: utf-8 -*-
"""对话情绪原始明细 ORM 模型

数据源为 Neo4j Dialogue 节点的 emotion 字段（BERT 十分类），
由 Celery 任务/全量脚本按对话粒度（一条对话一行）Upsert 到本表。

存储口径：
- 只存对话原始时刻 created_at（naive UTC，不预切日）+ emotion；
- 切日/聚合不下沉到存储，由查询接口按请求头 X-Timezone
  用 (created_at AT TIME ZONE 'UTC' AT TIME ZONE :tz)::date 实时完成，
  一次同步支持任意时区。
"""

from sqlalchemy import Column, DateTime, Index, String
from sqlalchemy.dialects.postgresql import UUID

from app.db import Base


class DialogueEmotionRaw(Base):
    """对话情绪原始明细表（一对话一行）"""

    __tablename__ = "dialogue_emotion_raw"

    id = Column(UUID(as_uuid=True), primary_key=True, comment="主键（由 dialogue_id 经 uuid5 确定性生成，幂等 Upsert 依据）")
    dialogue_id = Column(String(255), nullable=False, unique=True, comment="Neo4j Dialogue.id 原始字符串（格式如 Dialog_<uuid>_<n>，非纯 uuid）")
    end_user_id = Column(UUID(as_uuid=True), nullable=False, comment="终端用户ID")
    created_at = Column(DateTime, nullable=False, comment="对话原始时刻（naive UTC，不预切日）")
    emotion = Column(String(50), nullable=False, comment="情绪枚举（BERT 十分类英文 code）")

    __table_args__ = (
        Index("idx_dialogue_emotion_raw_user_time", "end_user_id", "created_at"),
    )

    def __repr__(self):
        return f"<DialogueEmotionRaw(id={self.id}, end_user_id={self.end_user_id}, created_at={self.created_at})>"
