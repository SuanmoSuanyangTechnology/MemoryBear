from typing import Optional

from pydantic import BaseModel


class UserInput(BaseModel):
    message: str
    history: list[dict]
    search_switch: str
    group_id: str
    config_id: Optional[str] = None


class Write_UserInput(BaseModel):
    message: str
    group_id: str
    config_id: Optional[str] = None
