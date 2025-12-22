"""API Key 工具函数"""
import secrets
from typing import Optional, Union
from datetime import datetime

from app.schemas.api_key_schema import ApiKeyType
from fastapi import Response
from fastapi.responses import JSONResponse


def generate_api_key(key_type: ApiKeyType) -> str:
    """
    生成 API Key
    
    Args:
        key_type: API Key 类型
        
    Returns:
        str: api_key
    """
    # 前缀映射
    prefix_map = {
        ApiKeyType.AGENT: "sk-agent-",
        ApiKeyType.CLUSTER: "sk-multi_agent-",
        ApiKeyType.WORKFLOW: "sk-workflow-",
        ApiKeyType.SERVICE: "sk-service-"
    }

    prefix = prefix_map[key_type]
    random_string = secrets.token_urlsafe(32)[:32]  # 32 字符
    api_key = f"{prefix}{random_string}"

    return api_key


def add_rate_limit_headers(response, headers: dict):
    """统一添加限流响应头"""
    if isinstance(response, Response):
        for key, value in headers.items():
            response.headers[key] = value
    elif isinstance(response, JSONResponse):
        for key, value in headers.items():
            response.headers[key] = value
    elif hasattr(response, 'headers'):
        response.headers.update(headers)

    return response


def timestamp_to_datetime(timestamp: Optional[Union[int, float]]) -> Optional[datetime]:
    """将时间戳转换为datetime对象"""
    if timestamp is None:
        return None

    # 处理毫秒级时间戳
    if timestamp > 1e10:
        timestamp = timestamp / 1000

    return datetime.fromtimestamp(timestamp)


def datetime_to_timestamp(dt: Optional[datetime]) -> Optional[int]:
    """将datetime对象转换为时间戳（毫秒）"""
    if dt is None:
        return None

    return int(dt.timestamp() * 1000)
