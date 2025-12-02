"""API Key 工具函数"""
import secrets
import hashlib
from app.models.api_key_model import ApiKeyType


def generate_api_key(key_type: ApiKeyType) -> tuple[str, str, str]:
    """生成 API Key
    
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
        ApiKeyType.GENERAL: "sk-gen-",
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
    """验证 API Key
    
    Args:
        api_key: API Key 明文
        key_hash: 存储的哈希值
        
    Returns:
        bool: 是否匹配
    """
    return hash_api_key(api_key) == key_hash
