"""km invoke 通道：包内帧传输 + 归因头 + km 错误语汇（镜像宿主 ``app/integrations/model/invoke``）。

服务侧契约（SSE 帧、JSON 信封、码面）由包内 ``redbear_model.runtime.remote`` 实现，本层只做
km 侧三件事：借独立连接池（每请求自带超时，``trust_env=False`` 不吃本机代理）、把归因翻成帧头
（``ModelCallContext`` 身份 + 用量 contextvar 业务归因 + ``trace_id`` 请求上下文）、把包内
``RemoteInvoke*`` 翻成 km 错误词表（码面透传不重编码，见 :func:`to_invoke_error`）。

与宿主底本的差别：无管理面客户端（km 只消费运行面 invoke），时钟/池参数取
``KnowledgeSettings.model_service_*``。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from error_core.codes import coerce_code
from memorybear_grpc.client import ChannelPool, SyncChannelPool
from redbear_model.errors import RemoteInvokeError
from redbear_model.runtime.grpc_remote import (
    GrpcAsyncInvokeTransport,
    GrpcSyncInvokeTransport,
)
from redbear_model.runtime.remote import (
    AsyncInvokeTransport,
    InvokeFrame,
    InvokeRequest,
    InvokeTarget,
    InvokeTimeouts,
    SyncInvokeTransport,
    envelope_failure,
)

from ...config import KnowledgeSettings
from ...trace import get_trace_id
from ...usage_context import get_usage_resource
from .errors import ModelInvokeProtocolError, ModelInvokeUnavailableError, to_invoke_error

logger = logging.getLogger(__name__)

#: 归因来源常量：服务侧 usage 事件 ``source_service`` 原样落库（服务侧只校验非空）
MODEL_SOURCE_MEM_KNOWLEDGE = "mem-knowledge"
_MODEL_AVAILABILITY_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class ModelCallContext:
    """单次调用的身份/追踪元数据（凭据与选路不在此层，全在服务侧）。"""

    tenant_id: UUID
    source: str = MODEL_SOURCE_MEM_KNOWLEDGE
    actor_id: UUID | None = None
    actor_name: str | None = None
    workspace_id: UUID | None = None
    trace_id: str | None = None


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


def invoke_timeouts(settings: KnowledgeSettings) -> InvokeTimeouts:
    """帧流超时：connect 5s / 块间 idle 180s / 总时长不限；池等待沿用统一口径。"""

    return InvokeTimeouts(
        connect_s=settings.model_service_invoke_connect_timeout_seconds,
        idle_s=settings.model_service_invoke_idle_timeout_seconds,
        write_s=settings.model_service_invoke_write_timeout_seconds,
        pool_s=settings.model_service_pool_timeout_seconds,
    )


def invoke_media_timeouts(settings: KnowledgeSettings) -> InvokeTimeouts:
    """媒体族档超时：connect/write/池同 :func:`invoke_timeouts`，idle 用媒体档。

    媒体调用在服务侧阻塞至任务完成（轮询归服务侧），期间 km 收不到任何帧；idle 取
    ``model_service_invoke_media_idle_timeout_seconds``（见配置注释的超时链口径）。
    """

    return InvokeTimeouts(
        connect_s=settings.model_service_invoke_connect_timeout_seconds,
        idle_s=settings.model_service_invoke_media_idle_timeout_seconds,
        write_s=settings.model_service_invoke_write_timeout_seconds,
        pool_s=settings.model_service_pool_timeout_seconds,
    )


class KBAsyncInvokeTransport(AsyncInvokeTransport):
    """包内异步 transport 的 km 面：只多一层错误翻译（词表留在集成层）。

    必须是**真子类**：包内 chat 适配器按 ``isinstance`` 挑同步/异步面，包装器会在构造调用时
    被拒。翻转的两处错误即 km 语汇，其余帧语义原样透传。
    """

    async def call(self, request: InvokeRequest, target: InvokeTarget) -> Any:
        try:
            return await super().call(request, target)
        except RemoteInvokeError as exc:
            raise to_invoke_error(exc) from exc

    async def stream(
        self, request: InvokeRequest, target: InvokeTarget
    ) -> AsyncIterator[InvokeFrame]:
        try:
            async for frame in super().stream(request, target):
                yield frame
        except RemoteInvokeError as exc:
            raise to_invoke_error(exc) from exc


class KBSyncInvokeTransport(SyncInvokeTransport):
    """包内同步 transport 的 km 面：错误翻译同异步版。

    同样必须是**真子类**：包内 chat 适配器按 ``isinstance`` 挑同步/异步面。消费方：
    同步 LLM 壳（``RedBearChatModel.for_invoke_sync_ref``）与媒体族同步孪生。
    """

    def call(self, request: InvokeRequest, target: InvokeTarget) -> Any:
        try:
            return super().call(request, target)
        except RemoteInvokeError as exc:
            raise to_invoke_error(exc) from exc

    def stream(
        self, request: InvokeRequest, target: InvokeTarget
    ) -> Iterator[InvokeFrame]:
        try:
            yield from super().stream(request, target)
        except RemoteInvokeError as exc:
            raise to_invoke_error(exc) from exc


class KBGrpcAsyncInvokeTransport(GrpcAsyncInvokeTransport):
    """gRPC 异步 transport 的 km 面：错误翻译与 :class:`KBAsyncInvokeTransport` 一致。"""

    async def call(self, request: InvokeRequest, target: InvokeTarget) -> Any:
        try:
            return await super().call(request, target)
        except RemoteInvokeError as exc:
            raise to_invoke_error(exc) from exc

    async def stream(
        self, request: InvokeRequest, target: InvokeTarget
    ) -> AsyncIterator[InvokeFrame]:
        try:
            async for frame in super().stream(request, target):
                yield frame
        except RemoteInvokeError as exc:
            raise to_invoke_error(exc) from exc


class KBGrpcSyncInvokeTransport(GrpcSyncInvokeTransport):
    """gRPC 同步 transport 的 km 面：错误翻译与 :class:`KBSyncInvokeTransport` 一致。"""

    def call(self, request: InvokeRequest, target: InvokeTarget) -> Any:
        try:
            return super().call(request, target)
        except RemoteInvokeError as exc:
            raise to_invoke_error(exc) from exc

    def stream(
        self, request: InvokeRequest, target: InvokeTarget
    ) -> Iterator[InvokeFrame]:
        try:
            yield from super().stream(request, target)
        except RemoteInvokeError as exc:
            raise to_invoke_error(exc) from exc


class ModelInvokeClient:
    """运行面异步调用通道：帧原样透出（``InvokeFrame``），失败翻 km 语汇。

    独立连接池（LLM 档超时见 :func:`invoke_timeouts`）。请求用包内 ``InvokeRequest``
    ——那是双方共同的 wire 契约，km 不另立一套。
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        timeouts: InvokeTimeouts,
        invoke: AsyncInvokeTransport | None = None,
    ):
        self._http = http_client
        self._invoke = invoke or KBAsyncInvokeTransport(
            http_client,
            base_url=str(http_client.base_url).rstrip("/"),
            timeouts=timeouts,
        )

    @property
    def async_transport(self) -> AsyncInvokeTransport:
        """借出带 km 错误词表的包内 transport：chat 壳直接消费它。"""

        return self._invoke

    @classmethod
    def from_settings(
        cls, settings: KnowledgeSettings, *, timeouts: InvokeTimeouts | None = None
    ) -> ModelInvokeClient:
        resolved = timeouts or invoke_timeouts(settings)
        http_client = httpx.AsyncClient(
            base_url=settings.model_service_base_url.rstrip("/") + "/",
            timeout=resolved.httpx_timeout(),
            limits=httpx.Limits(
                max_connections=settings.model_service_max_connections,
                max_keepalive_connections=settings.model_service_max_keepalive_connections,
            ),
            # trust_env=False：内部调用不走本机代理环境（与宿主同口径）
            trust_env=False,
        )
        return cls(http_client, timeouts=resolved)

    @classmethod
    def for_grpc(
        cls,
        settings: KnowledgeSettings,
        pool: ChannelPool,
        *,
        target: str,
        timeouts: InvokeTimeouts | None = None,
        call_budget_s: float = 180.0,
        margin_s: float = 30.0,
    ) -> ModelInvokeClient:
        """gRPC 运行面：invoke 走共享 channel pool，HTTP 客户端只保留 availability 查询。"""

        resolved = timeouts or invoke_timeouts(settings)
        http_client = httpx.AsyncClient(
            base_url=settings.model_service_base_url.rstrip("/") + "/",
            timeout=resolved.httpx_timeout(),
            limits=httpx.Limits(
                max_connections=settings.model_service_max_connections,
                max_keepalive_connections=settings.model_service_max_keepalive_connections,
            ),
            trust_env=False,
        )
        return cls(
            http_client,
            timeouts=resolved,
            invoke=KBGrpcAsyncInvokeTransport(
                pool,
                target_name=target,
                timeouts=resolved,
                call_budget_s=call_budget_s,
                margin_s=margin_s,
            ),
        )

    @classmethod
    def for_test(
        cls,
        base_url: str,
        transport: httpx.AsyncBaseTransport,
        *,
        timeouts: InvokeTimeouts | None = None,
    ) -> ModelInvokeClient:
        resolved = timeouts or InvokeTimeouts()
        http_client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            timeout=resolved.httpx_timeout(),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            transport=transport,
            trust_env=False,
        )
        return cls(http_client, timeouts=resolved)

    async def stream(
        self, request: InvokeRequest, context: ModelCallContext
    ) -> AsyncIterator[InvokeFrame]:
        """流式调用：帧序与终态由包内校验（``done`` 收线、乱序/截断响亮报错）。"""

        async for frame in self._invoke.stream(request, invoke_target(context)):
            yield frame

    async def call(self, request: InvokeRequest, context: ModelCallContext) -> Any:
        """非流式调用：返回 ``data`` 承载的族结果体（失败照旧翻 km 语汇）。"""

        return await self._invoke.call(request, invoke_target(context))

    async def is_model_available(self, model_id: UUID, context: ModelCallContext) -> bool:
        """Read the same derived availability flag used by the model selector."""
        try:
            response = await self._http.get(
                f"internal/v1/models/{model_id}",
                headers=invoke_target(context).headers(),
                timeout=_MODEL_AVAILABILITY_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise ModelInvokeUnavailableError("Model availability lookup failed") from exc
        # This endpoint validates a requested binding, not a model invocation.
        if response.status_code in (400, 404):
            return False
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict) and coerce_code(payload.get("code")):
                raise to_invoke_error(envelope_failure(payload, response.status_code)) from exc
            if response.status_code in (429, 503, 504):
                raise ModelInvokeUnavailableError("Model availability lookup failed") from exc
            raise ModelInvokeProtocolError("Invalid model availability response") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise ModelInvokeProtocolError("Invalid model availability response") from exc
        if isinstance(payload, dict) and coerce_code(payload.get("code")):
            remote = envelope_failure(payload, response.status_code)
            raise to_invoke_error(remote) from remote
        if (
            not isinstance(payload, dict)
            or payload.get("code") != 0
            or not isinstance(payload.get("data"), dict)
        ):
            raise ModelInvokeProtocolError("Invalid model availability response")
        return payload["data"].get("is_available") is True

    async def aclose(self) -> None:
        await self._http.aclose()


