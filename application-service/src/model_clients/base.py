from __future__ import annotations

import logging

import os
import threading
from collections.abc import Mapping
from typing import Any, ClassVar, Dict, List, Optional, TypeVar

import httpx
from langchain_aws import ChatBedrock
from langchain_core.language_models import BaseLLM
from langchain_ollama import OllamaLLM
from langchain_openai import ChatOpenAI, OpenAI
from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator

from redbear_model import FailoverPlan, legacy_capability_columns

from ..errors import BizCode
from ..errors import BusinessException
from .model_provider_config import (
    get_default_provider_api_base,
    is_local_deployment_provider,
)
from ..models.models_model import ModelFeature, ModelProvider, ModelType
from .compatible_chat import CompatibleChatOpenAI

T = TypeVar("T")
_OPENAI_SYNC_CLIENTS: dict[
    tuple[float | None, float | None, float | None, float | None],
    httpx.Client,
] = {}
_OPENAI_HTTP_CLIENTS_LOCK = threading.Lock()
_OPENAI_ASYNC_CLIENTS_LOCAL = threading.local()


def _get_shared_openai_clients(
        timeout_config: httpx.Timeout,
) -> tuple[httpx.Client, httpx.AsyncClient]:
    key = (
        getattr(timeout_config, "connect", None),
        getattr(timeout_config, "read", None),
        getattr(timeout_config, "write", None),
        getattr(timeout_config, "pool", None),
    )
    limits = httpx.Limits(
        max_connections=int(os.getenv("LLM_HTTP_MAX_CONNECTIONS", "300")),
        max_keepalive_connections=int(os.getenv("LLM_HTTP_MAX_KEEPALIVE", "50")),
    )

    with _OPENAI_HTTP_CLIENTS_LOCK:
        sync_client = _OPENAI_SYNC_CLIENTS.get(key)
        if sync_client is None:
            sync_client = httpx.Client(
                timeout=timeout_config, limits=limits, follow_redirects=True
            )
            _OPENAI_SYNC_CLIENTS[key] = sync_client

    async_clients = getattr(_OPENAI_ASYNC_CLIENTS_LOCAL, "clients", None)
    if async_clients is None:
        async_clients = {}
        _OPENAI_ASYNC_CLIENTS_LOCAL.clients = async_clients
    async_client = async_clients.get(key)
    if async_client is None:
        async_client = httpx.AsyncClient(
            timeout=timeout_config, limits=limits, follow_redirects=True
        )
        async_clients[key] = async_client

    return sync_client, async_client


def _shell_field(obj: Any, *names: str) -> Any:
    """Read a field from the runtime key shell (attribute object or dict snapshot); first present name wins."""
    for name in names:
        if isinstance(obj, Mapping):
            if name in obj:
                return obj[name]
        else:
            value = getattr(obj, name, None)
            if value is not None:
                return value
    return None


