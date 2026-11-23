"""模型提供商的内置连接配置（迁自老单体 `app/core/model_provider_config.py`）。"""

from typing import Optional

from ..config import current_settings
from ..errors import BizCode, BusinessException
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

# 产品开放范围，按各模型类型列出可选供应商。
_PROVIDERS_BY_MODEL_TYPE = {
    ModelType.LLM: {
        ModelProvider.OPENAI, ModelProvider.SPEEDBEAR, ModelProvider.MINIMAX,
        ModelProvider.DASHSCOPE, ModelProvider.OLLAMA, ModelProvider.XINFERENCE,
        ModelProvider.GPUSTACK, ModelProvider.BEDROCK, ModelProvider.VOLCANO,
    },
    ModelType.EMBEDDING: {
        ModelProvider.OPENAI, ModelProvider.SPEEDBEAR, ModelProvider.DASHSCOPE,
        ModelProvider.OLLAMA, ModelProvider.XINFERENCE, ModelProvider.GPUSTACK,
        ModelProvider.BEDROCK, ModelProvider.VOLCANO,
    },
    ModelType.RERANK: {
        ModelProvider.SPEEDBEAR, ModelProvider.DASHSCOPE,
        ModelProvider.XINFERENCE, ModelProvider.GPUSTACK, ModelProvider.BEDROCK,
    },
    ModelType.ASR: {ModelProvider.DASHSCOPE},
    ModelType.IMAGE: {ModelProvider.VOLCANO},
    ModelType.VIDEO: {ModelProvider.VOLCANO},
}


def _provider_names_by_model_type(model_type: ModelType | str) -> list[str]:
    """该类型支持的全部供应商名（稳定序，用于错误文案与前端下拉）。"""
    try:
        resolved = ModelType(_provider_name(model_type))
    except ValueError:
        return []
    return sorted(
        provider.value for provider in _PROVIDERS_BY_MODEL_TYPE.get(resolved, ())
    )


def _provider_name(value: ModelProvider | str | None) -> str:
    return str(getattr(value, "value", value)).lower()


def validate_provider_supports_model_type(
    provider: ModelProvider | str, model_type: ModelType | str | None
) -> Optional[str]:
    """校验 provider × model_type 组合是否真有运行时适配器。

    返回 None 表示通过；否则为可直接展示给用户的错误原因。

    这张表此前只服务于 `GET /models/provider?model_type=` 的前端下拉过滤，写路径
    完全不看它，于是「前端点不到、但 API 能提交」的组合会一路走到活体验证，最后
    被兜底成「未知错误：调用模型服务失败」——用户看不出是自己选错了组合。校验放
    在写路径前，错误才可归因。

    组合模型（COMPOSITE）不在此表内：它由成员解析运行，允许任意 type，调用方跳过。
    """
    provider_name = _provider_name(provider)
    try:
        resolved_type = ModelType(_provider_name(model_type))
    except ValueError:
        return f"不支持的模型类型: {model_type}"

    supported = _PROVIDERS_BY_MODEL_TYPE.get(resolved_type, set())
    if any(provider_name == item.value for item in supported):
        return None

    names = _provider_names_by_model_type(resolved_type)
    if not names:
        return f"模型类型 {resolved_type.value} 暂无可用的供应商适配"
    return (
        f"供应商 {provider_name} 不支持 {resolved_type.value} 类型模型；"
        f"当前支持 {resolved_type.value} 的供应商：{'、'.join(names)}"
    )


def require_provider_supports_model_type(
    provider: ModelProvider | str, model_type: ModelType | str | None
) -> None:
    """拒绝没有运行时适配器的组合，组合模型由成员解析运行。"""
    if _provider_name(provider) == ModelProvider.COMPOSITE.value:
        return
    reason = validate_provider_supports_model_type(provider, model_type)
    if reason is not None:
        raise BusinessException(reason, BizCode.INVALID_PARAMETER)


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
        return f"{current_settings().speedbear_base_url.rstrip('/')}/api/v1"
    return _DEFAULT_API_BASES.get(provider_name)


def get_model_provider_metadata(
    model_type: ModelType | None = None,
) -> list[dict[str, Optional[str]]]:
    """构建供模型管理 API 返回的提供商与默认地址列表。"""
    supported = _PROVIDERS_BY_MODEL_TYPE[model_type] if model_type is not None else None
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
        if provider != ModelProvider.COMPOSITE and (supported is None or provider in supported)
    ]


__all__ = [
    "get_default_provider_api_base",
    "get_model_provider_metadata",
    "get_provider_validation_model",
    "is_local_deployment_provider",
    "require_provider_supports_model_type",
    "uses_custom_api_base",
    "validate_api_base_against_default",
    "validate_provider_supports_model_type",
]