class ModelInvokeSyncClient:
    """运行面同步孪生：同步调用点（ES 向量链路 / rerank / sync LLM 壳）走同一 km 语汇。

    包内 ``SyncInvokeTransport`` 已提供同步信封解包；本层只做池借用，错误翻译在
    ``KBSyncInvokeTransport``。媒体族同步孪生传媒体档 timeouts（:func:`invoke_media_timeouts`）。
    """

    def __init__(
        self,
        http_client: httpx.Client | None,
        *,
        timeouts: InvokeTimeouts,
        invoke: SyncInvokeTransport | None = None,
    ):
        self._http = http_client
        if invoke is not None:
            self._invoke = invoke
            return
        if http_client is None:
            raise ValueError("sync model invoke needs an HTTP client or a transport")
        self._invoke = KBSyncInvokeTransport(
            http_client,
            base_url=str(http_client.base_url).rstrip("/"),
            timeouts=timeouts,
        )

    @property
    def sync_transport(self) -> SyncInvokeTransport:
        """借出带 km 错误词表的包内同步 transport：同步 LLM 壳直接消费它。"""

        return self._invoke

    @classmethod
    def from_settings(
        cls, settings: KnowledgeSettings, *, timeouts: InvokeTimeouts | None = None
    ) -> ModelInvokeSyncClient:
        resolved = timeouts or invoke_timeouts(settings)
        http_client = httpx.Client(
            base_url=settings.model_service_base_url.rstrip("/") + "/",
            timeout=resolved.httpx_timeout(),
            limits=httpx.Limits(
                max_connections=settings.model_service_max_connections,
                max_keepalive_connections=settings.model_service_max_keepalive_connections,
            ),
            trust_env=False,
        )
        return cls(http_client, timeouts=resolved)

    @classmethod
    def for_grpc(
        cls,
        pool: SyncChannelPool,
        *,
        target: str,
        timeouts: InvokeTimeouts | None = None,
        call_budget_s: float = 180.0,
        margin_s: float = 30.0,
    ) -> ModelInvokeSyncClient:
        """gRPC 同步面：同步 channel pool 由进程 runtime 持有并统一关闭。"""

        resolved = timeouts or InvokeTimeouts()
        return cls(
            None,
            timeouts=resolved,
            invoke=KBGrpcSyncInvokeTransport(
                pool,
                target_name=target,
                timeouts=resolved,
                call_budget_s=call_budget_s,
                margin_s=margin_s,
            ),
        )

    @classmethod
    def for_test(
        cls,
        base_url: str,
        transport: httpx.BaseTransport,
        *,
        timeouts: InvokeTimeouts | None = None,
    ) -> ModelInvokeSyncClient:
        resolved = timeouts or InvokeTimeouts()
        http_client = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            timeout=resolved.httpx_timeout(),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            transport=transport,
            trust_env=False,
        )
        return cls(http_client, timeouts=resolved)

    def call(self, request: InvokeRequest, context: ModelCallContext) -> Any:
        """非流式调用：返回 ``data`` 承载的族结果体（失败照旧翻 km 语汇）。"""

        return self._invoke.call(request, invoke_target(context))

    def close(self) -> None:
        if self._http is not None:
            self._http.close()


__all__ = [
    "KBAsyncInvokeTransport",
    "KBGrpcAsyncInvokeTransport",
    "KBGrpcSyncInvokeTransport",
    "KBSyncInvokeTransport",
    "MODEL_SOURCE_MEM_KNOWLEDGE",
    "ModelCallContext",
    "ModelInvokeClient",
    "ModelInvokeSyncClient",
    "invoke_media_timeouts",
    "invoke_target",
    "invoke_timeouts",
]
