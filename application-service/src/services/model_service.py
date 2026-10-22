import logging

import base64
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from typing import TYPE_CHECKING, Iterable, List, NoReturn, Optional, Dict, Any
import uuid
import time
import asyncio
from urllib.parse import urlparse

from pydantic import SecretStr

from ..models.models_model import (
    LLM_FAMILY_TYPES,
    ModelConfig,
    ModelApiKey,
    ModelType,
    LoadBalanceStrategy,
    ModelBase,
    ModelProvider,
)
from ..repositories.model_repository import ModelConfigRepository, ModelApiKeyRepository, ModelBaseRepository
from ..schemas import model_schema
from ..schemas.model_schema import ModelInfo
from ..config import current_settings
from ..model_clients.model_provider_config import (
    get_default_provider_api_base,
    is_local_deployment_provider,
    validate_api_base_against_default,
)
from ..errors import BusinessException
from ..errors import BizCode
from ..utils.datetime_utils import utcnow_naive
from ..integrations.model.invoke_backend import RemoteInvokeRef

from redbear_model import (
    CredentialDecryptError,
    FailoverPlan,
    ModelConfigDeprecatedError,
    ModelConfigInactiveError,
    RedBearModelError,
    ResolvedModelConfig,
    SpeedbearChannelMissingError,
)

from .channel_registry import (
    candidate_channels_sync,
    resolve_composite_plan_async,
    resolve_composite_plan_sync,
    resolve_config_plan_async,
    resolve_config_plan_sync,
)
from .model_profile_view import (
    legacy_view,
    normalize_type,
    profile_columns,
    profile_of,
    wire_model_base,
)

if TYPE_CHECKING:
    from redbear_model import ImageEmbeddingContent

logger = logging.getLogger(__name__)

# Channel resolution switch: off=legacy path only / prefer=v2 first with legacy fallback / only=v2 only
# off mode keeps the legacy read branch over the speedbear public-model binding table (rollback
# fallback); prefer/only always go through channel resolution
_RESOLUTION_MODES = ("off", "prefer", "only")

# Connectivity failure markers: a match is classified as "network unreachable / timeout"
# rather than a credential or parameter problem. Covers the openai SDK
# (APITimeoutError/APIConnectionError), httpx (Connect/ReadTimeout, ConnectError),
# requests and botocore (Max retries exceeded / EndpointConnectionError).
_CONNECTIVITY_ERROR_MARKERS = (
    "timeout",
    "timed out",
    "connecterror",
    "connection error",
    "connection refused",
    "connection reset",
    "max retries exceeded",
    "name resolution",
    "network is unreachable",
    "no route to host",
)

# Auth failure markers: the openai SDK exposes its own exception class (AuthenticationError);
# the native dashscope path (DashScopeEmbeddings raises ValueError for embedding,
# _dashscope_error_message raises RuntimeError for rerank) only carries
# "status_code: 401 \n code: InvalidApiKey \n message: ..." text with no dedicated exception
# class, hence these text markers; the qwen3-vl multimodal adapters (redbear_model) also carry
# "status_code: <code>" (see that package's dashscope_multimodal_embedding/rerank adapters).
_AUTH_ERROR_MARKERS = (
    "authentication",
    "invalidapikey",
    "invalid api-key",
    "invalid api key",
    "incorrect api key",
    "invalid_api_key",
    "status_code: 401",
    "unauthorized",
)
_resolution_fallback_hits = 0


def resolution_mode() -> str:
    mode = (getattr(current_settings(), "model_channel_resolution", "") or "prefer").strip().lower()
    if mode not in _RESOLUTION_MODES:
        logger.warning("Unknown MODEL_CHANNEL_RESOLUTION=%r; using 'prefer'", mode)
        return "prefer"
    return mode


def channel_resolution_fallback_hits() -> int:
    """Fallback-hit counter used while in prefer mode (watches for migration gaps; only mode is adopted once it returns to zero)."""
    return _resolution_fallback_hits


def _record_resolution_fallback(model_config_id: uuid.UUID, exc: Exception) -> None:
    global _resolution_fallback_hits
    _resolution_fallback_hits += 1
    logger.warning(
        "channel resolution fell back to legacy path for config %s: %s",
        model_config_id,
        exc,
    )

_MODEL_VALIDATION_CONFIG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
_MODEL_VALIDATION_KEY_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
_MODEL_VALIDATION_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000003")
_MODEL_VALIDATION_IMAGE_DATA_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAE0lEQVR4nGP8//8/AwMDEwMYAAAkBgMBXaJOiAAAAABJRU5ErkJggg=="
)
_MODEL_VALIDATION_IMAGE_BYTES = len(
    base64.b64decode(_MODEL_VALIDATION_IMAGE_DATA_URI.partition(",")[2])
)


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value)).lower()


def _shared_validation_config(
    *,
    model_name: str,
    provider: str,
    api_key: str,
    api_base: str | None,
    model_type: str,
    input_modalities: list | None = None,
    output_modalities: list | None = None,
    features: list | None = None,
) -> "ResolvedModelConfig":
    from redbear_model import ModelProfile as SharedModelProfile
    from redbear_model import (
        ModelProvider as SharedModelProvider,
    )
    from redbear_model import (
        ModelRuntimeOptions,
        ResolvedModelConfig,
    )

    shared_provider = SharedModelProvider(_enum_value(provider))
    return ResolvedModelConfig(
        model_config_id=_MODEL_VALIDATION_CONFIG_ID,
        key_id=_MODEL_VALIDATION_KEY_ID,
        tenant_id=_MODEL_VALIDATION_TENANT_ID,
        provider=shared_provider,
        model_name=model_name,
        api_key=SecretStr(api_key),
        base_url=api_base,
        profile=SharedModelProfile.from_stored_fields(
            model_id=_MODEL_VALIDATION_CONFIG_ID,
            tenant_id=_MODEL_VALIDATION_TENANT_ID,
            type=_enum_value(model_type),
            provider=shared_provider,
            input_modalities=tuple(_enum_value(item) for item in (input_modalities or [])),
            output_modalities=tuple(_enum_value(item) for item in (output_modalities or [])),
            features=tuple(_enum_value(item) for item in (features or [])),
        ),
        runtime=ModelRuntimeOptions(timeout_s=10.0, max_retries=0),
    )


def is_asr_model(model_type: str) -> bool:
    return _enum_value(model_type) == _enum_value(ModelType.ASR)


