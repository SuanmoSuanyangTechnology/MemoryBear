"""模型提供商的内置连接配置。"""

from typing import Optional

from app.core.config import settings
from app.models.models_model import ModelProvider


_LOCAL_DEPLOYMENT_PROVIDERS = frozenset(
    {
        ModelProvider.OLLAMA.value,
        ModelProvider.XINFERENCE.value,
        ModelProvider.GPUSTACK.value,
    }
)

_DEFAULT_API_BASES = {
    ModelProvider.OPENAI.value: "https://api.openai.com/v1",
    ModelProvider.MINIMAX.value: "https://api.minimaxi.com/v1",
    ModelProvider.DASHSCOPE.value: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ModelProvider.BEDROCK.value: "us-east-1",
    ModelProvider.VOLCANO.value: "https://ark.cn-beijing.volces.com/api/v3",
}


def is_local_deployment_provider(provider: ModelProvider | str) -> bool:
    """判断提供商是否必须使用用户部署的地址。"""
    provider_name = getattr(provider, "value", provider)
    return str(provider_name).lower() in _LOCAL_DEPLOYMENT_PROVIDERS


def uses_custom_api_base(
    provider: ModelProvider | str, model_type: str, is_omni: bool = False
) -> bool:
    """判断该组合在运行时是否真正读取 api_base。

    部分组合走提供商原生 SDK（如 dashscope 的 embedding/rerank/非omni LLM），
    其 endpoint 由 SDK 内部决定，填任何自定义地址都不会生效。
    """
    provider_name = str(getattr(provider, "value", provider)).lower()
    type_name = str(model_type).lower()

    if provider_name == ModelProvider.DASHSCOPE.value:
        if type_name in ("embedding", "rerank"):
            return False
        if type_name in ("llm", "chat"):
            # 仅 omni 模型走 OpenAI 兼容模式并读取 base_url。
            return bool(is_omni)
    return True


def validate_api_base_against_default(
    provider: ModelProvider | str,
    api_base: Optional[str],
    model_type: str,
    is_omni: bool = False,
) -> Optional[str]:
    """对运行时不读取 api_base 的组合，限制其只能留空或填官方默认地址。

    Returns:
        Optional[str]: None 表示通过；否则为可直接展示给用户的错误原因。
    """
    if uses_custom_api_base(provider, model_type, is_omni):
        return None

    value = (api_base or "").strip()
    if not value:
        return None

    default = get_default_provider_api_base(provider)
    if default and value.rstrip("/").lower() == str(default).rstrip("/").lower():
        return None

    provider_name = getattr(provider, "value", provider)
    return (
        f"{provider_name} 的 {model_type} 模型通过原生 SDK 调用，不会使用自定义 "
        f"API Base URL；请留空或填写官方默认地址 {default}"
    )


def get_default_provider_api_base(provider: ModelProvider | str) -> Optional[str]:
    """返回云端提供商的内置 API Base；本地提供商没有默认地址。"""
    provider_name = getattr(provider, "value", provider)
    if provider_name == ModelProvider.SPEEDBEAR.value:
        return f"{settings.SPEEDBEAR_BASE_URL.rstrip('/')}/api/v1"
    return _DEFAULT_API_BASES.get(str(provider_name).lower())


def get_model_provider_metadata() -> list[dict[str, Optional[str]]]:
    """构建供模型管理 API 返回的提供商与默认地址列表。"""
    return [
        {
            "provider": provider.value,
            "default_api_base": (
                None
                if is_local_deployment_provider(provider)
                else get_default_provider_api_base(provider)
            ),
        }
        for provider in ModelProvider
        if provider != ModelProvider.COMPOSITE
    ]
