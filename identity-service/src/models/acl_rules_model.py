from sqlalchemy import CheckConstraint, Column, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.types import TIMESTAMP

from src.models.base import ServiceBase


class AclRule(ServiceBase):
    __tablename__ = "acl_rules"
    __table_args__ = (CheckConstraint("effect IN ('allow', 'deny')", name="ck_acl_rules_effect"),)

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    caller_service = Column(Text, nullable=False)
    target_service = Column(Text, nullable=False)
    endpoint = Column(Text, nullable=False)
    effect = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))
    updated_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))