def _require_asr_model_configuration(provider: str, model_type: str) -> None:
    if not is_asr_model(model_type):
        raise BusinessException("ASR 模型类型不匹配", BizCode.INVALID_PARAMETER)
    if _enum_value(provider) != "dashscope":
        raise BusinessException("ASR 模型当前仅支持 DashScope", BizCode.INVALID_PARAMETER)


def _require_asr_api_base(api_base: str | None) -> None:
    from redbear_model.providers.dashscope_asr import (
        resolve_dashscope_asr_base_address,
    )

    try:
        resolve_dashscope_asr_base_address(api_base)
    except ValueError:
        raise BusinessException(
            "ASR API Base URL 必须是 DashScope 根路径：/api/v1、"
            "/compatible-mode/v1 或 /compatible-api/v1",
            BizCode.INVALID_PARAMETER,
        ) from None


def _validation_image() -> "ImageEmbeddingContent":
    from redbear_model import ImageEmbeddingContent

    return ImageEmbeddingContent(
        media_type="image/png",
        data_uri=_MODEL_VALIDATION_IMAGE_DATA_URI,
        decoded_bytes=_MODEL_VALIDATION_IMAGE_BYTES,
    )


async def _validate_qwen3_vl_embedding(
    config: "ResolvedModelConfig",
    test_message: str,
    started_at: float,
) -> dict[str, Any]:
    from redbear_model import EmbeddingPurpose, EmbeddingRequest, TextEmbeddingContent
    from redbear_model.runtime import RedBearEmbeddings as SharedRedBearEmbeddings

    embedding = SharedRedBearEmbeddings(config)
    try:
        result = await embedding.aembed_contents(
            EmbeddingRequest(
                purpose=EmbeddingPurpose.RETRIEVAL,
                contents=(TextEmbeddingContent(text=test_message), _validation_image()),
            )
        )
    finally:
        await embedding.aclose()
    usage = dict(result.usage)
    usage.update(vector_count=1, vector_dimension=result.dimension)
    return {
        "valid": True,
        "message": "Embedding 模型配置验证成功",
        "response": f"成功生成 1 个融合向量，维度: {result.dimension}",
        "elapsed_time": time.time() - started_at,
        "usage": usage,
        "error": None,
    }


async def _validate_qwen3_vl_rerank(
    config: "ResolvedModelConfig",
    started_at: float,
) -> dict[str, Any]:
    from redbear_model import RerankCandidateView
    from redbear_model.runtime import RedBearRerank as SharedRedBearRerank

    rerank = SharedRedBearRerank(config)
    views = (
        RerankCandidateView(chunk_index=0, kind="text", content="测试文本候选"),
        RerankCandidateView(
            chunk_index=1,
            kind="image",
            image_index=0,
            content=_MODEL_VALIDATION_IMAGE_DATA_URI,
        ),
    )
    try:
        results = await rerank.arerank_multimodal(
            _validation_image(),
            views,
            top_n=len(views),
        )
    finally:
        await rerank.aclose()
    return {
        "valid": True,
        "message": "Rerank 模型配置验证成功",
        "response": f"成功完成多模态重排序，返回 {len(results)} 个结果",
        "elapsed_time": time.time() - started_at,
        "usage": {"document_count": len(views), "result_count": len(results)},
        "error": None,
    }


def _require_api_base_for_local_provider(provider: ModelProvider | str, api_base: Optional[str]) -> None:
    """Locally deployed providers must explicitly configure the real service address."""
    if is_local_deployment_provider(provider) and not (
        isinstance(api_base, str) and api_base.strip()
    ):
        raise BusinessException(
            f"本地部署提供商 {getattr(provider, 'value', provider)} 必须配置 API Base URL",
            BizCode.INVALID_PARAMETER,
        )


def _require_wellformed_api_base(
    provider: ModelProvider | str,
    api_base: Optional[str],
    model_type: str | None = None,
) -> None:
    """A non-empty api_base must be a full address starting with http(s):// (empty is valid and uses the default).

    bedrock exception: its api_base carries the AWS region (e.g. us-east-1, mapped to region_name), not a URL.
    """
    provider_name = str(getattr(provider, "value", provider)).lower()
    if provider_name == ModelProvider.BEDROCK.value:
        return
    if not (isinstance(api_base, str) and api_base.strip()):
        return
    parsed = urlparse(api_base.strip())
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return
    default = get_default_provider_api_base(provider, model_type)
    hint = f"；如需使用官方地址请留空（默认 {default}）" if default else ""
    raise BusinessException(
        f"API Base URL 格式不正确：需要以 http:// 或 https:// 开头的完整地址{hint}",
        BizCode.INVALID_PARAMETER,
    )


def _require_wellformed_bedrock_credential(
    provider: ModelProvider | str, api_key: Optional[str]
) -> None:
    """bedrock's api_key is expected as access_key_id:secret_access_key (if either half is missing,
    ChatBedrock raises an unreadable pydantic ValidationError at construction time, so it is rejected early)."""
    provider_name = str(getattr(provider, "value", provider)).lower()
    if provider_name != ModelProvider.BEDROCK.value:
        return
    if not (isinstance(api_key, str) and api_key.strip()):
        return
    access_key_id, sep, secret = api_key.strip().partition(":")
    if sep and access_key_id.strip() and secret.strip():
        return
    raise BusinessException(
        "Bedrock 的 API Key 格式不正确：需要按 access_key_id:secret_access_key 填写"
        "（英文冒号分隔，两者都不可为空）",
        BizCode.INVALID_PARAMETER,
    )


def _require_supported_api_base(
    provider: ModelProvider | str,
    api_base: Optional[str],
    model_type: str,
) -> None:
    """Combinations whose custom api_base is never read at runtime; only empty or the official base address is allowed."""
    error = validate_api_base_against_default(provider, api_base, model_type)
    if error:
        raise BusinessException(error, BizCode.INVALID_PARAMETER)


