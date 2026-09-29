"""Chat 壳（远端模式，G2/G3）：宿主只持有配置引用，凭据、选路与 failover 在模型服务。

唯一构造入口 ``for_invoke(info, params=..., streaming=...)`` → ``POST /internal/v1/invoke``
（``type=llm``）。消息序列化、wire 参数白名单与结果还原复用包内适配器
（``RemoteRedBearChatModel``）；本层只做三件宿主侧的事：取调用通道与归因、把不过线的参数
响亮告警、把同步面显式拒止（宿主只装异步 transport）——静默退化会改变调用形态。

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
from redbear_model.runtime.remote import RemoteRedBearChatModel

from app.integrations.model.invoke import ModelInvokeClient, invoke_target
from app.integrations.model.invoke_backend import context_from_ref, ref_from_model_info
from app.integrations.model.runtime import get_model_invoke_client

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
        info: "ModelInfo",
        *,
        params: Mapping[str, Any] | None = None,
        streaming: bool = False,
        client: ModelInvokeClient | None = None,
    ) -> "RedBearChatModel":
        """远端模式：只带配置 id 与租户（设计 §2.2），凭据与选路在模型服务。"""

        ref = ref_from_model_info(info)
        invoke_client = client or get_model_invoke_client()
        default_params = dict(params or {})
        _warn_non_wire_params(default_params)
        return cls(
            ref.config_id,
            transport=invoke_client.async_transport,
            # 归因按调用时刻取：用量 contextvar 与 trace 都是当次请求的事实
            target=lambda: invoke_target(context_from_ref(ref)),
            default_params=default_params,
            streaming=streaming,
        )

    def _unsupported(self, feature: str) -> NotImplementedError:
        return NotImplementedError(
            f"{feature}不随 G3 接入（宿主只装异步 transport，同步面留至 G5 清理）"
        )

    # ==================== 同步面拒止（异步流式/工具走包实现） ====================

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
        raise self._unsupported("同步调用")

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        raise self._unsupported("同步流式调用")


__all__ = ["RedBearChatModel"]
