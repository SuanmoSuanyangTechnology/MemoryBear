from pydantic import BaseModel, EmailStr, field_serializer
from typing import Optional
import datetime
from app.core.utils.datetime_utils import to_timestamp_ms

class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    expires_at: datetime.datetime
    refresh_expires_at: datetime.datetime

    @field_serializer("expires_at", when_used="json")
    def _serialize_expires_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

    @field_serializer("refresh_expires_at", when_used="json")
    def _serialize_refresh_expires_at(self, dt: datetime.datetime):
        return to_timestamp_ms(dt)

class TokenData(BaseModel):
    userId: Optional[str] = None

class RefreshTokenRequest(BaseModel):
    refresh_token: str

class TokenRequest(BaseModel):
    email: EmailStr
    password: str
    invite: Optional[str] = None
    username: Optional[str] = None

