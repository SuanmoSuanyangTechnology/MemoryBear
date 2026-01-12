from pydantic import BaseModel, Field
from sqlalchemy import TypeDecorator, JSON


class PydanticType(TypeDecorator):
    impl = JSON

    def __init__(self, pydantic_model: type[BaseModel]):
        super().__init__()
        self.model = pydantic_model

    def process_bind_param(self, value, dialect):
        # 入库：Model -> dict
        if value is None:
            return None
        if isinstance(value, self.model):
            return value.dict()
        return value   # 已经是 dict 也放行

    def process_result_value(self, value, dialect):
        # 出库：dict -> Model
        if value is None:
            return None
        # return self.model.parse_obj(value)  # pydantic v1
        return self.model.model_validate(value)  # pydantic v2
