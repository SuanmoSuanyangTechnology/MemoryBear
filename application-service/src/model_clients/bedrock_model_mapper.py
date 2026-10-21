"""
AWS Bedrock model-name mapper

Automatically converts simplified model names to correct Bedrock Model IDs
"""
import logging

from typing import Optional

logger = logging.getLogger(__name__)

# Bedrock model-name mapping table
BEDROCK_MODEL_MAPPING = {
    # Claude 3.5 series
    "claude-3.5-sonnet": "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "claude-3-5-sonnet": "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "claude-sonnet-3.5": "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "claude-sonnet-3-5": "anthropic.claude-3-5-sonnet-20240620-v1:0",
    
    # Claude 3 series
    "claude-3-sonnet": "anthropic.claude-3-sonnet-20240229-v1:0",
    "claude-3-haiku": "anthropic.claude-3-haiku-20240307-v1:0",
    "claude-3-opus": "anthropic.claude-3-opus-20240229-v1:0",
    "claude-sonnet": "anthropic.claude-3-sonnet-20240229-v1:0",
    "claude-haiku": "anthropic.claude-3-haiku-20240307-v1:0",
    "claude-opus": "anthropic.claude-3-opus-20240229-v1:0",
    
    # Claude 2 series
    "claude-2": "anthropic.claude-v2",
    "claude-2.1": "anthropic.claude-v2:1",
    "claude-instant": "anthropic.claude-instant-v1",
    
    # Amazon Titan series
    "titan-text-express": "amazon.titan-text-express-v1",
    "titan-text-lite": "amazon.titan-text-lite-v1",
    "titan-embed-text": "amazon.titan-embed-text-v1",
    "titan-embed-image": "amazon.titan-embed-image-v1",
    
    # Meta Llama series
    "llama3-70b": "meta.llama3-70b-instruct-v1:0",
    "llama3-8b": "meta.llama3-8b-instruct-v1:0",
    "llama2-70b": "meta.llama2-70b-chat-v1",
    "llama2-13b": "meta.llama2-13b-chat-v1",
    
    # Mistral series
    "mistral-7b": "mistral.mistral-7b-instruct-v0:2",
    "mixtral-8x7b": "mistral.mixtral-8x7b-instruct-v0:1",
    "mistral-large": "mistral.mistral-large-2402-v1:0",
    
    # Mappings for commonly mistyped name formats
    "claude-sonnet-4-5": "anthropic.claude-3-5-sonnet-20240620-v1:0",  # Common mistake
    "claude-4-5-sonnet": "anthropic.claude-3-5-sonnet-20240620-v1:0",  # Common mistake
    "claude-sonnet-4.5": "anthropic.claude-3-5-sonnet-20240620-v1:0",  # Common mistake
}


def normalize_bedrock_model_id(model_name: str, region: Optional[str] = None) -> str:
    """
    Normalize a Bedrock model ID.

    Converts a simplified model name into the correct Bedrock Model ID format.

    Args:
        model_name: model name (possibly simplified or fully qualified)
        region: AWS region (optional, e.g. "us", "eu", "apac")

    Returns:
        str: the normalized Bedrock Model ID

    Examples:
        >>> normalize_bedrock_model_id("claude-sonnet-4-5")
        'anthropic.claude-3-5-sonnet-20240620-v1:0'
        
        >>> normalize_bedrock_model_id("claude-3.5-sonnet", region="eu")
        'eu.anthropic.claude-3-5-sonnet-20240620-v1:0'
        
        >>> normalize_bedrock_model_id("anthropic.claude-3-5-sonnet-20240620-v1:0")
        'anthropic.claude-3-5-sonnet-20240620-v1:0'
    """
    # Already in the correct format (contains a provider): return as-is
    if "." in model_name and not model_name.startswith(("us.", "eu.", "apac.", "sa.", "amer.", "global.", "us-gov.")):
        # Check whether the provider is valid
        provider = model_name.split(".", 1)[0]
        valid_providers = ["anthropic", "amazon", "meta", "mistral", "deepseek", "openai", "ai21", "cohere", "stability"]
        if provider in valid_providers:
            logger.debug(f"Model ID 已经是正确格式: {model_name}")
            return model_name
    
    # Strip the region prefix if present
    original_model_name = model_name
    region_prefix = None
    if model_name.startswith(("us.", "eu.", "apac.", "sa.", "amer.", "global.", "us-gov.")):
        parts = model_name.split(".", 1)
        region_prefix = parts[0]
        model_name = parts[1] if len(parts) > 1 else model_name
    
    # Lowercase for matching
    model_name_lower = model_name.lower()

    # Look up the mapping table
    if model_name_lower in BEDROCK_MODEL_MAPPING:
        mapped_id = BEDROCK_MODEL_MAPPING[model_name_lower]
        logger.info(f"映射模型名称: {original_model_name} -> {mapped_id}")
        
        # Prepend the region when given explicitly or when the original name had a prefix
        if region:
            mapped_id = f"{region}.{mapped_id}"
        elif region_prefix:
            mapped_id = f"{region_prefix}.{mapped_id}"
        
        return mapped_id
    
    # No mapping found: return the original name and log a warning
    logger.warning(
        f"未找到模型名称映射: {original_model_name}。"
        f"请确保使用正确的 Bedrock Model ID 格式，如 'anthropic.claude-3-5-sonnet-20240620-v1:0'"
    )
    return original_model_name


def is_bedrock_model_id(model_name: str) -> bool:
    """
    Check whether the name is in Bedrock Model ID format.

    Args:
        model_name: model name

    Returns:
        bool: whether it is in Bedrock Model ID format
    """
    # Strip the region prefix
    if model_name.startswith(("us.", "eu.", "apac.", "sa.", "amer.", "global.", "us-gov.")):
        model_name = model_name.split(".", 1)[1]

    # Check that a provider is present
    if "." not in model_name:
        return False
    
    provider = model_name.split(".", 1)[0]
    valid_providers = ["anthropic", "amazon", "meta", "mistral", "deepseek", "openai", "ai21", "cohere", "stability"]
    return provider in valid_providers


def get_provider_from_model_id(model_id: str) -> str:
    """
    Extract the provider from a Bedrock Model ID.

    Args:
        model_id: Bedrock Model ID

    Returns:
        str: provider name

    Examples:
        >>> get_provider_from_model_id("anthropic.claude-3-5-sonnet-20240620-v1:0")
        'anthropic'
        
        >>> get_provider_from_model_id("eu.anthropic.claude-3-5-sonnet-20240620-v1:0")
        'anthropic'
    """
    # Strip the region prefix
    if model_id.startswith(("us.", "eu.", "apac.", "sa.", "amer.", "global.", "us-gov.")):
        parts = model_id.split(".", 2)
        return parts[1] if len(parts) > 1 else model_id.split(".", 1)[0]
    
    return model_id.split(".", 1)[0]


# Helper for adding more mappings
def add_model_mapping(short_name: str, full_model_id: str) -> None:
    """
    Add a custom model-name mapping.

    Args:
        short_name: simplified model name
        full_model_id: full Bedrock Model ID
    """
    BEDROCK_MODEL_MAPPING[short_name.lower()] = full_model_id
    logger.info(f"添加模型映射: {short_name} -> {full_model_id}")


def get_all_mappings() -> dict:
    """
    Get all model-name mappings.

    Returns:
        dict: the model-name mapping dict
    """
    return BEDROCK_MODEL_MAPPING.copy()
