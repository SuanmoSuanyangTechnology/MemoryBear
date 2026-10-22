"""Pydantic-backed JSON column type.

Minimal closure of the monolith `app.base.type.PydanticType` (frozen copy: bind /
result semantics must stay identical to the host ORM).
"""

from pydantic import BaseModel
from sqlalchemy import JSON, TypeDecorator


class PydanticType(TypeDecorator):
    impl = JSON

    def __init__(self, pydantic_model: type[BaseModel]):
        super().__init__()
        self.model = pydantic_model

    def process_bind_param(self, value, dialect):
        # On write: model instance -> dict (a plain dict passes through).
        if value is None:
            return None
        if isinstance(value, self.model):
            return value.dict()
        return value

    def process_result_value(self, value, dialect):
        # On read: dict -> model instance (pydantic v2 validate).
        if value is None:
            return None
        return self.model.model_validate(value)
