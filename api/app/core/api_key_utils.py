"""API Key 工具函数"""
import secrets
import hashlib
from typing import Optional

from app.schemas.api_key_schema import ApiKeyType
from fastapi import Response
from fastapi.responses import JSONResponse


class ResourceType:
    """资源类型常量"""
    AGENT = "Agent"
    CLUSTER = "Cluster" 
    WORKFLOW = "Workflow"
    KNOWLEDGE = "Knowledge"
    MEMORY_ENGINE = "Memory_Engine"
    
    @classmethod
    def get_all_types(cls) -> list[str]:
        """获取所有支持的资源类型"""
        return [cls.AGENT, cls.CLUSTER, cls.WORKFLOW, cls.KNOWLEDGE, cls.MEMORY_ENGINE]
    
    @classmethod
    def is_valid_type(cls, resource_type: str) -> bool:
        """验证资源类型是否有效"""
        return resource_type in cls.get_all_types()


def generate_api_key(key_type: ApiKeyType) -> tuple[str, str, str]:
    """
    生成 API Key
    
    Args:
        key_type: API Key 类型
        
    Returns:
        tuple: (api_key, key_hash, key_prefix)
    """
    # 前缀映射
    prefix_map = {
        ApiKeyType.APP: "sk-app-",
        ApiKeyType.RAG: "sk-rag-",
        ApiKeyType.MEMORY: "sk-mem-",
    }

    prefix = prefix_map[key_type]
    random_string = secrets.token_urlsafe(32)[:32]  # 32 字符
    api_key = f"{prefix}{random_string}"

    # 生成哈希值存储
    key_hash = hash_api_key(api_key)

    return api_key, key_hash, prefix


def hash_api_key(api_key: str) -> str:
    """对 API Key 进行哈希
    
    Args:
        api_key: API Key 明文
        
    Returns:
        str: 哈希值
    """
    return hashlib.sha256(api_key.encode()).hexdigest()


def verify_api_key(api_key: str, key_hash: str) -> bool:
    """
    验证 API Key
    
    Args:
        api_key: API Key 明文
        key_hash: 存储的哈希值
        
    Returns:
        bool: 是否匹配
    """
    computed_hash = hash_api_key(api_key)
    return secrets.compare_digest(computed_hash, key_hash)


def validate_resource_binding(
    resource_type: Optional[str], 
    resource_id: Optional[str]
) -> tuple[bool, str]:
    """
    验证资源绑定的有效性
    
    Args:
        resource_type: 资源类型
        resource_id: 资源ID
        
    Returns:
        tuple: (是否有效, 错误信息)
    """
    # 如果都为空，表示不绑定资源，这是有效的
    if not resource_type and not resource_id:
        return True, ""
    
    # 如果只有一个为空，这是无效的
    if not resource_type or not resource_id:
        return False, "resource_type 和 resource_id 必须同时提供或同时为空"
    
    # 验证资源类型是否支持
    if not ResourceType.is_valid_type(resource_type):
        valid_types = ", ".join(ResourceType.get_all_types())
        return False, f"不支持的资源类型 '{resource_type}'，支持的类型：{valid_types}"
    
    return True, ""


def get_resource_scope_mapping() -> dict[str, list[str]]:
    """
    获取资源类型与权限范围的映射关系
    
    Returns:
        dict: 资源类型到推荐权限范围的映射
    """
    return {
        ResourceType.AGENT: [
            "app:all"
        ],
        ResourceType.CLUSTER: [
            "app:all"
        ],
        ResourceType.WORKFLOW: [
            "app:all"
        ],
        ResourceType.KNOWLEDGE: [
            "rag:search", "rag:upload", "rag:delete"
        ],
        ResourceType.MEMORY_ENGINE: [
            "memory:read", "memory:write", "memory:delete", "memory:search"
        ]
    }


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