class ModelConfigService:
    """Model configuration service."""

    @staticmethod
    def is_model_available(
        db: Session, model_config: ModelConfig, tenant_id: uuid.UUID | None = None
    ) -> bool | None:
        """Channel availability for a single model (details view); returns None when tenant_id is missing (not probed)."""
        if model_config.model_base is not None and model_config.model_base.is_deprecated:
            return False
        if not model_config.is_active:
            return False
        if tenant_id is None:
            return None
        return bool(candidate_channels_sync(db, model_config, tenant_id=tenant_id))

    @staticmethod
    def assert_refs_publishable(db: Session, config_ids: Iterable[uuid.UUID]) -> None:
        """Publish guard: referenced model configs must be enabled and their base models must not be deprecated.

        Single batched query (no N+1); one aggregated raise, with deprecated taking precedence over
        disabled. Missing reference rows (hard-deleted or invalid ids) are not blocked and fail at
        runtime instead.
        """
        ids = list(dict.fromkeys(config_ids))
        if not ids:
            return
        rows = (
            db.query(
                ModelConfig.id,
                ModelConfig.name,
                ModelConfig.is_active,
                ModelBase.is_deprecated,
            )
            .outerjoin(ModelBase, ModelBase.id == ModelConfig.model_id)
            .filter(ModelConfig.id.in_(ids))
            .all()
        )
        deprecated = [row.name for row in rows if row.is_deprecated]
        disabled = [row.name for row in rows if not row.is_active]
        if deprecated:
            raise BusinessException(
                f"以下模型已下线，请更换后再发布：{'、'.join(deprecated)}",
                BizCode.MODEL_DEPRECATED,
            )
        if disabled:
            raise BusinessException(
                f"以下模型已禁用，请更换后再发布：{'、'.join(disabled)}",
                BizCode.INVALID_PARAMETER,
            )

    @staticmethod
    def get_model_by_id(db: Session, model_id: uuid.UUID, tenant_id: uuid.UUID | None = None) -> ModelConfig:
        """Runtime read: deprecation is rejected immediately (read-side derived ban)."""
        model = ModelConfigRepository.get_by_id(db, model_id, tenant_id=tenant_id)
        if not model:
            raise BusinessException("模型配置不存在", BizCode.MODEL_NOT_FOUND)
        if model.model_base and model.model_base.is_deprecated:
            raise BusinessException(
                f"模型 '{model.name}' 已弃用，请在模型配置中更换为其他模型",
                BizCode.MODEL_DEPRECATED,
            )
        return model

    @staticmethod
    def get_model_detail(db: Session, model_id: uuid.UUID, tenant_id: uuid.UUID | None = None) -> ModelConfig:
        """Management detail read: deprecation is not blocked (the response carries the is_deprecated flag; the frontend greys it out)."""
        model = ModelConfigRepository.get_by_id(db, model_id, tenant_id=tenant_id)
        if not model:
            raise BusinessException("模型配置不存在", BizCode.MODEL_NOT_FOUND)
        return model

    @staticmethod
    async def get_model_by_id_async(
        db: AsyncSession,
        model_id: uuid.UUID,
        tenant_id: uuid.UUID | None = None,
    ) -> ModelConfig:
        """Get a model config by ID asynchronously."""
        model = await ModelConfigRepository.get_by_id_async(db, model_id, tenant_id=tenant_id)
        if not model:
            raise BusinessException("模型配置不存在", BizCode.MODEL_NOT_FOUND)
        if model.model_base and model.model_base.is_deprecated:
            raise BusinessException(
                f"模型 '{model.name}' 已弃用，请在模型配置中更换为其他模型",
                BizCode.MODEL_DEPRECATED,
            )
        return model

    @staticmethod
    def raise_model_unavailable(
        db: Session,
        model_config_id: uuid.UUID,
        tenant_id: uuid.UUID | None = None,
    ) -> NoReturn:
        """Restores error semantics when credentials cannot be resolved: missing/deprecated/inactive model or missing API key (sync db)."""
        ModelConfigService._raise_no_credential_error(
            ModelConfigService.get_model_by_id(db, model_config_id, tenant_id=tenant_id)
        )

    @staticmethod
    async def raise_model_unavailable_async(
        db: AsyncSession,
        model_config_id: uuid.UUID,
        tenant_id: uuid.UUID | None = None,
    ) -> NoReturn:
        """Async variant (same semantics as the sync version)."""
        ModelConfigService._raise_no_credential_error(
            await ModelConfigService.get_model_by_id_async(
                db, model_config_id, tenant_id=tenant_id
            )
        )

    @staticmethod
    async def raise_model_unavailable_bridge_async(
        db: Session | AsyncSession,
        model_config_id: uuid.UUID,
        tenant_id: uuid.UUID | None = None,
    ) -> NoReturn:
        """Bridge variant: inside an async context db may be a Session (trial-run host) or an AsyncSession."""
        if isinstance(db, AsyncSession):
            await ModelConfigService.raise_model_unavailable_async(
                db, model_config_id, tenant_id=tenant_id
            )
        ModelConfigService.raise_model_unavailable(db, model_config_id, tenant_id=tenant_id)

    @staticmethod
    def _raise_no_credential_error(model: ModelConfig) -> NoReturn:
        if not model.is_active:
            raise BusinessException(
                "当前模型未启用，请在模型配置中确认 API Key 和 URL 已配置后启用模型",
                BizCode.MODEL_CONFIG_INVALID,
            )
        raise BusinessException("模型配置缺少 API Key", BizCode.INVALID_PARAMETER)

    @staticmethod
    def _runtime_model_view(
        model: ModelConfig, tenant_id: uuid.UUID | None = None
    ) -> ModelInfo:
        """Shared mapping for non-decrypting views: capabilities/modalities derive from the profile; credentials and channels are always empty.

        Ownership tenant uses the **calling tenant** (when tenant_id is non-empty): public configs
        (speedbear etc.) belong to the system tenant, while the service loads candidate channels by
        request tenant (platform channels live under each calling tenant) and usage attribution lands
        on the calling tenant; without a tenant context (tenant_id=None) it falls back to the config
        row's tenant (runtime fallback semantics).
        """

        profile = profile_of(model)
        effective_tenant = tenant_id or model.tenant_id
        return ModelInfo(
            model_name=model.name,
            model_type=ModelType(normalize_type(model.type)),
            api_key=None,
            provider=model.provider,
            input_modalities=[str(item.value) for item in profile.input_modalities],
            output_modalities=[str(item.value) for item in profile.output_modalities],
            features=[str(item.value) for item in profile.features],
            tenant_id=str(effective_tenant) if effective_tenant is not None else None,
            model_config_id=str(model.id),
            channel_id=None,
        )

    @staticmethod
    async def get_runtime_model_view_async(
        db: AsyncSession,
        model_id: uuid.UUID,
        tenant_id: uuid.UUID | None = None,
    ) -> ModelInfo:
        """Runtime **non-decrypting** model view (invoke seam): `api_key` is always None and no failover plan is attached.

        Capabilities/modalities derive from the profile; visibility (missing / deprecated /
        cross-tenant) follows the error semantics of :meth:`get_model_by_id_async`; credential
        availability and routing are judged by the model service at invoke time (only there do
        facts such as disabled channels exist in full, see `channel_registry`). Ownership tenant
        uses the calling tenant (public configs belong to the system tenant; routing and
        attribution must land on the caller, see :meth:`_runtime_model_view`).
        """

        model = await ModelConfigService.get_model_by_id_async(db, model_id, tenant_id=tenant_id)
        return ModelConfigService._runtime_model_view(model, tenant_id)

    @staticmethod
    def get_runtime_model_view(
        db: Session,
        model_id: uuid.UUID,
        tenant_id: uuid.UUID | None = None,
    ) -> ModelInfo:
        """Sync twin (for sync paths such as the shared_chat / llm_router Session): same semantics as the async version."""

        model = ModelConfigService.get_model_by_id(db, model_id, tenant_id=tenant_id)
        return ModelConfigService._runtime_model_view(model, tenant_id)

    @staticmethod
    async def get_runtime_model_view_bridge_async(
        db: Session | AsyncSession,
        model_id: uuid.UUID,
        tenant_id: uuid.UUID | None = None,
    ) -> ModelInfo:
        """Bridge variant: inside an async context db may be a Session (trial-run host) or an AsyncSession."""

        if isinstance(db, AsyncSession):
            return await ModelConfigService.get_runtime_model_view_async(
                db, model_id, tenant_id=tenant_id
            )
        return ModelConfigService.get_runtime_model_view(db, model_id, tenant_id=tenant_id)

    @staticmethod
    def search_models_by_name(db: Session, name: str, tenant_id: uuid.UUID | None = None, limit: int = 10) -> List[
        ModelConfig]:
        """Fuzzy-match model configs by name."""
        return ModelConfigRepository.search_by_name(db, name, tenant_id=tenant_id, limit=limit)

    @staticmethod
    async def validate_model_config(
        db: Session,
        *,
        model_name: str,
        provider: str,
        api_key: str,
        api_base: Optional[str] = None,
        model_type: str = "llm",
        test_message: str = "Hello",
        input_modalities: Optional[list] = None,
        output_modalities: Optional[list] = None,
        features: Optional[list] = None,
    ) -> Dict[str, Any]:
        """Validate whether a model configuration works.

        Args:
            db: Database session
            model_name: Model name
            provider: Provider
            api_key: API key
            api_base: API base URL
            model_type: Model type (llm/chat/embedding/rerank)
            test_message: Test message
            input_modalities: Input modality list (contract v2)
            output_modalities: Output modality list (contract v2)
            features: Feature flag list (contract v2)

        Returns:
            Dict: Validation result
        """
        if is_asr_model(model_type):
            return {
                "valid": False,
                "message": "ASR 模型不支持配置时活体验证",
                "response": None,
                "elapsed_time": None,
                "usage": None,
                "error": "ASR 模型将在实际调用时校验模型和凭据",
                "error_type": "MediaValidationUnsupported",
            }
        _ = db
        import traceback

        model_type_lower = _enum_value(model_type)
        provider_lower = _enum_value(provider)
        is_qwen3_vl_request = provider_lower == "dashscope" and (
            (model_type_lower, model_name)
            in {
                ("embedding", "qwen3-vl-embedding"),
                ("rerank", "qwen3-vl-rerank"),
            }
        )
        try:
            start_time = time.time()

            if is_qwen3_vl_request:
                try:
                    from redbear_model import (
                        is_qwen3_vl_embedding,
                        is_qwen3_vl_reranker,
                    )
                except ModuleNotFoundError as exc:
                    if exc.name != "redbear_model":
                        raise
                    return {
                        "valid": False,
                        "message": "当前 API 暂不支持 Qwen3-VL 模型配置验证",
                        "response": None,
                        "elapsed_time": time.time() - start_time,
                        "usage": None,
                        "error": "API 未安装可选的 redbear-model 包，暂无法验证 Qwen3-VL 模型",
                        "error_type": "ModelRuntimeUnavailable",
                    }
                validation_input = [_enum_value(item) for item in (input_modalities or [])]
                if "text" not in validation_input:
                    validation_input.insert(0, "text")
                if "image" not in validation_input:
                    validation_input.append("image")
                shared_config = _shared_validation_config(
                    model_name=model_name,
                    provider=provider_lower,
                    api_key=api_key,
                    api_base=api_base,
                    model_type=model_type_lower,
                    input_modalities=validation_input,
                    output_modalities=output_modalities,
                    features=features,
                )
                if is_qwen3_vl_embedding(shared_config):
                    return await _validate_qwen3_vl_embedding(
                        shared_config,
                        test_message,
                        start_time,
                    )
                if is_qwen3_vl_reranker(shared_config):
                    return await _validate_qwen3_vl_rerank(shared_config, start_time)
                raise ValueError("Qwen3-VL model capability mismatch")

            from ..model_clients import RedBearLLM
            from ..model_clients.base import RedBearModelConfig

            model_config = RedBearModelConfig(
                model_name=model_name,
                provider=provider,
                api_key=api_key,
                base_url=api_base,
                input_modalities=[_enum_value(item) for item in (input_modalities or [])],
                output_modalities=[_enum_value(item) for item in (output_modalities or [])],
                features=[_enum_value(item) for item in (features or [])],
                timeout=10.0,
                max_retries=0,
            )

            # Pick the validation path by model type (including the legacy "chat" normalization)
            if model_type_lower in LLM_FAMILY_TYPES:
                # LLM-family validation - always uses string input
                llm = RedBearLLM(model_config, type=ModelType.LLM)
                response = await llm.ainvoke(test_message)
                elapsed_time = time.time() - start_time

                content = response.content if hasattr(response, 'content') else str(response)
                usage = None
                if hasattr(response, 'usage_metadata'):
                    usage = {
                        "input_tokens": getattr(response.usage_metadata, 'input_tokens', 0),
                        "output_tokens": getattr(response.usage_metadata, 'output_tokens', 0),
                        "total_tokens": getattr(response.usage_metadata, 'total_tokens', 0)
                    }

                return {
                    "valid": True,
                    "message": f"{model_type.upper()} 模型配置验证成功",
                    "response": content,
                    "elapsed_time": elapsed_time,
                    "usage": usage,
                    "error": None
                }

            elif model_type_lower == "embedding":
                # Embedding validation (package runtime, same semantics as the service-side /validate)
                from redbear_model.runtime import RedBearEmbeddings as SharedRedBearEmbeddings

                shared_config = _shared_validation_config(
                    model_name=model_name,
                    provider=provider_lower,
                    api_key=api_key,
                    api_base=api_base,
                    model_type=model_type_lower,
                    input_modalities=input_modalities,
                    output_modalities=output_modalities,
                    features=features,
                )
                embedding = SharedRedBearEmbeddings(shared_config)
                test_texts = [test_message, "测试文本"]

                # Volcano Engine uses embed_batch; others use embed_documents
                if provider_lower == "volcano":
                    vectors = await asyncio.to_thread(embedding.embed_batch, test_texts)
                else:
                    vectors = await asyncio.to_thread(embedding.embed_documents, test_texts)

                elapsed_time = time.time() - start_time

                return {
                    "valid": True,
                    "message": "Embedding 模型配置验证成功",
                    "response": f"成功生成 {len(vectors)} 个向量，维度: {len(vectors[0]) if vectors else 0}",
                    "elapsed_time": elapsed_time,
                    "usage": {
                        "input_tokens": len(test_message),
                        "vector_count": len(vectors),
                        "vector_dimension": len(vectors[0]) if vectors else 0
                    },
                    "error": None
                }

            elif model_type_lower == "rerank":
                # Rerank validation (the sync method runs in a thread; package runtime matches service-side semantics)
                from redbear_model.runtime import RedBearRerank as SharedRedBearRerank

                shared_config = _shared_validation_config(
                    model_name=model_name,
                    provider=provider_lower,
                    api_key=api_key,
                    api_base=api_base,
                    model_type=model_type_lower,
                    input_modalities=input_modalities,
                    output_modalities=output_modalities,
                    features=features,
                )
                rerank = SharedRedBearRerank(shared_config)
                query = test_message
                documents = ["这是第一个文档", "这是第二个文档", "这是第三个文档"]
                results = await asyncio.to_thread(rerank.rerank, query=query, documents=documents, top_n=3)
                elapsed_time = time.time() - start_time

                return {
                    "valid": True,
                    "message": "Rerank 模型配置验证成功",
                    "response": f"成功对 {len(documents)} 个文档进行重排序，返回 top {len(results) if results else 0} 结果",
                    "elapsed_time": elapsed_time,
                    "usage": {
                        "query_length": len(query),
                        "document_count": len(documents),
                        "result_count": len(results) if results else 0
                    },
                    "error": None
                }

            elif model_type_lower == "image":
                # Image generation validation
                from ..model_clients.generation import RedBearImageGenerator

                generator = RedBearImageGenerator(model_config)
                result = await generator.agenerate(
                    prompt="a cute panda",
                    size="2K"
                )
                elapsed_time = time.time() - start_time
                logger.info(f"成功生成图片，结果: {result}")

                return {
                    "valid": True,
                    "message": "图片生成模型配置验证成功",
                    "response": f"成功生成图片，结果: {result}",
                    "elapsed_time": elapsed_time,
                    "usage": {
                        "prompt_length": len("a cute panda"),
                        "image_count": 1
                    },
                    "error": None
                }

            elif model_type_lower == "video":
                # Video generation validation
                from ..model_clients.generation import RedBearVideoGenerator

                generator = RedBearVideoGenerator(model_config)
                result = await generator.agenerate(
                    prompt="a cute panda playing in bamboo forest",
                    duration=5
                )
                elapsed_time = time.time() - start_time

                # Video generation is an async job; it returns a task ID
                task_id = result.get("task_id") if isinstance(result, dict) else None

                return {
                    "valid": True,
                    "message": "视频生成模型配置验证成功",
                    "response": f"成功创建视频生成任务，任务ID: {task_id}",
                    "elapsed_time": elapsed_time,
                    "usage": {
                        "prompt_length": len("a cute panda playing in bamboo forest"),
                        "task_id": task_id
                    },
                    "error": None
                }

            else:
                return {
                    "valid": False,
                    "message": "不支持的模型类型",
                    "response": None,
                    "elapsed_time": None,
                    "usage": None,
                    "error": f"不支持的模型类型: {model_type}"
                }

        except Exception as e:
            # Classification always matches against the raw exception text: the VL path overrides
            # its display message with a generic hint, so matching the overridden error_message
            # would swallow auth/connectivity markers.
            raw_error_message = str(e)
            error_message = (
                "Qwen3-VL 模型验证失败"
                if is_qwen3_vl_request
                else raw_error_message
            )
            error_type = type(e).__name__
            # Special-case common error types
            if "unsupported countries" in raw_error_message.lower() or "unsupported region" in raw_error_message.lower():
                # Region/country restriction (applies to all providers)
                error_message = "区域限制: 该模型在当前区域或国家/地区不可用，请检查提供商的服务区域限制"
            elif "ValidationException" in error_type or "ValidationException" in raw_error_message:
                # Other validation errors
                if "access denied" in raw_error_message.lower():
                    error_message = "访问被拒绝: 请检查 API 凭证和权限配置"
                else:
                    error_message = f"验证失败: {raw_error_message}"
            elif any(
                marker in f"{error_type} {raw_error_message}".lower()
                for marker in _AUTH_ERROR_MARKERS
            ):
                error_message = "认证失败: API Key 无效或已过期"
            elif (
                "aws_access_key_id" in raw_error_message
                and "aws_secret_access_key" in raw_error_message
            ):
                # Incomplete credentials at ChatBedrock/BedrockEmbeddings construction (the raw pydantic message is unreadable)
                error_message = (
                    "认证失败: Bedrock 凭据不完整，API Key 需要按 "
                    "access_key_id:secret_access_key 格式填写（英文冒号分隔）"
                )
            elif any(
                marker in f"{error_type} {raw_error_message}".lower()
                for marker in _CONNECTIVITY_ERROR_MARKERS
            ):
                # On failure, report the actual request address, otherwise "bad key" and "network
                # down" cannot be told apart; the message varies by address source: user-configured
                # address vs official public endpoint (only the latter suggests configuring a gateway)
                configured = (api_base or "").strip()
                official = get_default_provider_api_base(provider)
                if configured:
                    error_message = (
                        f"连接失败: 无法访问你配置的 API Base URL {configured}"
                        f"（连接超时或网络不可达），请确认该地址正确且网络可达"
                    )
                    if official:
                        error_message += (
                            f"；如需改用官方公共端点，请清空 API Base URL（默认 {official}）"
                        )
                else:
                    error_message = (
                        f"连接失败: 无法访问官方公共端点 {official or '默认端点'}"
                        f"（连接超时或网络不可达），请检查网络连通性，"
                        f"或为该模型配置代理/网关地址（API Base URL）"
                    )
            elif "RateLimitError" in error_type or "rate limit" in raw_error_message.lower():
                error_message = "请求频率限制: 已超过 API 调用限制"
            elif "InvalidRequestError" in error_type or "invalid request" in raw_error_message.lower():
                error_message = f"无效请求: {raw_error_message}"
            elif "model_copy" in raw_error_message:
                error_message = "模型消息格式错误: 请确保使用正确的模型类型（LLM/Chat）"

            # Log the detailed error
            logger.error(f"模型验证失败 - 类型: {error_type}, 模型: {model_name}, 提供商: {provider}")
            logger.error(f"错误详情: {error_message}")
            logger.debug(f"完整堆栈: {traceback.format_exc()}")

            return {
                "valid": False,
                "message": f"{model_type.upper()} 模型配置验证失败",
                "response": None,
                "elapsed_time": None,
                "usage": None,
                "error": error_message,
                "error_type": error_type
            }