class RedBearModelConfig(BaseModel):
    """Model config base class."""
    model_name: str
    provider: str
    api_key: str
    base_url: Optional[str] = None
    # Capability carrier (contract v2 three columns): features drive thinking/json_output
    # toggles; the modality columns travel along for multimodal checks
    input_modalities: List[str] = Field(default_factory=list)
    output_modalities: List[str] = Field(default_factory=list)
    features: List[str] = Field(default_factory=list)
    deep_thinking: bool = False  # Whether deep thinking mode is enabled
    thinking_budget_tokens: Optional[int] = None  # Deep-thinking token budget
    json_output: bool = False  # Whether JSON output is forced
    # Request timeout (seconds) - defaults to 120s for complex LLM calls; configurable
    # via the LLM_TIMEOUT env var
    timeout: float = Field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT", "120.0")))
    # Max retries - defaults to 2 to avoid overly long waits; configurable via the
    # LLM_MAX_RETRIES env var
    max_retries: int = Field(default_factory=lambda: int(os.getenv("LLM_MAX_RETRIES", "2")))
    concurrency: int = 5  # Concurrency limit
    extra_params: Dict[str, Any] = {}
    # Usage-event attribution: the central builder fills these from resolved results and
    # business call sites never see them; when tenant/model_config are missing the usage
    # event is skipped, a missing channel is recorded as NULL
    tenant_id: Optional[str] = None
    model_config_id: Optional[str] = None
    channel_id: Optional[str] = None

    # In-request channel failover plan: a private attribute, kept out of
    # model_fields/model_dump/repr; passed through by from_api_key from the runtime key
    # shell (missing key -> None, legacy-shell compatible)
    _failover_plan: FailoverPlan | None = PrivateAttr(default=None)

    @property
    def failover_plan(self) -> FailoverPlan | None:
        return self._failover_plan

    def bind_failover_plan(self, plan: FailoverPlan | None) -> "RedBearModelConfig":
        """Attach the in-request failover plan on direct-construction paths (e.g. LangChainAgent); same semantics as the from_api_key shell pass-through."""
        self._failover_plan = plan
        return self

    @field_validator("tenant_id", "model_config_id", "channel_id", mode="before")
    @classmethod
    def _coerce_attribution_id(cls, value: Any) -> Any:
        if value is None or isinstance(value, str):
            return value
        return str(value)

    EXTRA_PARAMS_FIELD_MAP: ClassVar[dict] = {
        "deep_thinking": "deep_thinking",
        "thinking_budget_tokens": "thinking_budget_tokens",
        "json_output": "json_output",
        "streaming": None,
        "enable_search": None,
        "enable_thinking": None,
        "response_format": None,
    }

    @model_validator(mode="before")
    @classmethod
    def _lift_config_from_extra_params(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        extra_params = data.get("extra_params", {})
        if not extra_params:
            return data
        for param_key, field_name in cls.EXTRA_PARAMS_FIELD_MAP.items():
            if param_key in extra_params and field_name is not None:
                if field_name not in data:
                    data[field_name] = extra_params[param_key]
        return data

    @model_validator(mode="after")
    def _resolve_default_base_url(self) -> "RedBearModelConfig":
        """Fill in the cloud default base URL and reject an empty base URL for local providers."""
        if isinstance(self.base_url, str):
            self.base_url = self.base_url.strip()

        if not self.base_url:
            if is_local_deployment_provider(self.provider):
                raise ValueError(
                    f"本地部署提供商 {self.provider} 必须配置 API Base URL"
                )
            self.base_url = get_default_provider_api_base(self.provider)
        return self

    @model_validator(mode="after")
    def _resolve_features(self) -> "RedBearModelConfig":
        logger = logging.getLogger(__name__)

        has_thinking = ModelFeature.THINKING in self.features
        has_thinking_only = ModelFeature.THINKING_ONLY in self.features
        supports_json_output = ModelFeature.JSON_OUTPUT in self.features

        if self.deep_thinking and not has_thinking and not has_thinking_only:
            logger.warning(
                f"模型 {self.model_name} 不支持深度思考（features 中无 'thinking'/'thinking_only'），已自动关闭 deep_thinking"
            )
            self.deep_thinking = False
            self.thinking_budget_tokens = None

        if not self.deep_thinking and self.thinking_budget_tokens is not None:
            logger.warning(
                f"模型 {self.model_name} 未启用深度思考，已自动清除 thinking_budget_tokens"
            )
            self.thinking_budget_tokens = None

        # thinking_only models are always in a thinking state; deep_thinking is forced to True
        if has_thinking_only:
            self.deep_thinking = True
            # thinking_only models do not support the thinking_budget_tokens param;
            # clear it to avoid passing it by mistake
            self.thinking_budget_tokens = None

        # thinking_only models do not support json_output; the conflict makes the model
        # emit malformed output (e.g. "[1]")
        if self.json_output and has_thinking_only:
            logger.warning(
                f"模型 {self.model_name} 为 thinking_only 类型，不支持 json_output，已自动关闭 json_output"
            )
            self.json_output = False

        if self.json_output and not supports_json_output:
            logger.warning(
                f"模型 {self.model_name} 不支持 JSON 输出（features 中无 'json_output'），已自动关闭 json_output"
            )
            self.json_output = False
        return self

    @classmethod
    def from_api_key(cls, api_key_obj: Any, **overrides: Any) -> "RedBearModelConfig":
        """Runtime key shell -> model config (usage-attribution triple carried along).

        The shell may be a runtime ModelApiKey, ModelInfo or snapshot from the
        resolved shell / speedbear / legacy paths, or a dict snapshot; `overrides`
        holds call-site-specific params (timeout/max_retries/extra_params etc.)
        and wins on name conflicts. A missing tenant/model_config/channel is None
        (usage events skipped/NULL, same semantics as the central builder);
        failover_plan is carried along too (legacy shells / lost copies -> None,
        degrading to a single candidate).

        Capabilities come from the contract v2 three columns; legacy shells (dict
        snapshots without the three columns / queued Celery messages) fall back to
        conversion from the legacy `capability`/`is_omni` columns (deprecated;
        to be removed).
        """
        raw_input = _shell_field(api_key_obj, "input_modalities")
        if raw_input is None:
            legacy_input, legacy_output, legacy_features = legacy_capability_columns(
                type=ModelType.LLM.value,
                provider=str(_shell_field(api_key_obj, "provider") or ""),
                capabilities=tuple(_shell_field(api_key_obj, "capability") or ()),
                is_omni=bool(_shell_field(api_key_obj, "is_omni")),
            )
            raw_input, raw_output, raw_features = legacy_input, legacy_output, legacy_features
        else:
            raw_output = _shell_field(api_key_obj, "output_modalities") or ()
            raw_features = _shell_field(api_key_obj, "features") or ()
        data: Dict[str, Any] = {
            "model_name": _shell_field(api_key_obj, "model_name"),
            "provider": _shell_field(api_key_obj, "provider"),
            "api_key": _shell_field(api_key_obj, "api_key"),
            "base_url": _shell_field(api_key_obj, "api_base", "base_url") or None,
            "input_modalities": [str(item) for item in raw_input],
            "output_modalities": [str(item) for item in raw_output],
            "features": [str(item) for item in raw_features],
            "tenant_id": _shell_field(api_key_obj, "tenant_id"),
            "model_config_id": _shell_field(api_key_obj, "model_config_id"),
            "channel_id": _shell_field(api_key_obj, "channel_id"),
        }
        missing = [
            name for name in ("model_name", "provider", "api_key") if data[name] is None
        ]
        if missing:
            raise ValueError(f"from_api_key: 壳缺少必需字段 {', '.join(missing)}")
        data["provider"] = str(data["provider"])
        data.update(overrides)
        config = cls(**data)
        config._failover_plan = _shell_field(api_key_obj, "failover_plan")
        return config


def _map_budget_to_reasoning_effort(budget_tokens: Optional[int]) -> Optional[str]:
    if budget_tokens is None:
        return None
    if budget_tokens <= 2048:
        return "low"
    elif budget_tokens <= 4096:
        return "medium"
    else:
        return "high"


def _json_response_format(config: RedBearModelConfig) -> dict[str, Any]:
    response_format = config.extra_params.get("response_format")
    if isinstance(response_format, dict):
        return response_format
    return {"type": "json_object"}


def _should_send_response_format(config: RedBearModelConfig) -> bool:
    return config.json_output or isinstance(config.extra_params.get("response_format"), dict)


class RedBearModelFactory:
    """Model factory class."""

    _CONFIG_ONLY_KEYS = {
        "deep_thinking", "thinking_budget_tokens",
        "enable_search", "enable_thinking", "response_format", "json_output",
        "default_headers",
    }

    @staticmethod
    def _extract_provider_specific_params(extra_params: Dict[str, Any]) -> tuple[Dict[str, Any], dict[str, Any]]:
        """Split provider-specific params from RedBearModelConfig-only fields in
        extra_params; returns (filtered extra_params, provider_specific dict).

        provider_specific holds params that route per provider:
        - top_k: only Ollama accepts it top-level; DashScope compatible mode passes it via extra_body
        - repetition_penalty: only Ollama/DashScope support it; DashScope compatible mode via extra_body
        - seed: only some providers support it
        - enable_search: only DashScope supports it; compatible mode passes it via extra_body
        - stop: only OpenAI-compatible providers accept it top-level
        - temperature: passed top-level for OpenAI-compatible providers
        - max_tokens: passed top-level for OpenAI-compatible providers

        Fields in config_only_keys are RedBearModelConfig config fields and must
        not be expanded into the final LLM class constructor params.
        """
        provider_specific_keys = ("top_k", "repetition_penalty", "seed", "enable_search", "stop", "temperature", "max_tokens")
        config_only_keys = RedBearModelFactory._CONFIG_ONLY_KEYS
        provider_specific = {}
        for key in provider_specific_keys:
            if key in extra_params:
                provider_specific[key] = extra_params[key]
        filtered = {k: v for k, v in extra_params.items() if k not in config_only_keys and k not in provider_specific_keys}
        return filtered, provider_specific

    @classmethod
    def get_model_params(cls, config: RedBearModelConfig) -> Dict[str, Any]:
        """Get model params for the provider."""
        provider = config.provider.lower()

        # Log provider info for debugging
        logger = logging.getLogger(__name__)
        logger.debug(
            f"获取模型参数 - Provider: {provider}, Model: {config.model_name}, "
            f"features: {config.features}, deep_thinking: {config.deep_thinking}"
        )

        filtered_extra_params, provider_specific = cls._extract_provider_specific_params(config.extra_params)
        default_headers = config.extra_params.get("default_headers")
        if default_headers:
            logger.info(f"额外请求头已注入: {default_headers}")

        # All dashscope models use OpenAI-compatible mode
        # (the ChatTongyi native protocol is retired)
        if provider == ModelProvider.DASHSCOPE:
            if not config.base_url:
                config.base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            # Connect timeout follows the call budget (capped at 60s): short-budget
            # scenarios (e.g. config validation with timeout=10) must not spend 60s
            # connecting before failing
            timeout_config = httpx.Timeout(
                timeout=config.timeout,
                connect=min(config.timeout, 60.0),
                read=config.timeout,
                write=60.0,
                pool=10.0,
            )
            http_client, http_async_client = _get_shared_openai_clients(timeout_config)
            params: Dict[str, Any] = {
                "model": config.model_name,
                "base_url": config.base_url,
                "api_key": config.api_key,
                "timeout": timeout_config,
                "max_retries": config.max_retries,
                "http_client": http_client,
                "http_async_client": http_async_client,
                **filtered_extra_params
            }
            if default_headers:
                params["default_headers"] = default_headers
            # Enable stream_usage in streaming mode to collect token statistics
            is_streaming = bool(config.extra_params.get("streaming"))
            if is_streaming:
                params["stream_usage"] = True
            # thinking param handling:
            # - thinking_only (class B): cannot pass enable_thinking; no handling at all
            # - thinking (class A): hybrid thinking, toggleable in streaming and
            #   non-streaming modes; non-streaming also supports thinking_budget
            if ModelFeature.THINKING in config.features:
                extra_body = params.setdefault("extra_body", {})
                if config.deep_thinking:
                    extra_body["enable_thinking"] = True
                    if config.thinking_budget_tokens:
                        extra_body["thinking_budget"] = config.thinking_budget_tokens
                else:
                    extra_body["enable_thinking"] = False
            # DashScope OpenAI-compatible API: standard params (temperature/max_tokens/seed/stop)
            # go top-level; DashScope extensions (repetition_penalty/top_k/enable_search) via extra_body
            for key in ("temperature", "max_tokens", "seed", "stop",
                        "repetition_penalty", "top_k", "enable_search"):
                if key in provider_specific and provider_specific[key] is not None:
                    if key in ("repetition_penalty", "top_k", "enable_search"):
                        extra_body = params.setdefault("extra_body", {})
                        extra_body[key] = provider_specific[key]
                    else:
                        params[key] = provider_specific[key]
            # JSON output mode
            # With deep thinking enabled on a thinking (class A) model, response_format conflicts
            # with the thinking API; skip it, letting caller prompt injection be the fallback
            if _should_send_response_format(config):
                if not (ModelFeature.THINKING in config.features and config.deep_thinking):
                    model_kwargs = params.setdefault("model_kwargs", {})
                    model_kwargs["response_format"] = _json_response_format(config)
            return params

        if provider in [
            ModelProvider.OPENAI,
            ModelProvider.XINFERENCE,
            ModelProvider.GPUSTACK,
            ModelProvider.MINIMAX,
            ModelProvider.OLLAMA,
            ModelProvider.VOLCANO,
            ModelProvider.SPEEDBEAR,
        ]:
            # Use an httpx.Timeout object for fine-grained timeout configuration
            # so connect and read timeouts can be controlled separately
            timeout_config = httpx.Timeout(
                timeout=config.timeout,  # Overall timeout
                connect=min(config.timeout, 60.0),  # Connect timeout follows the call budget (cap 60s)
                read=config.timeout,  # Read timeout: use the configured timeout
                write=60.0,  # Write timeout: 60s
                pool=10.0,  # Connection pool timeout: 10s
            )
            http_client, http_async_client = _get_shared_openai_clients(timeout_config)
            # OllamaLLM has a native top_k field, so it can be passed top-level;
            # ChatOpenAI/CompatibleChatOpenAI do not support top_k and the OpenAI API has no
            # such param, so it must not go into model_kwargs or it would surface at
            # AsyncCompletions.create() as an unexpected keyword argument error
            if provider == ModelProvider.OLLAMA:
                if "top_k" in provider_specific and provider_specific["top_k"] is not None:
                    filtered_extra_params["top_k"] = provider_specific["top_k"]
                if "repetition_penalty" in provider_specific and provider_specific["repetition_penalty"] is not None:
                    filtered_extra_params["repetition_penalty"] = provider_specific["repetition_penalty"]

            params: Dict[str, Any] = {
                "model": config.model_name,
                "base_url": config.base_url,
                "api_key": config.api_key,
                "timeout": timeout_config,
                "max_retries": config.max_retries,
                "http_client": http_client,
                "http_async_client": http_async_client,
                **filtered_extra_params
            }

            # OpenAI-compatible providers: temperature, max_tokens, seed, stop
            # are top-level params. Other provider-specific params (top_k,
            # repetition_penalty) are already handled above for Ollama.
            for key in ("temperature", "max_tokens", "seed", "stop"):
                if key in provider_specific and provider_specific[key] is not None:
                    params[key] = provider_specific[key]

            if default_headers and provider != ModelProvider.OLLAMA:
                params["default_headers"] = default_headers

            is_streaming = bool(config.extra_params.get("streaming"))
            if is_streaming:
                params["stream_usage"] = True
            # thinking param handling:
            # - thinking_only (class B): cannot pass enable_thinking; no handling at all
            # - thinking (class A): hybrid thinking, toggleable in streaming and non-streaming modes
            if ModelFeature.THINKING in config.features:
                if provider == ModelProvider.VOLCANO:
                    extra_body = params.setdefault("extra_body", {})
                    if config.deep_thinking:
                        extra_body["thinking"] = {"type": "enabled"}
                        effort = _map_budget_to_reasoning_effort(config.thinking_budget_tokens)
                        if effort is not None:
                            params["reasoning_effort"] = effort
                    else:
                        extra_body["thinking"] = {"type": "disabled"}
                elif provider == ModelProvider.SPEEDBEAR:
                    if config.deep_thinking:
                        params["reasoning_effort"] = "minimal"
                        effort = _map_budget_to_reasoning_effort(config.thinking_budget_tokens)
                        if effort is not None:
                            params["reasoning_effort"] = effort
                    else:
                        params["reasoning_effort"] = "none"
                else:
                    extra_body = params.setdefault("extra_body", {})
                    if config.deep_thinking:
                        extra_body["enable_thinking"] = True
                        if config.thinking_budget_tokens:
                            extra_body["thinking_budget"] = config.thinking_budget_tokens
                    else:
                        extra_body["enable_thinking"] = False
            # JSON output mode
            if _should_send_response_format(config):
                model_kwargs = params.setdefault("model_kwargs", {})
                # With deep thinking enabled on a thinking (class A) model, response_format conflicts
                # with the thinking API; skip it, letting caller prompt injection be the fallback
                if not (
                    ModelFeature.THINKING in config.features and config.deep_thinking
                ):
                    model_kwargs["response_format"] = _json_response_format(config)
            return params
        elif provider == ModelProvider.BEDROCK:
            # Bedrock uses AWS credentials
            # api_key format: "access_key_id:secret_access_key" or just access_key_id
            # region comes from base_url or extra_params
            from botocore.config import Config as BotoConfig
            from .bedrock_model_mapper import normalize_bedrock_model_id

            max_pool_connections = int(os.getenv("BEDROCK_MAX_POOL_CONNECTIONS", "50"))
            max_retries = int(os.getenv("BEDROCK_MAX_RETRIES", "2"))
            # Configure with increased connection pool
            boto_config = BotoConfig(
                max_pool_connections=max_pool_connections,
                retries={'max_attempts': max_retries, 'mode': 'adaptive'}
            )

            # Normalize the model ID (auto-convert a short name to the full Bedrock Model ID)
            model_id = normalize_bedrock_model_id(config.model_name)

            params = {
                "model_id": model_id,
                "config": boto_config,
                **filtered_extra_params
            }
            model_kwargs = params.setdefault("model_kwargs", {})
            # Bedrock-specific param routing:
            # top_k, seed -> model_kwargs under the same names
            # stop -> model_kwargs, mapped to stop_sequences
            # temperature, max_tokens -> ChatBedrock has top-level fields; pass directly
            for key in ("top_k", "seed"):
                if key in provider_specific and provider_specific[key] is not None:
                    model_kwargs[key] = provider_specific[key]
            if "stop" in provider_specific and provider_specific["stop"] is not None:
                model_kwargs["stop_sequences"] = provider_specific["stop"]
            for key in ("temperature", "max_tokens"):
                if key in provider_specific and provider_specific[key] is not None:
                    params[key] = provider_specific[key]

            # Parse the API key (format: access_key_id:secret_access_key)
            if config.api_key:
                access_key_id, _, secret_access_key = config.api_key.partition(":")
                access_key_id = access_key_id.strip()
                secret_access_key = secret_access_key.strip()
                if not access_key_id or not secret_access_key:
                    raise BusinessException(
                        "Bedrock 凭据格式错误：API Key 应为 "
                        "access_key_id:secret_access_key（英文半角冒号分隔），"
                        "请检查是否只填了 Access Key ID、漏填了 secret，或误用了中文冒号",
                        BizCode.INVALID_PARAMETER,
                    )
                params["aws_access_key_id"] = access_key_id
                params["aws_secret_access_key"] = secret_access_key

            # Set the region
            if config.base_url:
                params["region_name"] = config.base_url
            elif "region_name" not in params:
                params["region_name"] = "us-east-1"  # Default region

            # Deep thinking mode: for thinking-capable models such as Claude 3.7 Sonnet
            # the thinking block is passed via additional_model_request_fields; when off it is
            # omitted entirely (Bedrock has no disabled option)
            if config.deep_thinking:
                budget = config.thinking_budget_tokens or 1024
                params["additional_model_request_fields"] = {
                    "thinking": {"type": "enabled", "budget_tokens": budget}
                }
            # JSON output mode
            # With deep thinking enabled on a thinking (class A) model, response_format conflicts
            # with the thinking API; skip it, letting caller prompt injection be the fallback
            if _should_send_response_format(config):
                if not (ModelFeature.THINKING in config.features and config.deep_thinking):
                    model_kwargs = params.setdefault("model_kwargs", {})
                    model_kwargs["response_format"] = _json_response_format(config)
            return params
        else:
            raise BusinessException(f"不支持的提供商: {provider}", code=BizCode.PROVIDER_NOT_SUPPORTED)

def get_provider_llm_class(config: RedBearModelConfig, type: ModelType = ModelType.LLM) -> type[BaseLLM]:
    """Get the model class for the given provider."""
    provider = config.provider.lower()

    # All dashscope models and volcano models use the OpenAI-compatible protocol
    # (the ChatTongyi native protocol is retired)
    if provider in [
        ModelProvider.DASHSCOPE,
        ModelProvider.VOLCANO,
        ModelProvider.OPENAI,
        ModelProvider.XINFERENCE,
        ModelProvider.GPUSTACK,
        ModelProvider.MINIMAX,
        ModelProvider.SPEEDBEAR,
    ]:
        return CompatibleChatOpenAI
    elif provider == ModelProvider.OLLAMA:
        return OllamaLLM
    elif provider == ModelProvider.BEDROCK:
        return ChatBedrock
    else:
        raise BusinessException(f"不支持的模型提供商: {provider}", code=BizCode.PROVIDER_NOT_SUPPORTED)


