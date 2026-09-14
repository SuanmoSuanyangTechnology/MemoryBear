import base64
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from typing import TYPE_CHECKING, List, Optional, Dict, Any, Sequence, Tuple
import uuid
import math
import time
import asyncio
from urllib.parse import urlparse

from pydantic import SecretStr

from app.models.models_model import ModelConfig, ModelApiKey, ModelType, LoadBalanceStrategy, ModelProvider
from app.repositories.model_repository import ModelConfigRepository, ModelApiKeyRepository, ModelBaseRepository
from app.schemas import model_schema
from app.schemas.model_schema import (
    ModelConfigCreate, ModelConfigUpdate,
    ModelConfigQuery, ModelConfigQueryNew, ModelInfo
)
from app.core.config import settings
from app.core.model_provider_config import (
    get_default_provider_api_base,
    is_local_deployment_provider,
    validate_api_base_against_default,
)
from app.core.logging_config import get_business_logger
from app.schemas.response_schema import PageData, PageMeta
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode
from app.core.utils.datetime_utils import utcnow_naive
from app.utils.redis_cache import (invalidate_workspace_model_options, get_json_async, set_json_async,
                                   CACHE_MISS, invalidate_runtime_model_info,
                                   invalidate_runtime_model_info_batch)

from redbear_model import (
    CredentialDecryptError,
    FailoverPlan,
    ModelConfigInactiveError,
    RedBearModelError,
    ResolvedModelConfig,
    SpeedbearChannelMissingError,
)

from app.services.channel_registry import (
    candidate_channels_batch_sync,
    candidate_channels_sync,
    resolve_composite_plan_async,
    resolve_composite_plan_sync,
    resolve_config_plan_async,
    resolve_config_plan_sync,
)
from app.services.channel_service import ChannelService
from app.services.model_impact_service import collect_model_impact

if TYPE_CHECKING:
    from redbear_model import ImageEmbeddingContent

logger = get_business_logger()

# 渠道解析开关（M3 切流）：off=纯旧路径 / prefer=v2 优先+旧兜底 / only=纯 v2
# off 档保留 speedbear 公共模型绑定表旧读分支（回滚兜底）；prefer/only 一律走渠道解析
_RESOLUTION_MODES = ("off", "prefer", "only")

# 连接类故障标记：命中即按"网络不可达/超时"归类，而非密钥或参数问题。
# 覆盖 openai SDK（APITimeoutError/APIConnectionError）、httpx（Connect/ReadTimeout、
# ConnectError）、requests 与 botocore（Max retries exceeded / EndpointConnectionError）。
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

# 认证类故障标记：openai SDK 以异常类名（AuthenticationError）暴露；dashscope 原生链路
# （embedding 的 DashScopeEmbeddings 抛 ValueError、rerank 的 _dashscope_error_message 抛
# RuntimeError）只带 "status_code: 401 \n code: InvalidApiKey \n message: ..." 文本，
# 无专用异常类，故补文本标记。
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
    mode = (getattr(settings, "MODEL_CHANNEL_RESOLUTION", "") or "prefer").strip().lower()
    if mode not in _RESOLUTION_MODES:
        logger.warning("Unknown MODEL_CHANNEL_RESOLUTION=%r; using 'prefer'", mode)
        return "prefer"
    return mode


def channel_resolution_fallback_hits() -> int:
    """prefer 期兜底命中计数（观察迁移遗漏；归零后再收 only）。"""
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
    capability: list | None,
) -> "ResolvedModelConfig":
    from redbear_model import (
        ModelCapability as SharedModelCapability,
    )
    from redbear_model import (
        ModelProvider as SharedModelProvider,
    )
    from redbear_model import (
        ModelRuntimeOptions,
        ResolvedModelConfig,
    )
    from redbear_model import (
        ModelType as SharedModelType,
    )

    return ResolvedModelConfig(
        model_config_id=_MODEL_VALIDATION_CONFIG_ID,
        key_id=_MODEL_VALIDATION_KEY_ID,
        tenant_id=_MODEL_VALIDATION_TENANT_ID,
        provider=SharedModelProvider(_enum_value(provider)),
        model_type=SharedModelType(_enum_value(model_type)),
        model_name=model_name,
        api_key=SecretStr(api_key),
        base_url=api_base,
        capabilities=tuple(
            SharedModelCapability(_enum_value(item)) for item in (capability or [])
        ),
        runtime=ModelRuntimeOptions(timeout_s=10.0, max_retries=0),
    )


_DASHSCOPE_ASR_MODEL = "qwen3-asr-flash-filetrans"
_DASHSCOPE_VIDEO_MODEL = "qwen3.5-omni-plus-2026-03-15"
_MEDIA_VALIDATION_TIMEOUT_MS = 60_000
_MEDIA_VALIDATION_VIDEO_PROMPT = "Describe this video briefly."


def is_media_validation_model(provider: str, model_name: str) -> bool:
    return _enum_value(provider) == "dashscope" and model_name in {
        _DASHSCOPE_ASR_MODEL, _DASHSCOPE_VIDEO_MODEL,
    }


