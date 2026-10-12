"""Chat 壳（远端模式，G2/G3/G4a）：宿主只持有配置引用，凭据、选路与 failover 在模型服务。

构造入口两枚，各自装入一种 transport（异步流式面 / 同步阻塞面，互不混装）：
``for_invoke(info, params=..., streaming=...)`` → 异步（``ainvoke``/``astream``）；
``for_invoke_sync(info, params=...)`` → 同步（``invoke``，纯 sync 站点如 celery ``parse_document``）。
已持有非解密引用（``RemoteInvokeRef``）的站点用 ``for_invoke_ref`` / ``for_invoke_sync_ref``
直接构造，免于再拼 ``ModelInfo``（记忆族 ``ModelClientMixin`` 走此入口）。
消息序列化、wire 参数白名单与结果还原复用包内适配器（``RemoteRedBearChatModel``）；
本层只做宿主侧三件事：取调用通道与归因、把不过线的参数响亮告警、把未装 transport 的
方向显式拒止——静默退化会改变调用形态。

``call_structured`` 是为存量消费方（记忆族各 pipeline）保留的 ``RedBearLLM`` 同义词表：
主路径 ``with_structured_output``，失败降级 ``ainvoke`` + ``StructResponse``。

``streaming`` 是与 ``disable_streaming`` 相对的正向开关（``BaseChatModel`` 无此字段，缺省即
视为未设置）：设真后 graph 节点 ``ainvoke`` 也会走 ``_astream``，回调面才出
``on_chat_model_stream``。工具经 ``bind_tools`` 直落包实现（G3 起不再拦截）。

与 ``RedBearLLM`` 的差别是**不持有 api_key**：宿主不再有 LLM 凭据解密面（设计 §2.2）。
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

from app.core.models.llm import StructResponse
from app.integrations.model.invoke import (
    ModelInvokeClient,
    ModelInvokeSyncClient,
    invoke_target,
)
from app.integrations.model.invoke_backend import (
    RemoteInvokeRef,
    context_from_ref,
    ref_from_model_info,
)
from app.integrations.model.runtime import (
    get_model_invoke_client,
    get_model_invoke_sync_client,
)

if TYPE_CHECKING:
    from app.schemas.model_schema import ModelInfo

logger = logging.getLogger(__name__)

#: 宿主配置里有、但运行面 wire 契约无对应物的参数（服务侧无凭据可带请求头）。出现即告警
#: 并丢弃——用户以为生效而实际不起作用是最坏情况。
_NON_WIRE_PARAMS = ("default_headers",)


def _warn_non_wire_params(params: Mapping[str, Any]) -> None:
    for key in _NON_WIRE_PARAMS:
        if params.get(key) is not None:
            logger.warning(
                "模型参数 %s 不过运行面（服务侧无对应实现），本次调用已忽略该参数", key
            )


class RedBearChatModel(RemoteRedBearChatModel):
    """宿主 Chat 壳：``ainvoke``/``astream`` 走模型服务，凭据不出宿主以外的服务侧。

    包内适配器已按 ``isinstance`` 校验 transport 类型，故这里只能传真子类
    （``ModelInvokeClient.async_transport`` 带宿主错误词表）。
    """

    #: langchain 流式开关（``BaseChatModel`` 无此字段）：进 ``model_fields_set`` 后由
    #: ``_should_stream`` 正向判定，graph 节点 ``ainvoke`` 才走 ``_astream``（``astream_events``
    #: 的 token 事件来源）。``for_invoke`` 恒显式传入：False 即「不流式」，回调链里的流式
    #: handler 不再兜底反选；``astream()`` 不受影响（基类先看 ``stream`` kwarg）。
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
        """远端模式：只带配置 id 与租户（设计 §2.2），凭据与选路在模型服务。"""

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
        """同 ``for_invoke``，直接用非解密引用构造（记忆族 mixin 已持有引用）。"""

        default_params = dict(params or {})
        _warn_non_wire_params(default_params)
        return cls(
            ref.config_id,
            # 通道懒取：构造可能发生在无事件循环的同步现场（记忆族 mixin），首次调用才落池
            transport=lambda: (client or get_model_invoke_client()).async_transport,
            # 归因按调用时刻取：用量 contextvar 与 trace 都是当次请求的事实
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
        """同步远端模式：只给纯 sync 站点（无事件循环，celery ``parse_document`` 等）。

        ``invoke()`` 走阻塞信封；``stream``/异步面不装（未装方向照旧响亮拒止）。
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
        """同 ``for_invoke_sync``，直接用非解密引用构造。"""

        default_params = dict(params or {})
        _warn_non_wire_params(default_params)
        return cls(
            ref.config_id,
            transport=lambda: (client or get_model_invoke_sync_client()).sync_transport,
            target=lambda: invoke_target(context_from_ref(ref)),
            default_params=default_params,
        )

    async def call_structured(self, input: Any, schema: dict[str, Any] | type, **kwargs: Any) -> Any:
        """``RedBearLLM.call_structured`` 的远端等价（存量消费方词表，语义一致）。

        主路径 ``with_structured_output``（langchain 侧默认实现：``bind_tools`` 工具调用 +
        解析）；任何异常或空结果降级 ``ainvoke`` + :class:`StructResponse`（json_repair 兜底，
        兼容不支持工具强约束的供应商）。降级不向上传播首个失败，与旧壳同口径。
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

    # ==================== 未装方向拒止（异步流式/工具走包实现） ====================

    # 这两个覆写同时是 ``_should_stream`` 的「已实现流式」证据：基类在 async 分支要求
    # ``_stream``/``_astream`` 至少一个被实现（「async 兜底 sync」），两个都不覆写会让
    # 异步流式被永久判否。故拒止实现必须保留，不能改成不覆写。

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        # 同步面只在 for_invoke_sync（装 HostSyncInvokeTransport）时委派包实现；
        # 异步壳误调同步入口保持响亮拒止，不静默降级
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
        # 同步流式无消费方（宿主异步流式走 _astream）：两个壳都拒止
        raise self._unsupported("同步流式调用")


__all__ = ["RedBearChatModel"]
