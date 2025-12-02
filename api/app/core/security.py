from datetime import datetime, timedelta, timezone
from typing import Any, Union, Optional
import uuid

from jose import jwt, JWTError
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["scrypt"], deprecated="auto")


def create_access_token(subject: Union[str, Any], expires_delta: timedelta = None) -> tuple[str, str]:
    """创建访问token
    
    Args:
        subject: token主体（通常是用户名）
        expires_delta: 过期时间间隔
        
    Returns:
        tuple: (token字符串, token_id)
    """
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    
    token_id = str(uuid.uuid4())
    to_encode = {
        "exp": expire,
        "iat": now,  # Issued at time
        "sub": str(subject), 
        "type": "access",
        "jti": token_id  # JWT ID
    }
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt, token_id


def create_refresh_token(subject: Union[str, Any], expires_delta: timedelta = None) -> tuple[str, str]:
    """创建刷新token
    
    Args:
        subject: token主体（通常是用户名）
        expires_delta: 过期时间间隔
        
    Returns:
        tuple: (token字符串, token_id)
    """
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(
            days=settings.REFRESH_TOKEN_EXPIRE_DAYS
        )
    
    token_id = str(uuid.uuid4())
    to_encode = {
        "exp": expire,
        "iat": now,  # Issued at time
        "sub": str(subject), 
        "type": "refresh",
        "jti": token_id  # JWT ID
    }
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt, token_id


def verify_token(token: str, token_type: str = "access") -> Union[str, None]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        userId: str = payload.get("sub")
        token_type_in_payload: str = payload.get("type")
        
        if userId is None or token_type_in_payload != token_type:
            return None
        return userId
    except JWTError:
        return None


def get_token_id(token: str) -> Optional[str]:
    """从token中提取token ID
    
    Args:
        token: JWT token字符串
        
    Returns:
        token ID或None
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload.get("jti")
    except JWTError:
        return None


def get_token_expiry(token: str) -> Optional[datetime]:
    """从token中提取过期时间
    
    Args:
        token: JWT token字符串
        
    Returns:
        过期时间或None
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        exp_timestamp = payload.get("exp")
        if exp_timestamp:
            return datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
        return None
    except JWTError:
        return None


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)
