"""模型提供商的内置连接配置。"""

from typing import Optional

from app.core.config import settings
from app.models.models_model import ModelProvider, ModelType


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

# 能力级公共端点：同一提供商按模型类型走不同公共地址时覆盖 provider 默认值。
# dashscope embedding/rerank 走原生 SDK（SDK 内部按 task 拼 /api/v1/services/...），
# llm 走 OpenAI 兼容模式（compatible-mode/v1），故仅此两项有差异。
_CAPABILITY_API_BASES = {
    (ModelProvider.DASHSCOPE.value, "embedding"): (
        "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding"
    ),
    (ModelProvider.DASHSCOPE.value, "rerank"): (
        "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
    ),
}


def is_local_deployment_provider(provider: ModelProvider | str) -> bool:
    """判断提供商是否必须使用用户部署的地址。"""
    provider_name = getattr(provider, "value", provider)
    return str(provider_name).lower() in _LOCAL_DEPLOYMENT_PROVIDERS


def uses_custom_api_base(provider: ModelProvider | str, model_type: str) -> bool:
    """判断该组合在运行时是否真正读取 api_base。

    仅 dashscope 的 embedding/rerank 走原生 SDK（endpoint 由 SDK 内部按 task 决定，
    填任何自定义地址都不会生效）；dashscope 的 llm 自兼容模式统一后读取 base_url。
    """
    provider_name = str(getattr(provider, "value", provider)).lower()
    type_name = str(getattr(model_type, "value", model_type)).lower()

    if provider_name == ModelProvider.DASHSCOPE.value:
        if type_name in ("embedding", "rerank"):
            return False
    return True


def validate_api_base_against_default(
    provider: ModelProvider | str,
    api_base: Optional[str],
    model_type: str,
) -> Optional[str]:
    """对运行时不读取 api_base 的组合，限制其只能留空或填官方公共端点。

    Returns:
        Optional[str]: None 表示通过；否则为可直接展示给用户的错误原因。
    """
    if uses_custom_api_base(provider, model_type):
        return None

    value = (api_base or "").strip()
    if not value:
        return None

    accepted = []
    for default in (
        get_default_provider_api_base(provider),
        get_default_provider_api_base(provider, model_type),
    ):
        if default and str(default) not in accepted:
            accepted.append(str(default))
    normalized = value.rstrip("/").lower()
    if any(normalized == item.rstrip("/").lower() for item in accepted):
        return None

    provider_name = getattr(provider, "value", provider)
    defaults = " 或 ".join(accepted) if accepted else "官方公共端点"
    return (
        f"{provider_name} 的 {model_type} 模型通过原生 SDK 调用，不会使用自定义 "
        f"API Base URL；请留空或填写官方公共端点 {defaults}"
    )


def get_default_provider_api_base(
    provider: ModelProvider | str, model_type: ModelType | str | None = None
) -> Optional[str]:
    """返回云端提供商的公共端点；本地提供商没有默认地址。

    model_type 给定时优先返回能力级公共端点（如 dashscope 的 embedding/rerank
    走原生 SDK 的 /api/v1/services/... 地址而非 compatible-mode）。
    """
    provider_name = str(getattr(provider, "value", provider)).lower()
    if model_type is not None:
        type_name = str(getattr(model_type, "value", model_type)).lower()
        capability_base = _CAPABILITY_API_BASES.get((provider_name, type_name))
        if capability_base:
            return capability_base
    if provider_name == ModelProvider.SPEEDBEAR.value:
        return f"{settings.SPEEDBEAR_BASE_URL.rstrip('/')}/api/v1"
    return _DEFAULT_API_BASES.get(provider_name)


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
