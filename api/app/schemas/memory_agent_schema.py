from typing import Optional

from pydantic import BaseModel


class UserInput(BaseModel):
    message: str
    history: list[dict]
    search_switch: str
    end_user_id: str
    config_id: Optional[str] = None


class Write_UserInput(BaseModel):
    messages: list[dict]
    end_user_id: str
    config_id: Optional[str] = None