class ModelApiKeyService:
    """Model API key service."""

    @staticmethod
    def _stamp_usage_attribution(
        api_key: ModelApiKey,
        tenant_id: uuid.UUID | None,
        model_config_id: uuid.UUID | str | None,
        channel_id: uuid.UUID | str | None = None,
    ) -> ModelApiKey:
        """Attach usage-event attribution onto the runtime key shell (non-mapped attributes, never persisted).

        Consumers pass them through to RedBearModelConfig; the usage-event side skips when tenant/config is missing.
        """
        api_key.tenant_id = None if tenant_id is None else str(tenant_id)
        api_key.model_config_id = None if model_config_id is None else str(model_config_id)
        api_key.channel_id = None if channel_id is None else str(channel_id)
        return api_key

    @staticmethod
    def _stamp_profile_columns(api_key: ModelApiKey, model_config: ModelConfig) -> ModelApiKey:
        """Legacy key rows carry no profile columns: the runtime shell fills them from the config row's derived view (consumers read the three columns uniformly; non-mapped attributes)."""
        columns = profile_columns(model_config)
        api_key.input_modalities = list(columns["input_modalities"])
        api_key.output_modalities = list(columns["output_modalities"])
        api_key.features = list(columns["features"])
        return api_key

    @staticmethod
    def _runtime_api_key_from_resolved(
        resolved: ResolvedModelConfig,
        *,
        failover_plan: FailoverPlan | None = None,
    ) -> ModelApiKey:
        """ResolvedModelConfig → transient ModelApiKey compatibility shell (never persisted; id=channel_id).

        Capabilities live in the three profile columns (.input_modalities/.output_modalities/.features);
        the legacy .capability/.is_omni columns are a derived view (read only by frozen consumer
        surfaces such as the e2b sandbox payload; internal consumers always read the three columns,
        and the legacy columns are removed along with the cleanup).

        When the channel api_base is empty (provider-level channels), the provider's public base
        address is materialized (the native dashscope SDK composition strips it back to /api/v1 at
        runtime), matching RedBearModelConfig's default-filling semantics; locally deployed
        providers have no default address, so it stays empty and downstream fails loudly.

        failover_plan: the in-request channel failover plan, a transient non-mapped attachment,
        never persisted or serialized; consumers pick it up after the facade call (without a plan
        the existing single-candidate behavior is kept).
        """
        capabilities, is_omni = resolved.profile.legacy_capability_view(resolved.provider)
        key = ModelApiKey(
            id=resolved.channel_id,
            model_name=resolved.model_name,
            provider=str(resolved.provider),
            api_key=resolved.api_key.get_secret_value(),
            api_base=resolved.base_url
            or get_default_provider_api_base(resolved.provider, resolved.profile.type),
            capability=[str(item) for item in capabilities],
            is_omni=is_omni,
            input_modalities=[str(item) for item in resolved.profile.input_modalities],
            output_modalities=[str(item) for item in resolved.profile.output_modalities],
            features=[str(item) for item in resolved.profile.features],
        )
        key.failover_plan = failover_plan
        return ModelApiKeyService._stamp_usage_attribution(
            key, resolved.tenant_id, resolved.model_config_id, resolved.channel_id
        )

    @staticmethod
    def _select_legacy_key(model_config: ModelConfig) -> Optional[ModelApiKey]:
        """Legacy (association) key selection: round_robin → least used; otherwise the first."""
        api_keys = [key for key in model_config.api_keys if key.is_active]
        if not api_keys:
            return None

        # Round-robin: pick the least used; ties go to the earliest used
        if model_config.load_balance_strategy == LoadBalanceStrategy.ROUND_ROBIN:
            return min(api_keys, key=lambda x: (int(x.usage_count or "0"), x.last_used_at or datetime.min))

        # Otherwise return the first key
        return api_keys[0]

    @staticmethod
    def _is_public_speedbear_model(model_config: ModelConfig) -> bool:
        return (
            model_config.provider == ModelProvider.SPEEDBEAR
            and bool(model_config.is_public)
        )

    @staticmethod
    def _build_speedbear_runtime_api_key(
        db: Session,
        model_config: ModelConfig,
        tenant_id: uuid.UUID | None,
    ) -> ModelApiKey:
        """Legacy read path for speedbear public models in off mode: the bound gateway key + SpeedBear gateway address (transient shell, never persisted)."""
        if not tenant_id:
            raise BusinessException(
                "SpeedBear 公共模型运行时缺少租户上下文",
                BizCode.AGENT_CONFIG_MISSING,
            )

        from premium.platform_admin.models import TenantSpeedBearBinding

        binding = (
            db.query(TenantSpeedBearBinding)
            .filter(TenantSpeedBearBinding.tenant_id == tenant_id)
            .first()
        )
        if not binding:
            raise BusinessException(
                "当前租户未绑定 SpeedBear Key，请联系平台管理员初始化",
                BizCode.AGENT_CONFIG_MISSING,
            )

        capabilities, is_omni = legacy_view(model_config)
        columns = profile_columns(model_config)
        return ModelApiKey(
            model_name=model_config.name,
            provider=ModelProvider.SPEEDBEAR,
            api_key=binding.gateway_api_key,
            api_base=f"{current_settings().speedbear_base_url.rstrip('/')}/api/v1",
            capability=capabilities,
            is_omni=is_omni,
            input_modalities=columns["input_modalities"],
            output_modalities=columns["output_modalities"],
            features=columns["features"],
        )

    @staticmethod
    async def _build_speedbear_runtime_api_key_async(
        db: AsyncSession,
        model_config: ModelConfig,
        tenant_id: uuid.UUID | None,
    ) -> ModelApiKey:
        """Async version of _build_speedbear_runtime_api_key."""
        if not tenant_id:
            raise BusinessException(
                "SpeedBear 公共模型运行时缺少租户上下文",
                BizCode.AGENT_CONFIG_MISSING,
            )

        from sqlalchemy import select

        from premium.platform_admin.models import TenantSpeedBearBinding

        result = await db.execute(
            select(TenantSpeedBearBinding).filter(
                TenantSpeedBearBinding.tenant_id == tenant_id
            )
        )
        binding = result.scalars().first()
        if not binding:
            raise BusinessException(
                "当前租户未绑定 SpeedBear Key，请联系平台管理员初始化",
                BizCode.AGENT_CONFIG_MISSING,
            )

        capabilities, is_omni = legacy_view(model_config)
        columns = profile_columns(model_config)
        return ModelApiKey(
            model_name=model_config.name,
            provider=ModelProvider.SPEEDBEAR,
            api_key=binding.gateway_api_key,
            api_base=f"{current_settings().speedbear_base_url.rstrip('/')}/api/v1",
            capability=capabilities,
            is_omni=is_omni,
            input_modalities=columns["input_modalities"],
            output_modalities=columns["output_modalities"],
            features=columns["features"],
        )

    @staticmethod
    def resolve_invoke_ref(
        db: Session,
        model_config_id: uuid.UUID,
        tenant_id: uuid.UUID | str | None = None,
    ) -> Optional[RemoteInvokeRef]:
        """Runtime reference (non-decrypting): only requires that the config exists and is active; credentials and routing live in the model service.

        Returning None aligns with ``get_available_api_key``'s "unavailable" semantics (missing or
        inactive config) so callers keep their existing branch; missing credentials, unavailable
        channels etc. now fail loudly on the service side at invoke time.
        """
        model_config = ModelConfigRepository.get_by_id(db, model_config_id, tenant_id=tenant_id)
        if not model_config or not model_config.is_active:
            return None
        return RemoteInvokeRef(
            config_id=model_config.id,
            tenant_id=uuid.UUID(str(tenant_id or model_config.tenant_id)),
        )

    @staticmethod
    async def resolve_invoke_ref_async(
        db: AsyncSession,
        model_config_id: uuid.UUID,
        tenant_id: uuid.UUID | str | None = None,
    ) -> Optional[RemoteInvokeRef]:
        """Async version of resolve_invoke_ref (same semantics as the sync version)."""
        model_config = await ModelConfigRepository.get_by_id_async(
            db, model_config_id, tenant_id=tenant_id
        )
        if not model_config or not model_config.is_active:
            return None
        return RemoteInvokeRef(
            config_id=model_config.id,
            tenant_id=uuid.UUID(str(tenant_id or model_config.tenant_id)),
        )

    @staticmethod
    def get_available_api_key(
        db: Session,
        model_config_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> Optional[ModelApiKey]:
        """Get an available API key (channel resolution switch off/prefer/only; the return shape matches the legacy path).

        tenant_id is required: the tenant boundary is checked by the resolver's `_validate_config_access`;
        omitting it would resolve under the config's own tenant, so implicit cross-tenant access is
        forbidden (an audit-driven tightening).
        """
        model_config = ModelConfigRepository.get_by_id(db, model_config_id)
        if not model_config:
            return None

        if not model_config.is_active:
            return None

        mode = resolution_mode()
        if mode != "off":
            try:
                if model_config.provider == ModelProvider.COMPOSITE:
                    outcome = resolve_composite_plan_sync(db, model_config, tenant_id=tenant_id)
                else:
                    outcome = resolve_config_plan_sync(
                        db, model_config.id, tenant_id=tenant_id, config_row=model_config
                    )
            except (ModelConfigInactiveError, ModelConfigDeprecatedError):
                return None
            except SpeedbearChannelMissingError as exc:
                raise BusinessException(
                    "当前租户未绑定 SpeedBear Key，请联系平台管理员初始化",
                    BizCode.SPEEDBEAR_CHANNEL_MISSING,
                ) from exc
            except CredentialDecryptError as exc:
                if mode == "only":
                    logger.warning(
                        "channel credential decrypt failed for config %s: %s",
                        model_config.id,
                        exc,
                    )
                    raise BusinessException(
                        "模型渠道凭据解密失败，请前往渠道管理重新登记该 API Key",
                        BizCode.CREDENTIAL_DECRYPT_ERROR,
                    ) from exc
                _record_resolution_fallback(model_config.id, exc)
            except RedBearModelError as exc:
                if mode == "only":
                    logger.warning(
                        "channel resolution failed for config %s: %s", model_config.id, exc
                    )
                    return None
                _record_resolution_fallback(model_config.id, exc)
            else:
                if outcome is None:
                    return None
                return ModelApiKeyService._runtime_api_key_from_resolved(
                    outcome.resolved, failover_plan=outcome.plan
                )

        if mode == "off" and ModelApiKeyService._is_public_speedbear_model(model_config):
            speedbear_key = ModelApiKeyService._build_speedbear_runtime_api_key(
                db, model_config, tenant_id
            )
            return ModelApiKeyService._stamp_usage_attribution(
                speedbear_key, tenant_id, model_config.id
            )

        legacy_key = ModelApiKeyService._select_legacy_key(model_config)
        if legacy_key is None:
            return None
        ModelApiKeyService._stamp_profile_columns(legacy_key, model_config)
        return ModelApiKeyService._stamp_usage_attribution(
            legacy_key, tenant_id, model_config.id
        )

    @staticmethod
    async def get_available_api_key_async(
        db: AsyncSession,
        model_config_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> Optional[ModelApiKey]:
        """Async version of get_available_api_key (tenant_id required; same semantics as the sync version)."""
        model_config = await ModelConfigRepository.get_by_id_async(db, model_config_id)
        if not model_config:
            return None

        if not model_config.is_active:
            return None

        mode = resolution_mode()
        if mode != "off":
            try:
                if model_config.provider == ModelProvider.COMPOSITE:
                    outcome = await resolve_composite_plan_async(db, model_config, tenant_id=tenant_id)
                else:
                    outcome = await resolve_config_plan_async(
                        db, model_config.id, tenant_id=tenant_id, config_row=model_config
                    )
            except (ModelConfigInactiveError, ModelConfigDeprecatedError):
                return None
            except SpeedbearChannelMissingError as exc:
                raise BusinessException(
                    "当前租户未绑定 SpeedBear Key，请联系平台管理员初始化",
                    BizCode.SPEEDBEAR_CHANNEL_MISSING,
                ) from exc
            except CredentialDecryptError as exc:
                if mode == "only":
                    logger.warning(
                        "channel credential decrypt failed for config %s: %s",
                        model_config.id,
                        exc,
                    )
                    raise BusinessException(
                        "模型渠道凭据解密失败，请前往渠道管理重新登记该 API Key",
                        BizCode.CREDENTIAL_DECRYPT_ERROR,
                    ) from exc
                _record_resolution_fallback(model_config.id, exc)
            except RedBearModelError as exc:
                if mode == "only":
                    logger.warning(
                        "channel resolution failed for config %s: %s", model_config.id, exc
                    )
                    return None
                _record_resolution_fallback(model_config.id, exc)
            else:
                if outcome is None:
                    return None
                return ModelApiKeyService._runtime_api_key_from_resolved(
                    outcome.resolved, failover_plan=outcome.plan
                )

        if mode == "off" and ModelApiKeyService._is_public_speedbear_model(model_config):
            speedbear_key = await ModelApiKeyService._build_speedbear_runtime_api_key_async(
                db, model_config, tenant_id
            )
            return ModelApiKeyService._stamp_usage_attribution(
                speedbear_key, tenant_id, model_config.id
            )

        legacy_key = ModelApiKeyService._select_legacy_key(model_config)
        if legacy_key is None:
            return None
        ModelApiKeyService._stamp_profile_columns(legacy_key, model_config)
        return ModelApiKeyService._stamp_usage_attribution(
            legacy_key, tenant_id, model_config.id
        )

    @staticmethod
    async def resolve_runtime_api_key_bridge_or_raise_async(
        db: Session | AsyncSession,
        model_config_id: uuid.UUID,
        tenant_id: uuid.UUID | None = None,
    ) -> ModelApiKey:
        """Fetch the runtime API key; on an empty result a cold path restores precise error semantics (adapts to sync/async sessions).

        `get_available_api_key(_async)` uniformly returns None for missing/deprecated/inactive
        models and missing credentials, leaving callers only a generic message. This re-reads the
        config row to restore the first three cases as precise error codes
        (MODEL_NOT_FOUND / MODEL_DEPRECATED / MODEL_CONFIG_INVALID); the rest keeps the original semantics.
        """
        async_session = isinstance(db, AsyncSession)
        api_key = (
            await ModelApiKeyService.get_available_api_key_async(
                db, model_config_id, tenant_id=tenant_id
            )
            if async_session
            else ModelApiKeyService.get_available_api_key(
                db, model_config_id, tenant_id=tenant_id
            )
        )
        if api_key is not None:
            return api_key

        model = (
            await ModelConfigService.get_model_by_id_async(
                db, model_config_id, tenant_id=tenant_id
            )
            if async_session
            else ModelConfigService.get_model_by_id(
                db, model_config_id, tenant_id=tenant_id
            )
        )
        if not model.is_active:
            raise BusinessException(
                "当前模型未启用，请在模型配置中确认 API Key 和 URL 已配置后启用模型",
                BizCode.MODEL_CONFIG_INVALID,
            )
        raise BusinessException("没有可用的 API Key", BizCode.AGENT_CONFIG_MISSING)

    @staticmethod
    def record_api_key_usage(db: Session, api_key_id: uuid.UUID | None) -> bool:
        """Record API key usage (when channel resolution is not off, the legacy counter retires and usage events take over).

        The resolved shell's .id is a channel_id (no such row in the legacy table), so writing the
        legacy table is meaningless and costly; off mode keeps the legacy write for rollback safety.
        Least-used derivation comes from usage_records.
        """
        if not api_key_id:
            return False
        if resolution_mode() != "off":
            return True
        success = ModelApiKeyRepository.update_usage(db, api_key_id)
        if success:
            db.commit()
        return success

    @staticmethod
    async def record_api_key_usage_bridge_async(
        db: Session | AsyncSession,
        api_key_id: uuid.UUID | None,
    ) -> bool:
        """API key usage counter working with both Session and AsyncSession (same counter-retirement semantics as the sync version)."""
        if not api_key_id:
            return False
        if resolution_mode() != "off":
            return True

        if isinstance(db, AsyncSession):
            api_key = await db.get(ModelApiKey, api_key_id)
            if not api_key:
                return False

            current_count = int(api_key.usage_count or "0")
            api_key.usage_count = str(current_count + 1)
            api_key.last_used_at = utcnow_naive()
            await db.commit()
            return True

        return ModelApiKeyService.record_api_key_usage(db, api_key_id)


class ModelBaseService:
    """Base model service."""

    @staticmethod
    def get_model_base_list(db: Session, query: model_schema.ModelBaseQuery, tenant_id: uuid.UUID = None) -> List:
        models = ModelBaseRepository.get_list(db, query)

        added_ids = (
            ModelBaseRepository.get_added_model_ids(db, tenant_id, [m.id for m in models])
            if tenant_id else set()
        )

        provider_groups = {}
        for m in models:
            model_dict = wire_model_base(m).model_dump()
            if tenant_id:
                model_dict['is_added'] = m.id in added_ids

            provider = m.provider
            if provider not in provider_groups:
                provider_groups[provider] = {
                    "provider": provider,
                    "models": []
                }
            provider_groups[provider]["models"].append(model_dict)

        return list(provider_groups.values())

    @staticmethod
    def get_model_base_by_id(db: Session, model_base_id: uuid.UUID):
        model = ModelBaseRepository.get_by_id(db, model_base_id)
        if not model:
            raise BusinessException("基础模型不存在", BizCode.MODEL_NOT_FOUND)
        return model
