"""宿主 invoke 通道：包内帧传输 + 宿主连接池 / 归因 / 错误语汇。

服务侧契约（SSE 帧、JSON 信封、码面）由包内 ``redbear_model.runtime.remote`` 实现，本层只做
宿主侧三件事：借宿主**独立**连接池（管理面 ``READ=120`` 是整响应语义，帧流必破）、把宿主
归因翻成帧头（``ModelCallContext`` 身份 + 用量 contextvar 业务归因 + 请求上下文 ``trace_id``）、
把包内 ``RemoteInvoke*`` 翻成宿主 ``ModelServiceClientError`` / ``BizCode``（透传不重编码）。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import UUID

import httpx
from redbear_model.errors import (
    RemoteInvokeError,
    RemoteInvokeFailedError,
    RemoteInvokeIdleTimeoutError,
    RemoteInvokeProtocolError,
)
from redbear_model.runtime.remote import (
    AsyncInvokeTransport,
    InvokeFrame,
    InvokeRequest,
    InvokeTarget,
    InvokeTimeouts,
    SyncInvokeTransport,
)

from app.core.config import settings as app_settings
from app.core.trace import get_trace_id
from app.core.usage_context import get_usage_resource

from .client import http_limits
from .contracts import ModelCallContext
from .errors import (
    ModelInvokeFailedError,
    ModelServiceClientError,
    ModelServiceProtocolError,
    ModelServiceTimeoutError,
    ModelServiceUnavailableError,
    biz_code_from_remote,
)
from .transport import ModelServiceHttpTransport, ModelServiceSyncTransport

logger = logging.getLogger(__name__)


def _uuid_or_none(value: str | None) -> UUID | None:
    """业务归因 id 只有 UUID 形态可跨线（服务侧按 UUID 解析头，非 UUID 会拒收整次调用）。"""

    if not value:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        logger.debug("model_invoke_resource_id_not_uuid value=%r", value)
        return None


def invoke_target(context: ModelCallContext) -> InvokeTarget:
    """归因头：身份/来源取调用方上下文，业务归因取用量 contextvar，trace 兜底请求上下文。"""

    resource = get_usage_resource()
    return InvokeTarget(
        tenant_id=context.tenant_id,
        source=context.source,
        actor_id=context.actor_id,
        actor_name=context.actor_name,
        workspace_id=context.workspace_id,
        resource_type=resource.resource_type if resource else None,
        resource_id=_uuid_or_none(resource.resource_id) if resource else None,
        trace_id=context.trace_id or get_trace_id() or None,
    )


def invoke_timeouts(settings: Any) -> InvokeTimeouts:
    """帧流超时：connect 5s / 块间 idle 180s / 总时长不限；池等待沿用管理面口径。"""

    return InvokeTimeouts(
        connect_s=settings.MODEL_SERVICE_INVOKE_CONNECT_TIMEOUT_SECONDS,
        idle_s=settings.MODEL_SERVICE_INVOKE_IDLE_TIMEOUT_SECONDS,
        write_s=settings.MODEL_SERVICE_INVOKE_WRITE_TIMEOUT_SECONDS,
        pool_s=settings.MODEL_SERVICE_POOL_TIMEOUT_SECONDS,
    )


def invoke_media_timeouts(settings: Any) -> InvokeTimeouts:
    """媒体族档超时：connect/write/池同 :func:`invoke_timeouts`，idle 用媒体档。

    媒体调用在服务侧阻塞至任务完成（轮询归服务侧），期间宿主收不到任何帧；idle 取
    ``MODEL_SERVICE_INVOKE_MEDIA_IDLE_TIMEOUT_SECONDS``（见配置注释的超时链口径）。
    """

    return InvokeTimeouts(
        connect_s=settings.MODEL_SERVICE_INVOKE_CONNECT_TIMEOUT_SECONDS,
        idle_s=settings.MODEL_SERVICE_INVOKE_MEDIA_IDLE_TIMEOUT_SECONDS,
        write_s=settings.MODEL_SERVICE_INVOKE_WRITE_TIMEOUT_SECONDS,
        pool_s=settings.MODEL_SERVICE_POOL_TIMEOUT_SECONDS,
    )


def to_host_error(exc: RemoteInvokeError) -> ModelServiceClientError:
    """包内错误 → 宿主错误语汇（唯一翻译点；码面透传不重编码）。"""

    if isinstance(exc, RemoteInvokeIdleTimeoutError):
        error: ModelServiceClientError = ModelServiceTimeoutError(
            f"Model invoke produced no frame within {exc.idle_seconds:g}s"
        )
    elif isinstance(exc, RemoteInvokeProtocolError):
        error = ModelServiceProtocolError(str(exc))
    elif isinstance(exc, RemoteInvokeFailedError):
        error = ModelInvokeFailedError(
            biz_code=biz_code_from_remote(exc.code),
            message=exc.message,
            attempts=exc.attempts,
            channel_id=exc.channel_id,
            retryable=exc.retryable,
            http_status=exc.http_status,
        )
    else:
        error = ModelServiceUnavailableError("Model service is unavailable")
    logger.warning(
        "model_invoke_failed error=%s remote=%s detail=%s",
        type(error).__name__,
        type(exc).__name__,
        exc,
    )
    return error


class HostAsyncInvokeTransport(AsyncInvokeTransport):
    """包内异步 transport 的宿主面：只多一层错误翻译（词表留在集成层）。

    必须是**真子类**：包内 chat 适配器按 ``isinstance`` 挑同步/异步面，包装器会在构造调用时
    被拒。翻转的两处错误即宿主语汇，其余帧语义原样透传。
    """

    async def call(self, request: InvokeRequest, target: InvokeTarget) -> Any:
        try:
            return await super().call(request, target)
        except RemoteInvokeError as exc:
            raise to_host_error(exc) from exc

    async def stream(
        self, request: InvokeRequest, target: InvokeTarget
    ) -> AsyncIterator[InvokeFrame]:
        try:
            async for frame in super().stream(request, target):
                yield frame
        except RemoteInvokeError as exc:
            raise to_host_error(exc) from exc


class HostSyncInvokeTransport(SyncInvokeTransport):
    """包内同步 transport 的宿主面：错误翻译同异步版，词表留在集成层。

    同样必须是**真子类**：包内 chat 适配器按 ``isinstance`` 挑同步/异步面。消费方：
    同步 LLM 壳（``RedBearChatModel.for_invoke_sync``）与媒体族同步孪生。
    """

    def call(self, request: InvokeRequest, target: InvokeTarget) -> Any:
        try:
            return super().call(request, target)
        except RemoteInvokeError as exc:
            raise to_host_error(exc) from exc

    def stream(
        self, request: InvokeRequest, target: InvokeTarget
    ) -> Iterator[InvokeFrame]:
        try:
            yield from super().stream(request, target)
        except RemoteInvokeError as exc:
            raise to_host_error(exc) from exc


class ModelInvokeClient:
    """运行面调用通道：帧原样透出（``InvokeFrame``），失败翻宿主语汇。

    独立连接池：管理面池被管理面请求占满时运行面仍可发（反之亦然）；超时按帧间隔语义，
    见 :func:`invoke_timeouts`。请求用包内 ``InvokeRequest``——那是双方共同的 wire 契约，
    宿主不另立一套。
    """

    def __init__(self, transport: ModelServiceHttpTransport, *, timeouts: InvokeTimeouts):
        self._transport = transport
        self._invoke = HostAsyncInvokeTransport(
            transport.client,
            base_url=str(transport.base_url).rstrip("/"),
            timeouts=timeouts,
        )

    @property
    def async_transport(self) -> HostAsyncInvokeTransport:
        """借出带宿主错误词表的包内 transport：包内 chat 适配器（G2）直接消费它。"""

        return self._invoke

    @classmethod
    def from_settings(
        cls, settings: Any = app_settings, *, timeouts: InvokeTimeouts | None = None
    ) -> ModelInvokeClient:
        resolved = timeouts or invoke_timeouts(settings)
        transport = ModelServiceHttpTransport(
            base_url=settings.MODEL_SERVICE_BASE_URL,
            timeout=resolved.httpx_timeout(),
            limits=http_limits(settings),
        )
        return cls(transport, timeouts=resolved)

    @classmethod
    def for_test(
        cls,
        base_url: str,
        transport: httpx.AsyncBaseTransport,
        *,
        timeouts: InvokeTimeouts | None = None,
    ) -> ModelInvokeClient:
        resolved = timeouts or InvokeTimeouts()
        return cls(
            ModelServiceHttpTransport(
                base_url=base_url,
                timeout=resolved.httpx_timeout(),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                transport=transport,
            ),
            timeouts=resolved,
        )

    async def stream(
        self, request: InvokeRequest, context: ModelCallContext
    ) -> AsyncIterator[InvokeFrame]:
        """流式调用：帧序与终态由包内校验（``done`` 收线、乱序/截断响亮报错）。"""

        async for frame in self._invoke.stream(request, invoke_target(context)):
            yield frame

    async def call(self, request: InvokeRequest, context: ModelCallContext) -> Any:
        """非流式调用：返回 ``data`` 承载的族结果体（失败照旧翻宿主语汇）。"""

        return await self._invoke.call(request, invoke_target(context))

    async def aclose(self) -> None:
        await self._transport.aclose()


class ModelInvokeSyncClient:
    """运行面同步孪生：同步调用点（ES 向量库链路 / RAG rerank / 注解 / sync LLM 壳）走同一宿主语汇。

    包内 ``SyncInvokeTransport`` 已提供同步信封解包；本层只做池借用，错误翻译在
    ``HostSyncInvokeTransport``（与异步版共用 :func:`invoke_target` / :func:`to_host_error`
    / :func:`invoke_timeouts`）。媒体族同步孪生传媒体档 timeouts（:func:`invoke_media_timeouts`）。
    """

    def __init__(self, transport: ModelServiceSyncTransport, *, timeouts: InvokeTimeouts):
        self._transport = transport
        self._invoke = HostSyncInvokeTransport(
            transport.client,
            base_url=str(transport.base_url).rstrip("/"),
            timeouts=timeouts,
        )

    @property
    def sync_transport(self) -> HostSyncInvokeTransport:
        """借出带宿主错误词表的包内同步 transport：同步 LLM 壳直接消费它。"""

        return self._invoke

    @classmethod
    def from_settings(
        cls, settings: Any = app_settings, *, timeouts: InvokeTimeouts | None = None
    ) -> ModelInvokeSyncClient:
        resolved = timeouts or invoke_timeouts(settings)
        transport = ModelServiceSyncTransport(
            base_url=settings.MODEL_SERVICE_BASE_URL,
            timeout=resolved.httpx_timeout(),
            limits=http_limits(settings),
        )
        return cls(transport, timeouts=resolved)

    @classmethod
    def for_test(
        cls,
        base_url: str,
        transport: httpx.BaseTransport,
        *,
        timeouts: InvokeTimeouts | None = None,
    ) -> ModelInvokeSyncClient:
        resolved = timeouts or InvokeTimeouts()
        return cls(
            ModelServiceSyncTransport(
                base_url=base_url,
                timeout=resolved.httpx_timeout(),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                transport=transport,
            ),
            timeouts=resolved,
        )

    def call(self, request: InvokeRequest, context: ModelCallContext) -> Any:
        """非流式调用：返回 ``data`` 承载的族结果体（失败照旧翻宿主语汇）。"""

        return self._invoke.call(request, invoke_target(context))

    def close(self) -> None:
        self._transport.close()
