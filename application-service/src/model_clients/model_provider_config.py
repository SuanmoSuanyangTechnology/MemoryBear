"""Built-in connection configuration for model providers."""

from typing import Optional

from ..config import current_settings
from ..models.models_model import ModelProvider, ModelType


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

# Native SDK base addresses: the dashscope embedding/rerank SDK appends
# /services/{task_group}/{task}/{function} after base_address itself, so users may only
# supply the base address; a full service endpoint would be joined twice (404), so the
# native base address is whitelisted here as an extra allowance.
_NATIVE_SDK_BASE_ADDRESSES = {
    ModelProvider.DASHSCOPE.value: "https://dashscope.aliyuncs.com/api/v1",
}

# Standard validation-model table (the live-probe anchor when saving a provider-level key):
# only low-tier, stably sold models are listed; when upstream retires one and reports
# "model not found", changing one line here suffices (no automatic downgrade).
# Providers outside the table fall back to the tenant model
# (model_channel_service._resolve_validation_anchor).
# Entries are always LLMs (the probe type is hardcoded to ModelType.LLM.value; adding a
# non-LLM model requires changing the probe type accordingly).
_VALIDATION_MODELS = {
    ModelProvider.DASHSCOPE.value: "qwen3.5-flash",
    ModelProvider.OPENAI.value: "gpt-4o-mini",
}


def is_local_deployment_provider(provider: ModelProvider | str) -> bool:
    """Return whether the provider requires a user-deployed address."""
    provider_name = getattr(provider, "value", provider)
    return str(provider_name).lower() in _LOCAL_DEPLOYMENT_PROVIDERS


def get_provider_validation_model(provider: ModelProvider | str) -> Optional[str]:
    """Return the standard validation model name for the provider; None when outside the table (tenant-model fallback)."""
    provider_name = str(getattr(provider, "value", provider)).lower()
    return _VALIDATION_MODELS.get(provider_name)


def uses_custom_api_base(provider: ModelProvider | str, model_type: str) -> bool:
    """Return whether the provider/type combination actually reads a custom api_base.

    Only dashscope embedding/rerank use the native SDK (after base_address the SDK appends
    the service path by task, so only the official base address is accepted); dashscope llm
    reads base_url after the unification on compatible mode.
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
    """For combinations that ignore a custom api_base, allow only empty or the official base address.

    Returns:
        Optional[str]: None means accepted; otherwise an error reason safe to show to users.
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
    """Return the public base address for cloud providers; local providers have no default.

    model_type is kept only for caller compatibility (dashscope unifies all types on
    compatible-mode; native-SDK combinations are stripped to the /api/v1 base address at
    runtime).
    """
    provider_name = str(getattr(provider, "value", provider)).lower()
    if provider_name == ModelProvider.SPEEDBEAR.value:
        return f"{current_settings().speedbear_base_url.rstrip('/')}/api/v1"
    return _DEFAULT_API_BASES.get(provider_name)


def get_model_provider_metadata() -> list[dict[str, Optional[str]]]:
    """Build the provider + default-address list returned by the model management API."""
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
