"""multi_agent_configs 只读映射（平台代管模型删除前的引用面检查用）。"""

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import UUID

from ..base import ReadOnlyBase


class MultiAgentConfig(ReadOnlyBase):
    __tablename__ = "multi_agent_configs"

    id = Column(UUID(as_uuid=True), primary_key=True)
    app_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    default_model_config_id = Column(UUID(as_uuid=True), nullable=True, index=True)
