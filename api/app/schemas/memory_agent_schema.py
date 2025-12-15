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

class End_User_Information(BaseModel):
    end_user_name: str  # 这是要更新的用户名
    id: str  # 宿主ID，用于匹配条件