async def _validate_media_model(
    *, model_name: str, provider: str, api_key: str, api_base: str | None,
    model_type: str, capability: list | None, test_media_url: SecretStr | str | None,
) -> dict[str, Any]:
    from redbear_model import (
        AudioTaskStatus,
        AudioTranscriptionRequest,
        MediaCallOptions,
        VideoUnderstandingRequest,
    )
    from redbear_model.runtime.audio import RedBearAudioTranscriber
    from redbear_model.runtime.video_understanding import RedBearVideoUnderstanding

    started = time.time()
    runtime = None
    try:
        if not test_media_url:
            raise ValueError("test_media_url is required for media validation")
        media_url = (test_media_url.get_secret_value()
                     if isinstance(test_media_url, SecretStr) else test_media_url)
        expected_types = {"asr"} if model_name == _DASHSCOPE_ASR_MODEL else {"llm", "chat"}
        if _enum_value(model_type) not in expected_types:
            raise ValueError("Media model type does not match its runtime")
        capabilities = list(capability or [])
        if model_name == _DASHSCOPE_VIDEO_MODEL and "video" not in capabilities:
            capabilities.append("video")
        config = _shared_validation_config(
            model_name=model_name, provider=provider, api_key=api_key,
            api_base=api_base, model_type=model_type, capability=capabilities,
        )
        options = MediaCallOptions(call_timeout_ms=_MEDIA_VALIDATION_TIMEOUT_MS)
        if model_name == _DASHSCOPE_ASR_MODEL:
            request = AudioTranscriptionRequest(file_url=media_url)
            runtime = RedBearAudioTranscriber(config, options=options)
            result = await runtime.asubmit(request)
            if result.status not in {AudioTaskStatus.PENDING, AudioTaskStatus.RUNNING,
                                     AudioTaskStatus.SUCCEEDED}:
                raise ValueError("Audio submission was not accepted")
            stage = "submitted"
            message = "请求受理验证通过；尚未验证转录完成或音频内容"
        else:
            request = VideoUnderstandingRequest(video_url=media_url, prompt=_MEDIA_VALIDATION_VIDEO_PROMPT)
            runtime = RedBearVideoUnderstanding(config, options=options)
            result = await runtime.ainvoke(request)
            if not result.text.strip() or result.finish_reason != "stop":
                raise ValueError("Video understanding did not complete")
            stage = "completed"
            message = "视频理解验证通过"
        return {"valid": True, "message": message, "response": None, "error": None,
                "usage": result.usage.model_dump() if result.usage else None,
                "elapsed_time": time.time() - started, "validation_stage": stage}
    except Exception:  # noqa: BLE001 - redact all provider and transport error details.
        # Provider errors and signed sample URLs must never enter API logs/responses.
        error = ("test_media_url is required for media validation" if not test_media_url
                 else "Media validation failed or submission is uncertain; no credential was saved")
        return {"valid": False, "message": "媒体验证未通过", "response": None,
                "error": error, "error_type": "MediaValidationError", "usage": None,
                "elapsed_time": time.time() - started}
    finally:
        if runtime is not None:
            try:
                await runtime.aclose()
            except Exception:  # noqa: BLE001 - cleanup errors may contain signed URLs.
                raise RuntimeError("Media validation cleanup failed") from None


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
    """本地部署提供商必须显式配置实际服务地址。"""
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
    """非空 api_base 必须是 http(s):// 开头的完整地址（留空合法，走默认）。

    bedrock 例外：其 api_base 承载 AWS region（如 us-east-1，映射 region_name），非 URL。
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
    """bedrock 的 api_key 约定为 access_key_id:secret_access_key（缺任一半，运行时
    ChatBedrock 构造期抛 pydantic ValidationError，报错原文不可读，故提前拦截）。"""
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
    """运行时不读取自定义 api_base 的组合，只允许留空或官方基地址。"""
    error = validate_api_base_against_default(provider, api_base, model_type)
    if error:
        raise BusinessException(error, BizCode.INVALID_PARAMETER)


def _model_option_cache_state(model: ModelConfig) -> tuple[uuid.UUID | None, bool]:
    provider = getattr(model.provider, "value", model.provider)
    public_speedbear = (
        provider == ModelProvider.SPEEDBEAR.value and bool(model.is_public)
    )
    return model.tenant_id, public_speedbear


def _invalidate_model_option_states(*states: tuple[uuid.UUID | None, bool]) -> None:
    invalidate_workspace_model_options(
        (tenant_id for tenant_id, _ in states),
        public_catalog_changed=any(is_public for _, is_public in states),
    )


def _model_base_configs(db: Session, model_base_id: uuid.UUID) -> list[ModelConfig]:
    return db.query(ModelConfig).filter(ModelConfig.model_id == model_base_id).all()


def _invalidate_model_base_caches(configs: Sequence[ModelConfig]) -> None:
    """基础模型弃用态变更后：清运行期模型缓存（按租户精确删）与工作空间模型选项缓存。"""
    if not configs:
        return
    config_ids = [config.id for config in configs]
    invalidate_runtime_model_info_batch(config_ids)
    by_tenant: dict[uuid.UUID, list[uuid.UUID]] = {}
    for config in configs:
        if config.tenant_id is not None:
            by_tenant.setdefault(config.tenant_id, []).append(config.id)
    for tenant_id, ids in by_tenant.items():
        invalidate_runtime_model_info_batch(ids, tenant_id)
    _invalidate_model_option_states(*[_model_option_cache_state(config) for config in configs])


def _probe_availability(
    db: Session, rows: Sequence[ModelConfig], tenant_id: uuid.UUID | None
) -> dict[uuid.UUID, bool]:
    """批量渠道可用性探测（列表页固定 ≤2 次查询）；tenant_id 缺失（公共目录）时不探测。"""
    if tenant_id is None or not rows:
        return {}
    return {
        row_id: bool(chain)
        for row_id, chain in candidate_channels_batch_sync(db, rows, tenant_id).items()
    }


def _with_availability(
    model: ModelConfig, availability: dict[uuid.UUID, bool]
) -> model_schema.ModelConfig:
    item = model_schema.ModelConfig.model_validate(model)
    if model.id in availability:
        item.is_available = availability[model.id]
    return item


class ModelConfigService:
    """模型配置服务"""

    @staticmethod
    def is_model_available(
        db: Session, model_config: ModelConfig, tenant_id: uuid.UUID | None = None
    ) -> bool | None:
        """单模型渠道可用性（详情展示）；tenant_id 缺失时返回 None（未探测）。"""
        if tenant_id is None:
            return None
        return bool(candidate_channels_sync(db, model_config, tenant_id=tenant_id))

    @staticmethod
    def get_model_by_id(db: Session, model_id: uuid.UUID, tenant_id: uuid.UUID | None = None) -> ModelConfig:
        """根据ID获取模型配置"""
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
    async def get_model_by_id_async(
            db: AsyncSession,
            model_id: uuid.UUID,
            tenant_id: uuid.UUID | None = None,
    ) -> ModelConfig:
        """Async version of get_model_by_id with the same availability checks."""
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
    async def get_model_by_id_async(
        db: AsyncSession,
        model_id: uuid.UUID,
        tenant_id: uuid.UUID | None = None,
    ) -> ModelConfig:
        """根据ID异步获取模型配置"""
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
    async def get_runtime_model_info_async(
        db: AsyncSession,
        model_id: uuid.UUID,
        tenant_id: uuid.UUID | None = None,
    ) -> ModelInfo:
        """统一获取运行时模型信息（异步）。

        缓存只承载非密字段（model_type，300s）；凭据每次现解（spec §7：解密收敛在
        resolver 取凭据处，明文不进跨请求缓存）。旧格式（含 api_key）缓存不采信。
        """
        if tenant_id is None:
            # 无租户上下文（如变量池缺失）时显式退化为 config 自身租户，与解析层兜底语义一致
            model_row = await ModelConfigService.get_model_by_id_async(db, model_id, tenant_id=None)
            tenant_id = model_row.tenant_id
        cache_key = f"runtime_model_info:{model_id}:{tenant_id}"
        cached = await get_json_async(cache_key)
        cached_model_type: ModelType | None = None
        if (
            cached is not CACHE_MISS
            and isinstance(cached, dict)
            and "api_key" not in cached
            and isinstance(cached.get("model_type"), str)
        ):
            try:
                cached_model_type = ModelType(cached["model_type"])
            except ValueError:
                cached_model_type = None

        api_key = await ModelApiKeyService.get_available_api_key_async(
            db,
            model_id,
            tenant_id=tenant_id,
        )
        if not api_key:
            # 冷路径补全错误语义（模型不存在/已弃用/未启用/缺少凭据）
            model = await ModelConfigService.get_model_by_id_async(
                db,
                model_id,
                tenant_id=tenant_id,
            )
            if not model.is_active:
                raise BusinessException(
                    "当前模型未启用，请在模型配置中确认 API Key 和 URL 已配置后启用模型",
                    BizCode.MODEL_CONFIG_INVALID,
                )
            raise BusinessException("模型配置缺少 API Key", BizCode.INVALID_PARAMETER)

        if cached_model_type is None:
            model = await ModelConfigService.get_model_by_id_async(
                db,
                model_id,
                tenant_id=tenant_id,
            )
            cached_model_type = ModelType(model.type)
            await set_json_async(cache_key, {"model_type": cached_model_type.value}, ttl=300)

        return ModelInfo(
            model_name=api_key.model_name,
            model_type=cached_model_type,
            api_key=api_key.api_key,
            api_base=api_key.api_base,
            provider=api_key.provider,
            is_omni=api_key.is_omni,
            capability=api_key.capability,
            tenant_id=api_key.tenant_id,
            model_config_id=api_key.model_config_id,
            channel_id=api_key.channel_id,
            failover_plan=api_key.failover_plan,
        )

    @staticmethod
    def get_model_list(db: Session, query: ModelConfigQuery, tenant_id: uuid.UUID | None = None) -> PageData:
        """获取模型配置列表（含渠道可用性：候选链非空 = True）"""
        models, total = ModelConfigRepository.get_list(db, query, tenant_id=tenant_id)
        pages = math.ceil(total / query.pagesize) if total > 0 else 0

        availability = _probe_availability(db, models, tenant_id)
        return PageData(
            page=PageMeta(
                page=query.page,
                pagesize=query.pagesize,
                total=total,
                hasnext=query.page < pages
            ),
            items=[
                _with_availability(model, availability)
                for model in models
            ]
        )

    @staticmethod
    def get_model_list_new(db: Session, query: ModelConfigQueryNew, tenant_id: uuid.UUID | None = None) -> List[dict]:
        """获取模型配置列表（按 provider 分组，含渠道可用性）"""
        provider_groups, total = ModelConfigRepository.get_list_new(db, query, tenant_id=tenant_id)
        _ = total

        rows = [model for models in provider_groups.values() for model in models]
        availability = _probe_availability(db, rows, tenant_id)

        items = []
        for provider, models in provider_groups.items():
            # 验证每个模型并封装分组信息
            validated_models = [_with_availability(model, availability) for model in models]
            tags = list({model.type for model in validated_models})
            group_item = {
                "provider": provider,  # 服务商名称
                "logo": validated_models[0].logo,
                "tags": tags,
                "models": validated_models  # 该服务商下的所有模型
            }
            items.append(group_item)

        return items

    @staticmethod
    def get_model_by_name(db: Session, name: str, provider: str | None = None,
                          tenant_id: uuid.UUID | None = None) -> ModelConfig:
        """根据名称获取模型配置"""
        model = ModelConfigRepository.get_by_name(db, name, provider=provider, tenant_id=tenant_id)
        if not model:
            raise BusinessException("模型配置不存在", BizCode.MODEL_NOT_FOUND)
        return model

    @staticmethod
    def search_models_by_name(db: Session, name: str, tenant_id: uuid.UUID | None = None, limit: int = 10) -> List[
        ModelConfig]:
        """按名称模糊匹配获取模型配置列表"""
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
        is_omni: bool = False,
        capability: Optional[list] = None,
        test_media_url: SecretStr | str | None = None,
    ) -> Dict[str, Any]:
        """验证模型配置是否有效

        Args:
            db: 数据库会话
            model_name: 模型名称
            provider: 提供商
            api_key: API密钥
            api_base: API基础URL
            model_type: 模型类型 (llm/chat/embedding/rerank)
            test_message: 测试消息
            is_omni: 是否为Omni模型
            capability: 模型能力列表

        Returns:
            Dict: 验证结果
        """
        if is_media_validation_model(provider, model_name):
            return await _validate_media_model(
                model_name=model_name, provider=provider, api_key=api_key,
                api_base=api_base, model_type=model_type, capability=capability,
                test_media_url=test_media_url,
            )
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
                validation_capability = list(capability or [])
                if "vision" not in {
                    _enum_value(item) for item in validation_capability
                }:
                    validation_capability.append("vision")
                shared_config = _shared_validation_config(
                    model_name=model_name,
                    provider=provider_lower,
                    api_key=api_key,
                    api_base=api_base,
                    model_type=model_type_lower,
                    capability=validation_capability,
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

            from app.core.models import RedBearLLM, RedBearRerank
            from app.core.models.base import RedBearModelConfig
            from app.core.models.embedding import RedBearEmbeddings

            model_config = RedBearModelConfig(
                model_name=model_name,
                provider=provider,
                api_key=api_key,
                base_url=api_base,
                is_omni=is_omni,
                capability=capability,
                timeout=10.0,
                max_retries=0,
            )

            # 根据模型类型选择不同的验证方式
            if model_type_lower in ["llm", "chat"]:
                # LLM/Chat 模型验证 - 统一使用字符串输入
                llm = RedBearLLM(model_config, type=ModelType.LLM if model_type_lower == "llm" else ModelType.CHAT)
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
                # Embedding 模型验证
                # 统一使用 RedBearEmbeddings（自动支持火山引擎多模态）
                embedding = RedBearEmbeddings(model_config)
                test_texts = [test_message, "测试文本"]

                # 火山引擎使用 embed_batch，其他使用 embed_documents
                if provider.lower() == "volcano":
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
                # Rerank 模型验证（在线程中运行同步方法）
                rerank = RedBearRerank(model_config)
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
                # 图片生成模型验证
                from app.core.models.generation import RedBearImageGenerator

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
                # 视频生成模型验证
                from app.core.models.generation import RedBearVideoGenerator

                generator = RedBearVideoGenerator(model_config)
                result = await generator.agenerate(
                    prompt="a cute panda playing in bamboo forest",
                    duration=5
                )
                elapsed_time = time.time() - start_time

                # 视频生成是异步任务，返回任务ID
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
            # 提取详细的错误信息
            error_message = (
                "Qwen3-VL 模型验证失败"
                if is_qwen3_vl_request
                else str(e)
            )
            error_type = type(e).__name__
            # 特殊处理常见的错误类型
            if "unsupported countries" in error_message.lower() or "unsupported region" in error_message.lower():
                # 区域/国家限制（适用于所有提供商）
                error_message = "区域限制: 该模型在当前区域或国家/地区不可用，请检查提供商的服务区域限制"
            elif "ValidationException" in error_type or "ValidationException" in error_message:
                # 其他验证错误
                if "access denied" in error_message.lower():
                    error_message = "访问被拒绝: 请检查 API 凭证和权限配置"
                else:
                    error_message = f"验证失败: {error_message}"
            elif any(
                marker in f"{error_type} {error_message}".lower()
                for marker in _AUTH_ERROR_MARKERS
            ):
                error_message = "认证失败: API Key 无效或已过期"
            elif (
                "aws_access_key_id" in error_message
                and "aws_secret_access_key" in error_message
            ):
                # ChatBedrock/BedrockEmbeddings 构造期凭据不完整（pydantic 报错原文不可读）
                error_message = (
                    "认证失败: Bedrock 凭据不完整，API Key 需要按 "
                    "access_key_id:secret_access_key 格式填写（英文冒号分隔）"
                )
            elif any(
                marker in f"{error_type} {error_message}".lower()
                for marker in _CONNECTIVITY_ERROR_MARKERS
            ):
                # 报错时必须给出实际请求地址，否则无法区分"密钥错"与"网络不通"；
                # 按地址来源分文案：用户配置的地址 vs 官方公共端点（后者才引导配网关）
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
            elif "RateLimitError" in error_type or "rate limit" in error_message.lower():
                error_message = "请求频率限制: 已超过 API 调用限制"
            elif "InvalidRequestError" in error_type or "invalid request" in error_message.lower():
                error_message = f"无效请求: {error_message}"
            elif "model_copy" in error_message:
                error_message = "模型消息格式错误: 请确保使用正确的模型类型（LLM/Chat）"

            # 记录详细错误日志
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

    @staticmethod
    def _check_media_model_name(model_data: dict, tenant_id: uuid.UUID) -> None:
        from app.db import get_db_context

        with get_db_context() as db:
            if ModelConfigRepository.get_by_name(
                db, model_data["name"], provider=model_data["provider"], tenant_id=tenant_id,
            ):
                raise BusinessException("模型名称已存在", BizCode.DUPLICATE_NAME)

    @staticmethod
    def _save_media_model(model_data: dict, credential: dict, tenant_id: uuid.UUID,
                          created_by: uuid.UUID | None, stage: str) -> model_schema.ModelConfig:
        from app.db import get_db_context

        with get_db_context() as db:
            try:
                if ModelConfigRepository.get_by_name(
                    db, model_data["name"], provider=model_data["provider"], tenant_id=tenant_id,
                ):
                    raise BusinessException("模型名称已存在", BizCode.DUPLICATE_NAME)
                model = ModelConfigRepository.create(db, {**model_data, "tenant_id": tenant_id})
                ChannelService(db).register_for_model(
                    provider=model_data["provider"], tenant_id=tenant_id,
                    model_name=model_data["name"], api_key=credential["api_key"],
                    api_base=credential["api_base"], remark=credential["remark"],
                    priority=credential["priority"], created_by=created_by,
                )
                db.commit()
                db.refresh(model)
                result = model_schema.ModelConfig.model_validate(model)
                result.validation_stage = stage
                cache_state = _model_option_cache_state(model)
            except Exception:
                db.rollback()
                raise
        _invalidate_model_option_states(cache_state)
        return result

    @staticmethod
    async def _create_media_model(
        model_data: ModelConfigCreate, tenant_id: uuid.UUID, created_by: uuid.UUID | None,
    ) -> model_schema.ModelConfig:
        credential = model_data.credential
        _require_api_base_for_local_provider(model_data.provider, credential.api_base)
        _require_supported_api_base(model_data.provider, credential.api_base, model_data.type)
        if model_data.name == _DASHSCOPE_VIDEO_MODEL and "video" not in model_data.capability:
            raise BusinessException("模型缺少视频理解能力", BizCode.INVALID_PARAMETER)
        snapshot = model_data.model_dump(exclude={"credential"})
        await asyncio.to_thread(ModelConfigService._check_media_model_name, snapshot, tenant_id)
        validation = await ModelConfigService.validate_model_config(
            db=None, model_name=model_data.name, provider=model_data.provider,
            api_key=credential.api_key, api_base=credential.api_base, model_type=model_data.type,
            capability=model_data.capability, test_media_url=credential.test_media_url,
        )
        if not validation["valid"]:
            raise BusinessException(validation["error"], BizCode.INVALID_PARAMETER)
        return await asyncio.to_thread(
            ModelConfigService._save_media_model, snapshot, credential.model_dump(),
            tenant_id, created_by, validation["validation_stage"],
        )

    @staticmethod
    async def create_model(
        db: Session,
        model_data: ModelConfigCreate,
        tenant_id: uuid.UUID,
        created_by: uuid.UUID | None = None,
    ) -> ModelConfig | model_schema.ModelConfig:
        """创建自定义模型：内嵌凭据活体验证通过后，config + 点名渠道单事务落库。

        验证失败拒绝创建（零写入）；网络调用在事务外完成。
        """
        if is_media_validation_model(model_data.provider, model_data.name):
            return await ModelConfigService._create_media_model(model_data, tenant_id, created_by)
        # 检查名称是否已存在（同租户内；先于任何网络调用）
        if ModelConfigRepository.get_by_name(db, model_data.name, provider=model_data.provider, tenant_id=tenant_id):
            raise BusinessException("模型名称已存在", BizCode.DUPLICATE_NAME)

        provider = model_data.provider
        credential = model_data.credential
        _require_api_base_for_local_provider(provider, credential.api_base)
        _require_wellformed_bedrock_credential(provider, credential.api_key)
        _require_wellformed_api_base(provider, credential.api_base, model_data.type)
        _require_supported_api_base(provider, credential.api_base, model_data.type)

        validation_result = await ModelConfigService.validate_model_config(
            db=db,
            model_name=model_data.name,
            provider=provider,
            api_key=credential.api_key,
            api_base=credential.api_base,
            model_type=model_data.type,
            test_message="Hello",
            is_omni=model_data.is_omni,
            capability=model_data.capability,
        )
        if not validation_result["valid"]:
            raise BusinessException(
                f"模型配置验证失败: {validation_result['error']}", BizCode.INVALID_PARAMETER
            )

        model_config_data = model_data.model_dump(exclude={"credential"})
        # 添加租户ID
        model_config_data["tenant_id"] = tenant_id

        try:
            model = ModelConfigRepository.create(db, model_config_data)
            ChannelService(db).register_for_model(
                provider=provider,
                tenant_id=tenant_id,
                model_name=model_data.name,
                api_key=credential.api_key,
                api_base=credential.api_base,
                remark=credential.remark,
                priority=credential.priority,
                created_by=created_by,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        db.refresh(model)
        _invalidate_model_option_states(_model_option_cache_state(model))
        return model

    @staticmethod
    def update_model(db: Session, model_id: uuid.UUID, model_data: ModelConfigUpdate,
                     tenant_id: uuid.UUID | None = None) -> ModelConfig:
        """更新模型配置"""
        existing_model = ModelConfigRepository.get_by_id(db, model_id, tenant_id=tenant_id)
        if not existing_model:
            raise BusinessException("模型配置不存在", BizCode.MODEL_NOT_FOUND)
        old_cache_state = _model_option_cache_state(existing_model)

        if model_data.name and model_data.name != existing_model.name:
            if ModelConfigRepository.get_by_name(db, model_data.name, provider=existing_model.provider,
                                                 tenant_id=tenant_id):
                raise BusinessException("模型名称已存在", BizCode.DUPLICATE_NAME)

        model = ModelConfigRepository.update(db, model_id, model_data, tenant_id=tenant_id)

        db.commit()
        db.refresh(model)
        invalidate_runtime_model_info(model_id)
        _invalidate_model_option_states(old_cache_state, _model_option_cache_state(model))
        return model

    @staticmethod
    def _resolve_composite_members(
        model_data: model_schema.CompositeModelCreate,
    ) -> List[Tuple[str, str]]:
        """成员声明 members[]（(provider, model_name)）去重保序；成员 config 可选、缺失不阻塞。"""
        members: List[Tuple[str, str]] = []
        seen: set[Tuple[str, str]] = set()
        for item in model_data.members or []:
            pair = (item.provider, item.model_name)
            if pair in seen:
                continue
            seen.add(pair)
            members.append(pair)
        return members

    @staticmethod
    def _validate_composite_members(
        db: Session,
        tenant_id: uuid.UUID,
        members: List[Tuple[str, str]],
        model_type,
    ) -> None:
        """成员校验（§10.3）：拒绝嵌套组合；同租户存在普通 config 时校验类型兼容（llm↔chat）。

        组合 name 是别名，真实调用名在成员声明（association 绑定 key 行的 provider/model_name）；
        成员 config 为可选增强——缺失不阻塞（运行期按声明合成快照），存在时类型须兼容。
        不看 `config.is_active`：启用/禁用是 config 自身状态位，不是成员资格闸门。
        """
        if not members:
            return

        rows = ModelConfigRepository.get_members_by_provider_names(db, tenant_id, members)
        request_type = str(model_type)
        compatible_types = {ModelType.LLM.value, ModelType.CHAT.value}
        for provider, model_name in members:
            if provider == ModelProvider.COMPOSITE.value:
                raise BusinessException(
                    f"组合成员 {provider}/{model_name} 不允许嵌套组合模型",
                    BizCode.INVALID_PARAMETER
                )
            member = rows.get((provider, model_name))
            if member is None:
                continue
            config_type = str(member.type)
            if not (config_type == request_type or
                    (config_type in compatible_types and request_type in compatible_types)):
                raise BusinessException(
                    f"组合成员 {provider}/{model_name} 的模型类型 ({member.type}) 与组合模型类型 ({request_type}) 不匹配",
                    BizCode.INVALID_PARAMETER
                )

    @staticmethod
    def _composite_config(
        config: Dict[str, Any] | None, members: List[Tuple[str, str]]
    ) -> Dict[str, Any]:
        """合并写 config["members"]（保留其余 config 键）。"""
        merged = dict(config or {})
        merged["members"] = [
            {"provider": provider, "model_name": model_name}
            for provider, model_name in members
        ]
        return merged

    @staticmethod
    async def create_composite_model(db: Session, model_data: model_schema.CompositeModelCreate,
                                     tenant_id: uuid.UUID) -> ModelConfig:
        """创建组合模型"""
        if ModelConfigRepository.get_by_name(db, model_data.name, provider=ModelProvider.COMPOSITE,
                                             tenant_id=tenant_id):
            raise BusinessException("模型名称已存在", BizCode.DUPLICATE_NAME)

        members = ModelConfigService._resolve_composite_members(model_data)
        ModelConfigService._validate_composite_members(db, tenant_id, members, model_data.type)

        # 创建组合模型（空成员不得悬于启用态）
        model_config_data = {
            "tenant_id": tenant_id,
            "name": model_data.name,
            "type": model_data.type,
            "logo": model_data.logo,
            "description": model_data.description,
            "provider": ModelProvider.COMPOSITE,
            "config": ModelConfigService._composite_config(model_data.config, members),
            "is_active": model_data.is_active and bool(members),
            "is_public": model_data.is_public,
            "is_composite": True
        }
        if "load_balance_strategy" in model_data.model_fields_set:
            model_config_data["load_balance_strategy"] = model_data.load_balance_strategy

        model = ModelConfigRepository.create(db, model_config_data)

        db.commit()
        db.refresh(model)
        _invalidate_model_option_states(_model_option_cache_state(model))
        return model

    @staticmethod
    async def update_composite_model(db: Session, model_id: uuid.UUID, model_data: model_schema.CompositeModelCreate,
                                     tenant_id: uuid.UUID) -> ModelConfig:
        """更新组合模型"""
        existing_model = ModelConfigRepository.get_by_id(db, model_id, tenant_id=tenant_id)
        if not existing_model:
            raise BusinessException("模型配置不存在", BizCode.MODEL_NOT_FOUND)
        old_cache_state = _model_option_cache_state(existing_model)

        if model_data.name and model_data.name != existing_model.name:
            if ModelConfigRepository.get_by_name(db, model_data.name, provider=existing_model.provider,
                                                 tenant_id=tenant_id):
                raise BusinessException("模型名称已存在", BizCode.DUPLICATE_NAME)

        if not existing_model.is_composite:
            raise BusinessException("该模型不是组合模型", BizCode.INVALID_PARAMETER)

        members = ModelConfigService._resolve_composite_members(model_data)
        # 组合类型不可变更（controller 已拒 type），校验锚定既有 type
        ModelConfigService._validate_composite_members(db, tenant_id, members, existing_model.type)

        # 更新基本信息（空成员不得悬于启用态）
        existing_model.name = model_data.name
        # existing_model.type = model_data.type
        existing_model.logo = model_data.logo
        existing_model.description = model_data.description
        existing_model.config = ModelConfigService._composite_config(model_data.config, members)
        existing_model.is_active = model_data.is_active and bool(members)
        existing_model.is_public = model_data.is_public
        if "load_balance_strategy" in model_data.model_fields_set:
            existing_model.load_balance_strategy = model_data.load_balance_strategy

        db.commit()
        db.refresh(existing_model)
        invalidate_runtime_model_info(model_id)
        _invalidate_model_option_states(
            old_cache_state,
            _model_option_cache_state(existing_model),
        )
        return existing_model

    @staticmethod
    def delete_model(db: Session, model_id: uuid.UUID, tenant_id: uuid.UUID | None = None) -> bool:
        """删除模型配置（软删）；仍被业务引用时 409，清单在 ``context["impact"]``。

        组合模型同一路径；引用面见 model_impact_service（含组合被工作空间/应用引用）。
        """
        existing_model = ModelConfigRepository.get_by_id(db, model_id, tenant_id=tenant_id)
        if not existing_model:
            raise BusinessException("模型配置不存在", BizCode.MODEL_NOT_FOUND)
        impact = collect_model_impact(db, [model_id])
        if impact["total"] > 0:
            raise BusinessException(
                f"模型正被 {impact['total']} 处业务引用，无法删除",
                BizCode.RESOURCE_IN_USE,
                context={"impact": impact},
            )
        old_cache_state = _model_option_cache_state(existing_model)

        success = ModelConfigRepository.delete(db, model_id, tenant_id=tenant_id)
        db.commit()
        invalidate_runtime_model_info(model_id)
        _invalidate_model_option_states(old_cache_state)
        return success

class ModelApiKeyService:
    """模型API Key服务"""

    @staticmethod
    def _stamp_usage_attribution(
        api_key: ModelApiKey,
        tenant_id: uuid.UUID | None,
        model_config_id: uuid.UUID | str | None,
        channel_id: uuid.UUID | str | None = None,
    ) -> ModelApiKey:
        """在运行时 key 壳上挂用量事件归属（spec §13.2，非映射属性、不落库）。

        消费方透传给 RedBearModelConfig；tenant/config 缺失时用量事件侧跳过。
        """
        api_key.tenant_id = None if tenant_id is None else str(tenant_id)
        api_key.model_config_id = None if model_config_id is None else str(model_config_id)
        api_key.channel_id = None if channel_id is None else str(channel_id)
        return api_key

    @staticmethod
    def _runtime_api_key_from_resolved(
        resolved: ResolvedModelConfig,
        *,
        failover_plan: FailoverPlan | None = None,
    ) -> ModelApiKey:
        """ResolvedModelConfig → 瞬时 ModelApiKey 兼容壳（不落库；id=channel_id）。

        调用方只消费 .model_name/.api_key/.api_base/.provider/.is_omni/.capability
        与 .id（usage 计数），形状与旧路径一致。

        渠道 api_base 为空（provider 级渠道）时物化 provider 公共基地址（dashscope
        原生 SDK 组合在运行期再剥离为 /api/v1），与 RedBearModelConfig 的补默认
        语义一致；本地部署 provider 无默认地址，保持空并由下游明确报错。

        failover_plan：请求内换渠道计划（spec §11.2），非映射类属瞬时挂载，
        不落库/不序列化；门面消费后自取（无 plan 时保持既有单候选行为）。
        """
        key = ModelApiKey(
            id=resolved.channel_id,
            model_name=resolved.model_name,
            provider=str(resolved.provider),
            api_key=resolved.api_key.get_secret_value(),
            api_base=resolved.base_url
            or get_default_provider_api_base(resolved.provider, resolved.model_type),
            capability=[str(item) for item in resolved.capabilities],
            is_omni=resolved.is_omni,
        )
        key.failover_plan = failover_plan
        return ModelApiKeyService._stamp_usage_attribution(
            key, resolved.tenant_id, resolved.model_config_id, resolved.channel_id
        )

    @staticmethod
    def _select_legacy_key(model_config: ModelConfig) -> Optional[ModelApiKey]:
        """旧表（association）选择：round_robin → 最少使用；否则首个。"""
        api_keys = [key for key in model_config.api_keys if key.is_active]
        if not api_keys:
            return None

        # 如果是轮询策略，按使用次数最少，次数相同则选最早使用的
        if model_config.load_balance_strategy == LoadBalanceStrategy.ROUND_ROBIN:
            return min(api_keys, key=lambda x: (int(x.usage_count or "0"), x.last_used_at or datetime.min))

        # 否则返回第一个
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
        """off 档 speedbear 公共模型旧读路径：绑定的 gateway key + SpeedBear 网关地址（瞬时壳，不落库）。"""
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

        return ModelApiKey(
            model_name=model_config.name,
            provider=ModelProvider.SPEEDBEAR,
            api_key=binding.gateway_api_key,
            api_base=f"{settings.SPEEDBEAR_BASE_URL.rstrip('/')}/api/v1",
            capability=model_config.capability,
            is_omni=model_config.is_omni,
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

        return ModelApiKey(
            model_name=model_config.name,
            provider=ModelProvider.SPEEDBEAR,
            api_key=binding.gateway_api_key,
            api_base=f"{settings.SPEEDBEAR_BASE_URL.rstrip('/')}/api/v1",
            capability=model_config.capability,
            is_omni=model_config.is_omni,
        )

    @staticmethod
    def get_available_api_key(
        db: Session,
        model_config_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> Optional[ModelApiKey]:
        """获取可用的API Key（渠道解析开关 off/prefer/only，返回形状与旧路径一致）。

        tenant_id 必填：租户边界在 resolver `_validate_config_access` 校验，省略等于
        按 config 自身租户解析，禁止隐式跨租户（审计 §2.3 收紧项）。
        """
        model_config = ModelConfigRepository.get_by_id(db, model_config_id)
        if not model_config:
            return None

        if not model_config.is_active:
            return None

        mode = resolution_mode()
        if mode != "off":
            try:
                if model_config.is_composite:
                    outcome = resolve_composite_plan_sync(db, model_config, tenant_id=tenant_id)
                else:
                    outcome = resolve_config_plan_sync(
                        db, model_config.id, tenant_id=tenant_id, config_row=model_config
                    )
            except ModelConfigInactiveError:
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
        return ModelApiKeyService._stamp_usage_attribution(
            legacy_key, tenant_id, model_config.id
        )

    @staticmethod
    async def get_available_api_key_async(
        db: AsyncSession,
        model_config_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> Optional[ModelApiKey]:
        """Async version of get_available_api_key（tenant_id 必填，语义同 sync 版）。"""
        model_config = await ModelConfigRepository.get_by_id_async(db, model_config_id)
        if not model_config:
            return None

        if not model_config.is_active:
            return None

        mode = resolution_mode()
        if mode != "off":
            try:
                if model_config.is_composite:
                    outcome = await resolve_composite_plan_async(db, model_config, tenant_id=tenant_id)
                else:
                    outcome = await resolve_config_plan_async(
                        db, model_config.id, tenant_id=tenant_id, config_row=model_config
                    )
            except ModelConfigInactiveError:
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
        return ModelApiKeyService._stamp_usage_attribution(
            legacy_key, tenant_id, model_config.id
        )

    @staticmethod
    async def get_available_api_key_bridge_async(
        db: Session | AsyncSession,
        model_config_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> Optional[ModelApiKey]:
        if isinstance(db, AsyncSession):
            return await ModelApiKeyService.get_available_api_key_async(
                db,
                model_config_id,
                tenant_id=tenant_id,
            )
        return ModelApiKeyService.get_available_api_key(
            db,
            model_config_id,
            tenant_id=tenant_id,
        )

    @staticmethod
    def record_api_key_usage(db: Session, api_key_id: uuid.UUID | None) -> bool:
        """记录API Key使用（D14：渠道解析非 off 时旧表计数器退役，M4 usage 事件接管）。

        解析壳的 .id 是 channel_id（旧表无此行），写旧表无意义且贵；off 模式
        保持旧写以保回滚安全。least-used 派生由 M4 usage_records 提供。
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
        """兼容 Session/AsyncSession 的 API Key 使用统计（D14 退役语义同 sync 版）"""
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
    """基础模型服务"""

    @staticmethod
    def get_model_base_list(db: Session, query: model_schema.ModelBaseQuery, tenant_id: uuid.UUID = None) -> List:
        models = ModelBaseRepository.get_list(db, query)

        added_ids = (
            ModelBaseRepository.get_added_model_ids(db, tenant_id, [m.id for m in models])
            if tenant_id else set()
        )

        provider_groups = {}
        for m in models:
            model_dict = model_schema.ModelBase.model_validate(m).model_dump()
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

    @staticmethod
    def create_model_base(db: Session, data: model_schema.ModelBaseCreate):
        existing = ModelBaseRepository.get_by_name_and_provider(db, data.name, data.provider)
        if existing:
            raise BusinessException("模型已存在", BizCode.DUPLICATE_NAME)
        model_base = ModelBaseRepository.create(db, data.model_dump())
        db.commit()
        db.refresh(model_base)
        return model_base

    @staticmethod
    def update_model_base(db: Session, model_base_id: uuid.UUID, data: model_schema.ModelBaseUpdate):
        payload = data.model_dump(exclude_unset=True)
        model_base = ModelBaseRepository.update(db, model_base_id, payload)
        if not model_base:
            raise BusinessException("基础模型不存在", BizCode.MODEL_NOT_FOUND)
        db.commit()
        db.refresh(model_base)
        if "is_deprecated" in payload:
            _invalidate_model_base_caches(_model_base_configs(db, model_base_id))
        return model_base

    @staticmethod
    def delete_model_base(db: Session, model_base_id: uuid.UUID) -> dict:
        """软停用基础模型（is_deprecated=True，配置与历史引用保留，运行期报 MODEL_DEPRECATED）。

        返回全量引用面清单（跨租户聚合；停用不阻塞，由调用方透出影响面）。
        """
        model_base = ModelBaseRepository.get_by_id(db, model_base_id)
        if not model_base:
            raise BusinessException("基础模型不存在", BizCode.MODEL_NOT_FOUND)
        configs = _model_base_configs(db, model_base_id)
        impact = collect_model_impact(db, [config.id for config in configs])
        ModelBaseRepository.update(db, model_base_id, {"is_deprecated": True})
        db.commit()
        _invalidate_model_base_caches(configs)
        return impact

    @staticmethod
    def add_model_from_plaza(db: Session, model_base_id: uuid.UUID, tenant_id: uuid.UUID) -> ModelConfig:
        model_base = ModelBaseRepository.get_by_id(db, model_base_id)
        if not model_base:
            raise BusinessException("基础模型不存在", BizCode.MODEL_NOT_FOUND)

        if ModelBaseRepository.check_added_by_tenant(db, model_base_id, tenant_id):
            raise BusinessException("模型已添加", BizCode.DUPLICATE_NAME)

        model_config_data = {
            "model_id": model_base_id,
            "tenant_id": tenant_id,
            "name": model_base.name,
            "provider": model_base.provider,
            "type": model_base.type,
            "logo": model_base.logo,
            "description": model_base.description,
            "capability": model_base.capability,
            "is_omni": model_base.is_omni,
            "is_active": False,
            "is_composite": False
        }
        model_config = ModelConfigRepository.create(db, model_config_data)
        ModelBaseRepository.increment_add_count(db, model_base_id)
        db.commit()
        db.refresh(model_config)
        return model_config
