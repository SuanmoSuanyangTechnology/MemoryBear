"""运行面 invoke 请求契约（设计 §2.2/§2.3）：按模型族分派的严格 union。

边界口径：**本文件的字段严格，隧道载荷不严格**。
- `config_id` / `type` / `stream` / 各族 `params` 是我们的契约，`extra="forbid"`：
  未知字段一律 422，`api_key` / `base_url` / `provider` / `model_name` 出现即拒收
  （凭据与选路全在服务侧，调用方不得指定来源）。
- `messages`（langchain 序列化）与 `tools`（OpenAI function 形状）是**透传载荷**：
  其内部字段属 langchain 契约，不设白名单，只做最小形状校验。
"""

from __future__ import annotations

import base64
from typing import Annotated, Any, Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 媒体内联上限（设计 §2.3 选项 (c)）：超过即 422，不做截断
MAX_INLINE_MEDIA_BYTES = 1024 * 1024
# base64 编码膨胀：4 字符/3 字节（含 padding），先按编码长度拒绝，避免为超限载荷做无谓解码
_MAX_INLINE_B64_CHARS = ((MAX_INLINE_MEDIA_BYTES + 2) // 3) * 4


class MediaUrlRef(BaseModel):
    """宿主先落对象存储、传 URL（设计 §2.3 选项 (a)）。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["url"] = "url"
    url: str = Field(min_length=1, repr=False)
    mime: str | None = None

    @field_validator("url")
    @classmethod
    def _require_http_url(cls, value: str) -> str:
        # 仅 http(s)：挡住 file:// 与 data: 之类由服务进程代取的伪 scheme
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("media url must be an absolute http(s) url")
        return value


class MediaInlineRef(BaseModel):
    """小文件内联（设计 §2.3 选项 (c)）：仅 base64，≤1MB。"""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["inline"] = "inline"
    data_b64: str = Field(min_length=1, repr=False)
    mime: str = Field(min_length=3, pattern=r"^[\w.+-]+/[\w.+-]+$")

    @field_validator("data_b64")
    @classmethod
    def _bounded_base64(cls, value: str) -> str:
        if len(value) > _MAX_INLINE_B64_CHARS:
            raise ValueError(f"inline media exceeds {MAX_INLINE_MEDIA_BYTES} bytes")
        try:
            decoded = base64.b64decode(value, validate=True)
        except ValueError as exc:
            raise ValueError(f"inline media is not valid base64: {exc}") from exc
        if len(decoded) > MAX_INLINE_MEDIA_BYTES:
            raise ValueError(f"inline media exceeds {MAX_INLINE_MEDIA_BYTES} bytes")
        return value


MediaRef = Annotated[MediaUrlRef | MediaInlineRef, Field(discriminator="kind")]


class LLMParams(BaseModel):
    """LLM 族入参。采样/开关为供应商无关意图；值域与宿主 LLM 节点配置同口径。

    provider 专有旋钮随 G3 按需增列；``default_headers`` 不在此列（请求头属调用方传输
    属性，服务侧无凭据可携带，宿主侧显式告警而非静默丢弃）。
    """

    model_config = ConfigDict(extra="forbid")

    messages: list[dict[str, Any]] = Field(min_length=1)
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    top_k: int | None = Field(default=None, ge=0, le=100)
    max_tokens: int | None = Field(default=None, ge=1)
    stop: list[str] | None = None
    seed: int | None = None
    repetition_penalty: float | None = Field(default=None, ge=0, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    enable_search: bool | None = None
    deep_thinking: bool | None = None
    thinking_budget_tokens: int | None = Field(default=None, ge=1)
    json_output: bool | None = None

    @field_validator("messages")
    @classmethod
    def _require_message_roles(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for message in value:
            if not isinstance(message.get("role"), str):
                raise ValueError("each message must carry a string 'role'")
        return value


class EmbeddingParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: list[str] = Field(min_length=1)
    dimensions: int | None = Field(default=None, ge=1)

    @field_validator("input")
    @classmethod
    def _no_blank_inputs(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("input must not contain blank entries")
        return value


class RerankParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    documents: list[str] = Field(min_length=1)
    top_n: int | None = Field(default=None, ge=1)
    instruct: str | None = None

    @field_validator("query")
    @classmethod
    def _non_blank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value

    @field_validator("documents")
    @classmethod
    def _no_blank_documents(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("documents must not contain blank entries")
        return value


class ASRParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audio: MediaRef
    format: str | None = None
    sample_rate: int | None = Field(default=None, ge=8000)


class ImageParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1)
    negative_prompt: str | None = None
    size: str | None = None
    n: int | None = Field(default=None, ge=1, le=10)
    reference_image: MediaRef | None = None


class VideoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1)
    negative_prompt: str | None = None
    resolution: str | None = None
    n: int | None = Field(default=None, ge=1, le=10)
    reference_image: MediaRef | None = None


class _InvokeEnvelope(BaseModel):
    """请求信封公共字段；`resource` 等归因走内部头，不进 body。"""

    model_config = ConfigDict(extra="forbid")

    config_id: UUID
    stream: bool = True


class LLMInvokeRequest(_InvokeEnvelope):
    type: Literal["llm"]
    params: LLMParams


class EmbeddingInvokeRequest(_InvokeEnvelope):
    type: Literal["embedding"]
    params: EmbeddingParams


class RerankInvokeRequest(_InvokeEnvelope):
    type: Literal["rerank"]
    params: RerankParams


class ASRInvokeRequest(_InvokeEnvelope):
    type: Literal["asr"]
    params: ASRParams


class ImageInvokeRequest(_InvokeEnvelope):
    type: Literal["image"]
    params: ImageParams


class VideoInvokeRequest(_InvokeEnvelope):
    type: Literal["video"]
    params: VideoParams


InvokeRequestBody = Annotated[
    LLMInvokeRequest
    | EmbeddingInvokeRequest
    | RerankInvokeRequest
    | ASRInvokeRequest
    | ImageInvokeRequest
    | VideoInvokeRequest,
    Field(discriminator="type"),
]


__all__ = [
    "MAX_INLINE_MEDIA_BYTES",
    "ASRInvokeRequest",
    "ASRParams",
    "EmbeddingInvokeRequest",
    "EmbeddingParams",
    "ImageInvokeRequest",
    "ImageParams",
    "InvokeRequestBody",
    "LLMInvokeRequest",
    "LLMParams",
    "MediaInlineRef",
    "MediaRef",
    "MediaUrlRef",
    "RerankInvokeRequest",
    "RerankParams",
    "VideoInvokeRequest",
    "VideoParams",
]
