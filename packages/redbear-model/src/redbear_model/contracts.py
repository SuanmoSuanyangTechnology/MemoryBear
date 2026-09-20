"""Pure model registry and runtime contracts."""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    model_validator,
)

logger = logging.getLogger(__name__)


class ContractModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ModelType(StrEnum):
    """接口族：调用协议/适配器形态，只含已接入事实（契约 v2：原 llm + chat 合并）。"""

    LLM = "llm"
    EMBEDDING = "embedding"
    RERANK = "rerank"
    IMAGE = "image"
    VIDEO = "video"
    ASR = "asr"
    # deprecated（2a 别名窗口，2e 删除）：原独立值 "chat"，仅存量数据仍持该字符串
    CHAT = "llm"

    @classmethod
    def _missing_(cls, value: object) -> ModelType | None:
        """存量字符串读侧归一：`"chat"` → LLM（DB/YAML/事件兼容层）；`"asr"` → ASR（大小写容忍）。"""
        if isinstance(value, str):
            if value.lower() == "chat":
                return cls.LLM
            if value.lower() == "asr":
                return cls.ASR
        return None


class ModelCapability(StrEnum):
    """deprecated（2a 起别名窗口，2e 删除）：能力混装集合，拆为 `input_modalities` /
    `output_modalities` / `ModelFeature` 三侧；本类仅在旧列读取窗口内保留。"""

    VISION = "vision"
    AUDIO = "audio"
    VIDEO = "video"
    THINKING = "thinking"
    THINKING_ONLY = "thinking_only"
    JSON_OUTPUT = "json_output"
    FUNCTION_CALL = "function_call"


