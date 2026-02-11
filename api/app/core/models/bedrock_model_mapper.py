"""
AWS Bedrock 模型名称映射器

将简化的模型名称自动转换为正确的 Bedrock Model ID
"""
from typing import Optional
from app.core.logging_config import get_business_logger

logger = get_business_logger()

# Bedrock 模型名称映射表
BEDROCK_MODEL_MAPPING = {
    # Claude 3.5 系列
    "claude-3.5-sonnet": "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "claude-3-5-sonnet": "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "claude-sonnet-3.5": "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "claude-sonnet-3-5": "anthropic.claude-3-5-sonnet-20240620-v1:0",
    
    # Claude 3 系列
    "claude-3-sonnet": "anthropic.claude-3-sonnet-20240229-v1:0",
    "claude-3-haiku": "anthropic.claude-3-haiku-20240307-v1:0",
    "claude-3-opus": "anthropic.claude-3-opus-20240229-v1:0",
    "claude-sonnet": "anthropic.claude-3-sonnet-20240229-v1:0",
    "claude-haiku": "anthropic.claude-3-haiku-20240307-v1:0",
    "claude-opus": "anthropic.claude-3-opus-20240229-v1:0",
    
    # Claude 2 系列
    "claude-2": "anthropic.claude-v2",
    "claude-2.1": "anthropic.claude-v2:1",
    "claude-instant": "anthropic.claude-instant-v1",
    
    # Amazon Titan 系列
    "titan-text-express": "amazon.titan-text-express-v1",
    "titan-text-lite": "amazon.titan-text-lite-v1",
    "titan-embed-text": "amazon.titan-embed-text-v1",
    "titan-embed-image": "amazon.titan-embed-image-v1",
    
    # Meta Llama 系列
    "llama3-70b": "meta.llama3-70b-instruct-v1:0",
    "llama3-8b": "meta.llama3-8b-instruct-v1:0",
    "llama2-70b": "meta.llama2-70b-chat-v1",
    "llama2-13b": "meta.llama2-13b-chat-v1",
    
    # Mistral 系列
    "mistral-7b": "mistral.mistral-7b-instruct-v0:2",
    "mixtral-8x7b": "mistral.mixtral-8x7b-instruct-v0:1",
    "mistral-large": "mistral.mistral-large-2402-v1:0",
    
    # 常见错误格式的映射
    "claude-sonnet-4-5": "anthropic.claude-3-5-sonnet-20240620-v1:0",  # 常见错误
    "claude-4-5-sonnet": "anthropic.claude-3-5-sonnet-20240620-v1:0",  # 常见错误
    "claude-sonnet-4.5": "anthropic.claude-3-5-sonnet-20240620-v1:0",  # 常见错误
}


def normalize_bedrock_model_id(model_name: str, region: Optional[str] = None) -> str:
    """
    标准化 Bedrock 模型 ID
    
    将简化的模型名称转换为正确的 Bedrock Model ID 格式
    
    Args:
        model_name: 模型名称（可能是简化格式或完整格式）
        region: AWS 区域（可选，如 "us", "eu", "apac"）
        
    Returns:
        str: 标准化的 Bedrock Model ID
        
    Examples:
        >>> normalize_bedrock_model_id("claude-sonnet-4-5")
        'anthropic.claude-3-5-sonnet-20240620-v1:0'
        
        >>> normalize_bedrock_model_id("claude-3.5-sonnet", region="eu")
        'eu.anthropic.claude-3-5-sonnet-20240620-v1:0'
        
        >>> normalize_bedrock_model_id("anthropic.claude-3-5-sonnet-20240620-v1:0")
        'anthropic.claude-3-5-sonnet-20240620-v1:0'
    """
    # 如果已经是正确的格式（包含 provider），直接返回
    if "." in model_name and not model_name.startswith(("us.", "eu.", "apac.", "sa.", "amer.", "global.", "us-gov.")):
        # 检查是否是有效的 provider
        provider = model_name.split(".", 1)[0]
        valid_providers = ["anthropic", "amazon", "meta", "mistral", "deepseek", "openai", "ai21", "cohere", "stability"]
        if provider in valid_providers:
            logger.debug(f"Model ID 已经是正确格式: {model_name}")
            return model_name
    
    # 移除区域前缀（如果存在）
    original_model_name = model_name
    region_prefix = None
    if model_name.startswith(("us.", "eu.", "apac.", "sa.", "amer.", "global.", "us-gov.")):
        parts = model_name.split(".", 1)
        region_prefix = parts[0]
        model_name = parts[1] if len(parts) > 1 else model_name
    
    # 转换为小写进行匹配
    model_name_lower = model_name.lower()
    
    # 尝试从映射表中查找
    if model_name_lower in BEDROCK_MODEL_MAPPING:
        mapped_id = BEDROCK_MODEL_MAPPING[model_name_lower]
        logger.info(f"映射模型名称: {original_model_name} -> {mapped_id}")
        
        # 如果指定了区域或原始名称包含区域前缀，添加区域前缀
        if region:
            mapped_id = f"{region}.{mapped_id}"
        elif region_prefix:
            mapped_id = f"{region_prefix}.{mapped_id}"
        
        return mapped_id
    
    # 如果没有找到映射，返回原始名称并记录警告
    logger.warning(
        f"未找到模型名称映射: {original_model_name}。"
        f"请确保使用正确的 Bedrock Model ID 格式，如 'anthropic.claude-3-5-sonnet-20240620-v1:0'"
    )
    return original_model_name


def is_bedrock_model_id(model_name: str) -> bool:
    """
    检查是否是 Bedrock Model ID 格式
    
    Args:
        model_name: 模型名称
        
    Returns:
        bool: 是否是 Bedrock Model ID 格式
    """
    # 移除区域前缀
    if model_name.startswith(("us.", "eu.", "apac.", "sa.", "amer.", "global.", "us-gov.")):
        model_name = model_name.split(".", 1)[1]
    
    # 检查是否包含 provider
    if "." not in model_name:
        return False
    
    provider = model_name.split(".", 1)[0]
    valid_providers = ["anthropic", "amazon", "meta", "mistral", "deepseek", "openai", "ai21", "cohere", "stability"]
    return provider in valid_providers


def get_provider_from_model_id(model_id: str) -> str:
    """
    从 Bedrock Model ID 中提取 provider
    
    Args:
        model_id: Bedrock Model ID
        
    Returns:
        str: Provider 名称
        
    Examples:
        >>> get_provider_from_model_id("anthropic.claude-3-5-sonnet-20240620-v1:0")
        'anthropic'
        
        >>> get_provider_from_model_id("eu.anthropic.claude-3-5-sonnet-20240620-v1:0")
        'anthropic'
    """
    # 移除区域前缀
    if model_id.startswith(("us.", "eu.", "apac.", "sa.", "amer.", "global.", "us-gov.")):
        parts = model_id.split(".", 2)
        return parts[1] if len(parts) > 1 else model_id.split(".", 1)[0]
    
    return model_id.split(".", 1)[0]


# 添加更多映射的辅助函数
def add_model_mapping(short_name: str, full_model_id: str) -> None:
    """
    添加自定义模型名称映射
    
    Args:
        short_name: 简化的模型名称
        full_model_id: 完整的 Bedrock Model ID
    """
    BEDROCK_MODEL_MAPPING[short_name.lower()] = full_model_id
    logger.info(f"添加模型映射: {short_name} -> {full_model_id}")


def get_all_mappings() -> dict:
    """
    获取所有模型名称映射
    
    Returns:
        dict: 模型名称映射字典
    """
    return BEDROCK_MODEL_MAPPING.copy()
