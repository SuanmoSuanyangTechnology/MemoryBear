"""Chat shell (remote mode): the host only holds a config reference; credentials, routing and failover live in the model service.

Two construction entries, each wiring in one transport (async streaming face / sync blocking face, never mixed):
``for_invoke(info, params=..., streaming=...)`` -> async (``ainvoke``/``astream``);
``for_invoke_sync(info, params=...)`` -> sync (``invoke``, for pure-sync call sites such as celery ``parse_document``).
Sites that already hold a non-decrypted reference (``RemoteInvokeRef``) construct directly via
``for_invoke_ref`` / ``for_invoke_sync_ref``, avoiding a rebuilt ``ModelInfo`` (the memory-family
``ModelClientMixin`` uses that entry).
Message serialization, the wire-param whitelist and result restoration reuse the in-package adapter
(``RemoteRedBearChatModel``); this layer does only three host-side things: fetch the invoke channel
and attribution, loudly warn about params that never cross the wire, and explicitly reject directions
without a transport installed -- silent degradation would change the call shape.

``call_structured`` is the ``RedBearLLM`` synonym table kept for existing consumers (memory-family
pipelines): primary path ``with_structured_output``, failing over to ``ainvoke`` + ``StructResponse``.

``streaming`` is the positive switch opposite ``disable_streaming`` (``BaseChatModel`` lacks the
field; absent means unset): once true, a graph node's ``ainvoke`` also goes through ``_astream`` and
``on_chat_model_stream`` appears on the callback face. Tools land directly in the package
implementation via ``bind_tools``, without interception.

The difference from ``RedBearLLM`` is that it does **not hold an api_key**: the host no longer has
an LLM credential-decryption face.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from redbear_model.runtime.remote import (
    RemoteRedBearChatModel,
    SyncInvokeTransport,
)

from .llm import StructResponse
from ..integrations.model.invoke import (
    ModelInvokeClient,
    ModelInvokeSyncClient,
    invoke_target,
)
from ..integrations.model.invoke_backend import (
    RemoteInvokeRef,
    context_from_ref,
    ref_from_model_info,
)
from ..integrations.model.runtime import (
    get_model_invoke_client,
    get_model_invoke_sync_client,
)

if TYPE_CHECKING:
    from ..schemas.model_schema import ModelInfo

logger = logging.getLogger(__name__)

#: Params present in host config with no counterpart in the runtime wire contract (the
#: service side has no credentials to carry request headers). Warn and drop on sight --
#: the user believing a param takes effect while it silently does nothing is the worst case.
_NON_WIRE_PARAMS = ("default_headers",)


def _warn_non_wire_params(params: Mapping[str, Any]) -> None:
    for key in _NON_WIRE_PARAMS:
        if params.get(key) is not None:
            logger.warning(
                "模型参数 %s 不过运行面（服务侧无对应实现），本次调用已忽略该参数", key
            )


class RedBearChatModel(RemoteRedBearChatModel):
    """Host chat shell: ``ainvoke``/``astream`` go through the model service; credentials never leave the host for the service side.

    The in-package adapter already validates the transport type via ``isinstance``, so only
    true subclasses can be passed here (``ModelInvokeClient.async_transport`` carries the
    host error vocabulary).
    """

    #: langchain streaming switch (``BaseChatModel`` lacks this field): once in
    #: ``model_fields_set`` it is evaluated positively by ``_should_stream``, and a graph
    #: node's ``ainvoke`` goes through ``_astream`` (the source of ``astream_events`` token
    #: events). ``for_invoke`` always passes it explicitly: False means "not streaming" and
    #: streaming handlers in the callback chain no longer inverse-select as a fallback;
    #: ``astream()`` is unaffected (the base class checks the ``stream`` kwarg first).
    streaming: bool = False

    @classmethod
    def for_invoke(
        cls,
        info: ModelInfo,
        *,
        params: Mapping[str, Any] | None = None,
        streaming: bool = False,
        client: ModelInvokeClient | None = None,
    ) -> RedBearChatModel:
        """Remote mode: carries only the config id and tenant; credentials and routing live in the model service."""

        return cls.for_invoke_ref(
            ref_from_model_info(info), params=params, streaming=streaming, client=client
        )

    @classmethod
    def for_invoke_ref(
        cls,
        ref: RemoteInvokeRef,
        *,
        params: Mapping[str, Any] | None = None,
        streaming: bool = False,
        client: ModelInvokeClient | None = None,
    ) -> RedBearChatModel:
        """Like ``for_invoke`` but constructed directly from the non-decrypted reference (the memory-family mixin already holds it)."""

        default_params = dict(params or {})
        _warn_non_wire_params(default_params)
        return cls(
            ref.config_id,
            # Lazy channel acquisition: construction may happen in a sync context without
            # an event loop (memory-family mixin); only the first call populates the pool
            transport=lambda: (client or get_model_invoke_client()).async_transport,
            # Attribution is resolved at call time: the usage contextvar and trace are
            # facts of the current request
            target=lambda: invoke_target(context_from_ref(ref)),
            default_params=default_params,
            streaming=streaming,
        )

    @classmethod
    def for_invoke_sync(
        cls,
        info: ModelInfo,
        *,
        params: Mapping[str, Any] | None = None,
        client: ModelInvokeSyncClient | None = None,
    ) -> RedBearChatModel:
        """Sync remote mode: for pure-sync call sites only (no event loop, e.g. celery ``parse_document``).

        ``invoke()`` uses the blocking envelope; the ``stream``/async faces are not wired in
        (missing directions keep rejecting loudly).
        """

        return cls.for_invoke_sync_ref(ref_from_model_info(info), params=params, client=client)

    @classmethod
    def for_invoke_sync_ref(
        cls,
        ref: RemoteInvokeRef,
        *,
        params: Mapping[str, Any] | None = None,
        client: ModelInvokeSyncClient | None = None,
    ) -> RedBearChatModel:
        """Like ``for_invoke_sync`` but constructed directly from the non-decrypted reference."""

        default_params = dict(params or {})
        _warn_non_wire_params(default_params)
        return cls(
            ref.config_id,
            transport=lambda: (client or get_model_invoke_sync_client()).sync_transport,
            target=lambda: invoke_target(context_from_ref(ref)),
            default_params=default_params,
        )

    async def call_structured(self, input: Any, schema: dict[str, Any] | type, **kwargs: Any) -> Any:
        """Remote equivalent of ``RedBearLLM.call_structured`` (same vocabulary and semantics for existing consumers).

        Primary path ``with_structured_output`` (the langchain default: ``bind_tools`` tool
        call + parsing); any exception or empty result falls back to ``ainvoke`` +
        :class:`StructResponse` (json_repair as the last resort, compatible with providers
        lacking tool constraint support). The fallback does not propagate the first failure,
        matching the legacy shell.
        """

        try:
            chain = self.with_structured_output(schema, **kwargs)
            result = await chain.ainvoke(input)
            if result is not None:
                return result
        except Exception:
            logger.warning(
                "call_structured: with_structured_output 失败，降级 ainvoke + StructResponse",
                exc_info=True,
            )
        response = await self.ainvoke(input)
        return response | StructResponse(schema)

    def _unsupported(self, feature: str) -> NotImplementedError:
        return NotImplementedError(
            f"{feature}不可用：本壳未装该方向的 transport（同步站点请用 for_invoke_sync）"
        )

    # ==================== Rejection of unwired directions (async streaming / tools go through the package) ====================

    # These two overrides double as the "streaming implemented" evidence for
    # ``_should_stream``: in the async branch the base class requires at least one of
    # ``_stream``/``_astream`` to be implemented ("async falls back to sync"), so leaving
    # both unoverridden would permanently disqualify async streaming. The rejecting
    # implementations must stay; they cannot be turned into plain non-overrides.

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        # The sync face delegates to the package implementation only under
        # for_invoke_sync (HostSyncInvokeTransport installed); an async shell wrongly
        # calling the sync entry keeps rejecting loudly, with no silent degradation
        if not isinstance(self._resolve("_transport"), SyncInvokeTransport):
            raise self._unsupported("同步调用")
        return super()._generate(messages, stop, run_manager, **kwargs)

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        # No consumers for sync streaming (host async streaming uses _astream): both shells reject
        raise self._unsupported("同步流式调用")


__all__ = ["RedBearChatModel"]