class Modality(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"


class ModelFeature(StrEnum):
    """功能开关（原 ModelCapability 功能侧；模态侧移出到 input/output_modalities）。"""

    THINKING = "thinking"             # 可开关：请求侧 deep_thinking=false 生效
    THINKING_ONLY = "thinking_only"   # 恒开不可关：禁止下发关闭参数；thinking_budget 不可配
    JSON_OUTPUT = "json_output"
    FUNCTION_CALL = "function_call"


class CompositeMember(BaseModel):
    """组合模型成员声明（config JSON members[] 的序列化形状，spec §10.3）。"""
    provider: str
    model_name: str


# deprecated（2a–2d 兼容窗口，2e 随 ModelCapability 删）：feature ↔ 旧 capability 双向换算表
_FEATURE_CAPABILITIES = {
    ModelFeature.THINKING: ModelCapability.THINKING,
    ModelFeature.THINKING_ONLY: ModelCapability.THINKING_ONLY,
    ModelFeature.JSON_OUTPUT: ModelCapability.JSON_OUTPUT,
    ModelFeature.FUNCTION_CALL: ModelCapability.FUNCTION_CALL,
}
_MODALITY_CAPABILITIES = (
    (Modality.IMAGE, ModelCapability.VISION),
    (Modality.AUDIO, ModelCapability.AUDIO),
    (Modality.VIDEO, ModelCapability.VIDEO),
)


def legacy_capability_columns(
    *,
    type: ModelType | str,
    provider: ModelProvider | str,
    capabilities: Sequence[ModelCapability | str] = (),
    is_omni: bool = False,
) -> tuple[tuple[Modality, ...], tuple[Modality, ...], tuple[ModelFeature, ...]]:
    """旧列（`type`/`capability`/`is_omni`）→ 三新列（spec §13.4 换算口径，2d backfill 同源）。

    未知 capability 值跳过；`is_omni` 仅 provider=dashscope 参与 output 例外（含 audio）。
    deprecated（2a–2d 兼容窗口，2e 随 ModelCapability 删）。
    """
    model_type = ModelType(type)
    parsed = []
    for value in capabilities:
        try:
            parsed.append(ModelCapability(value))
        except ValueError:
            continue
    values = set(parsed)
    input_modalities = [Modality.TEXT]
    for modality, capability in _MODALITY_CAPABILITIES:
        if capability in values:
            input_modalities.append(modality)
    if model_type in (ModelType.IMAGE, ModelType.VIDEO):
        output_modalities = [Modality(model_type.value)]
    else:
        output_modalities = [Modality.TEXT]
    if is_omni and provider == ModelProvider.DASHSCOPE:
        output_modalities = [Modality.TEXT, Modality.AUDIO]
    return (
        tuple(input_modalities),
        tuple(output_modalities),
        tuple(
            feature
            for feature, capability in _FEATURE_CAPABILITIES.items()
            if capability in values
        ),
    )


def _parse_modalities(
    values: Sequence[Modality | str],
    model_id: UUID,
    label: str,
) -> list[Modality]:
    parsed = []
    for value in values:
        try:
            parsed.append(Modality(value))
        except ValueError:
            logger.warning("unknown %s %r dropped for model %s", label, value, model_id)
    return parsed


def _parse_features(
    values: Sequence[ModelFeature | str],
    model_id: UUID,
) -> list[ModelFeature]:
    parsed = []
    for value in values:
        try:
            parsed.append(ModelFeature(value))
        except ValueError:
            logger.warning("unknown feature %r dropped for model %s", value, model_id)
    return parsed


class ModelProfile(ContractModel):
    """模型能力描述（ModelConfig / ModelBase 快照侧，frozen；契约 v2 单一能力载体）。"""

    model_id: UUID
    tenant_id: UUID | None                     # model_bases 无租户（广场全局目录），base 侧为 None
    type: ModelType
    input_modalities: tuple[Modality, ...]     # 显式非空（text 非必需；存量迁移兜底以 text 起步）
    output_modalities: tuple[Modality, ...]    # 显式非空；生成族 (image,)/(video,) 不含 text
    features: tuple[ModelFeature, ...] = ()
    members: tuple[CompositeMember, ...] = ()  # 仅 provider="composite" 时非空

    @model_validator(mode="after")
    def validate_modalities(self) -> ModelProfile:
        if not self.input_modalities:
            raise ValueError("input_modalities must not be empty")
        if not self.output_modalities:
            raise ValueError("output_modalities must not be empty")
        return self

    @classmethod
    def from_legacy_fields(
        cls,
        *,
        model_id: UUID,
        tenant_id: UUID | None,
        type: ModelType | str,
        provider: ModelProvider | str,
        capabilities: Sequence[ModelCapability | str] = (),
        is_omni: bool = False,
        members: Sequence[CompositeMember] = (),
    ) -> ModelProfile:
        """旧列（`type` / `capability` / `is_omni`）→ profile（spec §13.4 换算口径，2d backfill 同源）。

        未知 capability 值跳过；`is_omni` 仅 provider=dashscope 参与 output 例外（含 audio）。
        deprecated（2a–2d 兼容窗口，2e 随 ModelCapability 删）。
        """
        input_modalities, output_modalities, features = legacy_capability_columns(
            type=type,
            provider=provider,
            capabilities=capabilities,
            is_omni=is_omni,
        )
        return cls(
            model_id=model_id,
            tenant_id=tenant_id,
            type=ModelType(type),
            input_modalities=input_modalities,
            output_modalities=output_modalities,
            features=features,
            members=tuple(members),
        )

    @classmethod
    def from_stored_fields(
        cls,
        *,
        model_id: UUID,
        tenant_id: UUID | None,
        type: ModelType | str,
        provider: ModelProvider | str,
        input_modalities: Sequence[Modality | str] = (),
        output_modalities: Sequence[Modality | str] = (),
        features: Sequence[ModelFeature | str] = (),
        capabilities: Sequence[ModelCapability | str] = (),
        is_omni: bool = False,
        members: Sequence[CompositeMember] = (),
    ) -> ModelProfile:
        """存储行列 → profile 两态（spec §13.4）：

        `input_modalities` 非空 = 新口径行 → 只读三新列（output 为空按 type 定基补齐，
        未知枚举值跳过并 warning）；为空 → 回退旧列换算（回滚窗口旧镜像写入行，旧列为其唯一事实源）。
        """
        model_type = ModelType(type)
        if not input_modalities:
            input_modalities, output_modalities, features = legacy_capability_columns(
                type=model_type,
                provider=provider,
                capabilities=capabilities,
                is_omni=is_omni,
            )
            return cls(
                model_id=model_id,
                tenant_id=tenant_id,
                type=model_type,
                input_modalities=input_modalities,
                output_modalities=output_modalities,
                features=features,
                members=tuple(members),
            )

        parsed_input = _parse_modalities(input_modalities, model_id, "input modality")
        if not parsed_input:
            logger.warning("input modalities all unknown for model %s; defaulting to text", model_id)
            parsed_input = [Modality.TEXT]
        parsed_output = _parse_modalities(output_modalities, model_id, "output modality")
        if not parsed_output:
            if model_type in (ModelType.IMAGE, ModelType.VIDEO):
                parsed_output = [Modality(model_type.value)]
            else:
                parsed_output = [Modality.TEXT]
        return cls(
            model_id=model_id,
            tenant_id=tenant_id,
            type=model_type,
            input_modalities=tuple(parsed_input),
            output_modalities=tuple(parsed_output),
            features=tuple(_parse_features(features, model_id)),
            members=tuple(members),
        )

    def legacy_capability_view(
        self,
        provider: ModelProvider | str,
    ) -> tuple[tuple[ModelCapability, ...], bool]:
        """profile → 旧列视图 `(capability, is_omni)`：宿主遗留壳与包内 v1 key 兼容路径用（deprecated，2e 删）。"""
        capabilities = [
            capability
            for modality, capability in _MODALITY_CAPABILITIES
            if modality in self.input_modalities
        ]
        capabilities.extend(
            _FEATURE_CAPABILITIES[feature] for feature in self.features
        )
        is_omni = (
            provider == ModelProvider.DASHSCOPE
            and Modality.AUDIO in self.output_modalities
        )
        return tuple(capabilities), is_omni


class ModelProvider(StrEnum):
    OPENAI = "openai"
    SPEEDBEAR = "speedbear"
    DASHSCOPE = "dashscope"
    OLLAMA = "ollama"
    XINFERENCE = "xinference"
    GPUSTACK = "gpustack"
    BEDROCK = "bedrock"
    VOLCANO = "volcano"
    COMPOSITE = "composite"


class LoadBalanceStrategy(StrEnum):
    ROUND_ROBIN = "round_robin"
    NONE = "none"


QWEN3_VL_EMBEDDING_DIMENSION = 2048
type SupportedImageMediaType = Literal[
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/bmp",
]


class EmbeddingPurpose(StrEnum):
    INDEX = "index"
    RETRIEVAL = "retrieval"


class TextEmbeddingContent(ContractModel):
    type: Literal["text"] = "text"
    text: str

    @model_validator(mode="after")
    def normalize_text(self) -> TextEmbeddingContent:
        text = self.text.strip()
        if not text:
            raise ValueError("embedding text must not be blank")
        object.__setattr__(self, "text", text)
        return self


class ImageEmbeddingContent(ContractModel):
    type: Literal["image"] = "image"
    media_type: SupportedImageMediaType
    data_uri: str = Field(min_length=1, repr=False)
    decoded_bytes: int = Field(ge=1, repr=False)


type EmbeddingContent = TextEmbeddingContent | ImageEmbeddingContent


class EmbeddingRequest(ContractModel):
    purpose: EmbeddingPurpose
    contents: tuple[EmbeddingContent, ...] = Field(min_length=1, max_length=20)
    dimension: Literal[2048] = QWEN3_VL_EMBEDDING_DIMENSION
    fusion: Literal[True] = True

    @model_validator(mode="after")
    def validate_image_count(self) -> EmbeddingRequest:
        if sum(isinstance(item, ImageEmbeddingContent) for item in self.contents) > 10:
            raise ValueError("embedding request supports at most 10 images")
        return self


class EmbeddingResult(ContractModel):
    vector: tuple[float, ...] = Field(min_length=1, repr=False)
    dimension: Literal[2048] = QWEN3_VL_EMBEDDING_DIMENSION
    usage: dict[str, int] = Field(default_factory=dict)


type RerankQuery = TextEmbeddingContent | ImageEmbeddingContent


class RerankCandidateView(ContractModel):
    chunk_index: int = Field(ge=0)
    kind: Literal["text", "image"]
    content: str = Field(min_length=1, repr=False)
    image_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_image_index(self) -> RerankCandidateView:
        if self.kind == "image" and self.image_index is None:
            object.__setattr__(self, "image_index", 0)
        if self.kind == "text" and self.image_index is not None:
            raise ValueError("text rerank views cannot have an image index")
        return self


class RerankScore(ContractModel):
    input_index: int = Field(ge=0)
    relevance_score: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_finite_score(self) -> RerankScore:
        if not math.isfinite(self.relevance_score):
            raise ValueError("rerank score must be finite")
        return self


class ModelRuntimeOptions(ContractModel):
    timeout_s: float = Field(default=120.0, gt=0)
    max_retries: int = Field(default=2, ge=0)
    concurrency: int = Field(default=5, ge=1)
    http_max_connections: int = Field(default=300, ge=1)
    http_max_keepalive_connections: int = Field(default=50, ge=0)
    http_trust_env: bool = True
    bedrock_max_pool_connections: int = Field(default=50, ge=1)
    bedrock_max_retries: int = Field(default=2, ge=0)
    embedding_batch_size: int = Field(default=10, ge=1)


class ModelConfigSnapshot(ContractModel):
    model_config_id: UUID
    tenant_id: UUID
    provider: ModelProvider
    name: str = Field(min_length=1)  # 解析锚点名：非组合 config 的 name 即真实调用名（组合模型调用名在 members 声明）
    is_active: bool
    is_public: bool
    is_deprecated: bool = False  # 模型下线（model_bases.is_deprecated 派生）：解析期拒止，见 D15⑥
    load_balance_strategy: LoadBalanceStrategy = LoadBalanceStrategy.NONE
    profile: ModelProfile
    config: dict[str, JsonValue] = Field(default_factory=dict)


# deprecated（阶段一过渡）：mem-knowledge 旧读窗口消费；M3 切 model_channels 后由 ChannelSnapshot 取代，收尾任务删除
class ModelKeySnapshot(ContractModel):
    key_id: UUID
    model_name: str = Field(min_length=1)
    provider: ModelProvider
    api_key: SecretStr
    base_url: str | None = None
    is_active: bool
    priority: str = "1"
    usage_count: int = Field(default=0, ge=0)
    last_used_at_ms: int | None = Field(default=None, ge=0)
    capabilities: tuple[ModelCapability, ...] = ()
    is_omni: bool = False
    config: dict[str, JsonValue] = Field(default_factory=dict)


class PublicModelBindingSnapshot(ContractModel):
    tenant_id: UUID
    provider: ModelProvider
    api_key: SecretStr
    base_url: str | None = None


class ResolvedModelConfig(ContractModel):
    model_config_id: UUID
    key_id: UUID | None  # v1 遗留：旧 usage 回写用，M3 渠道化后废弃
    channel_id: UUID | None = None  # v2：命中渠道 id（usage 事件 / 编排追踪）
    tenant_id: UUID
    provider: ModelProvider
    model_name: str = Field(min_length=1)
    api_key: SecretStr
    base_url: str | None = None
    profile: ModelProfile
    deep_thinking: bool = False
    thinking_budget_tokens: int | None = Field(default=None, ge=1)
    json_output: bool = False
    provider_params: dict[str, JsonValue] = Field(default_factory=dict)
    runtime: ModelRuntimeOptions = Field(default_factory=ModelRuntimeOptions)


class ChannelSource:
    """渠道来源（中性措辞，不带企业概念）：租户自管 / 平台代管。"""
    MANUAL = "manual"
    PLATFORM = "platform"


class ChannelSnapshot(ContractModel):
    """渠道登记快照（registry/resolver 用；密文信封原样传递，解密收敛在 resolver 取凭据处）。

    model_names == ()  = provider 级默认渠道，覆盖该 provider 全部未点名模型；
    非空 = 点名渠道，仅匹配列出的模型名。点名态只看 model_names，与 api_base 无关。
    """
    id: UUID
    tenant_id: UUID
    provider: str
    model_names: tuple[str, ...] = ()
    api_base: str | None = None          # 执行端点属性，不参与覆盖匹配
    credential_encrypted: str            # 信封 v{ver}:iv:tag:ct
    credential_sha256: str
    credential_masked: str
    is_active: bool = True
    priority: int = 0
    cooldown_until_ms: int | None = None  # 熔断预留（阶段一恒空）
    source: str = ChannelSource.MANUAL
    extra: dict[str, JsonValue] = Field(default_factory=dict)
    created_at_ms: int
    updated_at_ms: int

    @property
    def is_provider_level(self) -> bool:
        return not self.model_names

    def covers(self, model_name: str) -> bool:
        return self.is_provider_level or model_name in self.model_names
