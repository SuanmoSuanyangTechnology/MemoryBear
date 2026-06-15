import logging
from typing import Any

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.models import RedBearLLM, RedBearModelConfig
from app.models.models_model import ModelApiKey, ModelConfig, ModelType
from app.schemas.knowledge_metadata_schema import MetadataAutoFilterModelParameters

logger = logging.getLogger(__name__)


class MetadataAutoFilterLLM:
    """RedBear LLM adapter for metadata auto filter extraction."""

    def __init__(self, llm: RedBearLLM):
        self._llm = llm

    @classmethod
    def from_model_config(
            cls,
            *,
            model_config: ModelConfig,
            api_key: ModelApiKey,
            model_parameters: MetadataAutoFilterModelParameters | None = None,
    ) -> "MetadataAutoFilterLLM":
        model_type = cls._coerce_model_type(getattr(model_config, "type", None))
        if model_type not in {ModelType.LLM, ModelType.CHAT}:
            raise BusinessException(
                "auto 元数据过滤模型仅支持 llm/chat 类型",
                code=BizCode.INVALID_PARAMETER,
            )

        redbear_config = RedBearModelConfig(
            model_name=api_key.model_name,
            provider=cls._enum_value(getattr(model_config, "provider", None) or api_key.provider),
            api_key=api_key.api_key,
            base_url=api_key.api_base,
            capability=list(getattr(model_config, "capability", None) or api_key.capability or []),
            is_omni=cls._resolve_is_omni(model_config, api_key),
            **cls._build_config_kwargs(model_parameters),
        )
        return cls(RedBearLLM(redbear_config, type=model_type))

    def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        prompt = f"{system_prompt}\n\n{user_prompt}"
        try:
            response = self._llm.invoke(prompt)
        except Exception:
            logger.exception("[MetadataAutoFilter] RedBearLLM invocation failed")
            return ""

        content = getattr(response, "content", response)
        return str(content or "").strip()

    @staticmethod
    def _build_config_kwargs(
            model_parameters: MetadataAutoFilterModelParameters | None,
    ) -> dict[str, Any]:
        params = model_parameters.model_dump(exclude_none=True) if model_parameters else {}
        raw_extra_params = params.pop("extra_params", {}) or {}
        extra_params = dict(raw_extra_params)
        extra_params.setdefault("temperature", 0)

        config_kwargs: dict[str, Any] = {"extra_params": extra_params}
        for key in ("deep_thinking", "json_output", "timeout", "max_retries", "concurrency"):
            if key in params:
                config_kwargs[key] = params[key]
        return config_kwargs

    @staticmethod
    def _coerce_model_type(value: Any) -> ModelType:
        if isinstance(value, ModelType):
            return value
        try:
            return ModelType(str(value))
        except ValueError as exc:
            raise BusinessException(
                "auto 元数据过滤模型仅支持 llm/chat 类型",
                code=BizCode.INVALID_PARAMETER,
            ) from exc

    @staticmethod
    def _enum_value(value: Any) -> Any:
        return value.value if hasattr(value, "value") else value

    @staticmethod
    def _resolve_is_omni(model_config: ModelConfig, api_key: ModelApiKey) -> bool:
        model_value = getattr(model_config, "is_omni", None)
        if model_value is not None:
            return bool(model_value)
        return bool(getattr(api_key, "is_omni", False))
