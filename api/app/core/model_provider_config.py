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

# 原生 SDK 基地址：dashscope embedding/rerank 的 SDK 会在 base_address 之后
# 自行拼接 /services/{task_group}/{task}/{function}，故用户只能给基地址；
# 完整服务端点会二次拼接服务路径（404），仅在此白名单中额外放行原生基地址。
_NATIVE_SDK_BASE_ADDRESSES = {
    ModelProvider.DASHSCOPE.value: "https://dashscope.aliyuncs.com/api/v1",
}

# 标准校验模型表（provider 级密钥保存时活体探测的锚点，spec D3）：
# 仅列低配、稳定在售的模型；上游退役报"模型不存在"时改这里一行即可（D7 不自动降级）。
# 表外 provider 走租户模型兜底（model_channel_service._resolve_validation_anchor）。
# 表项恒为 LLM（探测类型硬编码 ModelType.LLM.value；入表非 LLM 模型需同步改探测类型）。
_VALIDATION_MODELS = {
    ModelProvider.DASHSCOPE.value: "qwen3.5-flash",
    ModelProvider.OPENAI.value: "gpt-4o-mini",
}


def is_local_deployment_provider(provider: ModelProvider | str) -> bool:
    """判断提供商是否必须使用用户部署的地址。"""
    provider_name = getattr(provider, "value", provider)
    return str(provider_name).lower() in _LOCAL_DEPLOYMENT_PROVIDERS


def get_provider_validation_model(provider: ModelProvider | str) -> Optional[str]:
    """返回该 provider 的标准校验模型名；不在表内返回 None（走租户模型兜底）。"""
    provider_name = str(getattr(provider, "value", provider)).lower()
    return _VALIDATION_MODELS.get(provider_name)


def uses_custom_api_base(provider: ModelProvider | str, model_type: str) -> bool:
    """判断该组合在运行时是否真正读取自定义 api_base。

    仅 dashscope 的 embedding/rerank 走原生 SDK（base_address 之后由 SDK 按 task
    拼服务路径，只接受官方基地址）；dashscope 的 llm 自兼容模式统一后读取 base_url。
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
    """对运行时不读取自定义 api_base 的组合，限制其只能留空或填官方基地址。

    Returns:
        Optional[str]: None 表示通过；否则为可直接展示给用户的错误原因。
    """
    if uses_custom_api_base(provider, model_type):
        return None

    value = (api_base or "").strip()
    if not value:
        return None

    provider_name = str(getattr(provider, "value", provider)).lower()
    accepted = []
    for default in (
        get_default_provider_api_base(provider),
        get_default_provider_api_base(provider, model_type),
        _NATIVE_SDK_BASE_ADDRESSES.get(provider_name),
    ):
        if default and str(default) not in accepted:
            accepted.append(str(default))
    normalized = value.rstrip("/").lower()
    if any(normalized == item.rstrip("/").lower() for item in accepted):
        return None

    defaults = " 或 ".join(accepted) if accepted else "官方基地址"
    return (
        f"{provider_name} 的 {model_type} 模型通过原生 SDK 调用，不支持自定义 "
        f"API Base URL；请留空或填写官方基地址 {defaults}"
    )


def get_default_provider_api_base(
    provider: ModelProvider | str, model_type: ModelType | str | None = None
) -> Optional[str]:
    """返回云端提供商的公共基地址；本地提供商没有默认地址。

    model_type 保留仅为调用方兼容（dashscope 全类型统一 compatible-mode，原生
    SDK 组合运行时会剥离为 /api/v1 基地址）。
    """
    provider_name = str(getattr(provider, "value", provider)).lower()
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
