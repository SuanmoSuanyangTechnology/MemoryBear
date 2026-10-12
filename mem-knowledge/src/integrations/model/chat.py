"""Chat 壳（远端模式，G4b）：km 只持有配置引用，凭据、选路与 failover 在模型服务。

构造入口两枚，各自装入一种 transport（异步流式面 / 同步阻塞面，互不混装）：
``for_invoke_ref(ref, pool=...)`` → 异步（``ainvoke``/``astream``）；
``for_invoke_sync_ref(ref, pool=...)`` → 同步（``invoke``，纯 sync 站点如 celery
``parse_document``）。消息序列化、wire 参数白名单与结果还原复用包内适配器
（``RemoteRedBearChatModel``）；本层只做 km 侧三件事：把归因翻成帧头、把不过线的参数
响亮告警、把未装 transport 的方向显式拒止——静默退化会改变调用形态。

``call_structured`` 为存量消费方（graphrag extractor / retrieval_pipeline）保留的
``RedBearLLM`` 同义词表：主路径 ``with_structured_output``，失败降级 ``ainvoke`` +
:class:`StructResponse`（json_repair 兜底，兼容不支持工具强约束的供应商）。

``streaming`` 是与 ``disable_streaming`` 相对的正向开关（``BaseChatModel`` 无此字段，
缺省即视为未设置）：设真后 graph 节点 ``ainvoke`` 也会走 ``_astream``，回调面才出
``on_chat_model_stream``。

与 ``RedBearLLM`` 的差别是**不持有 api_key**：km 不再有 LLM 凭据解密面（设计 §2.2）。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from typing import Any

from json_repair import json_repair
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from redbear_model.runtime.remote import (
    RemoteRedBearChatModel,
    SyncInvokeTransport,
)

from ...rag.chunk.token_utils import num_tokens_from_string
from .invoke import invoke_target
from .invoke_backend import RemoteInvokeRef, context_from_ref
from .runtime import ModelInvokeRuntime

logger = logging.getLogger(__name__)

#: km 配置里有、但运行面 wire 契约无对应物的参数（服务侧无凭据可带请求头）。出现即告警
#: 并丢弃——用户以为生效而实际不起作用是最坏情况。
_NON_WIRE_PARAMS = ("default_headers",)


def _warn_non_wire_params(params: Mapping[str, Any]) -> None:
    for key in _NON_WIRE_PARAMS:
        if params.get(key) is not None:
            logger.warning(
                "模型参数 %s 不过运行面（服务侧无对应实现），本次调用已忽略该参数", key
            )


def message_text(response: Any) -> str:
    """AIMessage/字符串 → 纯文本（与旧 ``rag.models.vision._message_text`` 同口径）。"""

    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            str(block.get("text", "")).strip()
            for block in content
            if isinstance(block, dict) and block.get("text")
        ]
        return "\n".join(parts).strip()
    return str(content or "").strip()


def message_token_count(response: Any, text: str) -> int:
    """消息 token 数：优先 ``usage_metadata.total_tokens``，缺省按文本估算。"""

    usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, Mapping):
        total = usage.get("total_tokens")
        if isinstance(total, int) and not isinstance(total, bool) and total > 0:
            return total
    return num_tokens_from_string(text)


class StructResponse:
    """降级后处理器（镜像宿主 ``app/core/models/llm.py`` 的同名类）。

    抽文本 → ``json_repair`` 修复 → 目标格式；``schema`` 口径与
    ``with_structured_output`` 一致（Pydantic 类 → 实例；dict → dict）。

    Pipe 用法 ``ai_msg | StructResponse(MyModel)``；直接解析
    ``StructResponse.parse(text, schema)``。
    """

    def __init__(self, schema: dict[str, Any] | type):
        self.schema = schema

    def __ror__(self, other: AIMessage | str) -> Any:
        return self._convert(self.extract_text(other))

    @staticmethod
    def parse(text: str, schema: dict[str, Any] | type) -> Any:
        return StructResponse(schema)._convert(text)

    @staticmethod
    def extract_text(other: AIMessage | str) -> str:
        if isinstance(other, str):
            return other
        if isinstance(other, AIMessage):
            content = getattr(other, "content", None)
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts: list[str] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        parts.append(block.get("text") or "")
                    elif block.get("text"):
                        parts.append(block.get("text") or "")
                return "".join(parts)
            return str(content) if content else ""
        raise RuntimeError(f"Unsupported struct type {type(other)}")

    def _convert(self, text: str) -> Any:
        fixed_json = json_repair.repair_json(text, return_objects=True)
        schema = self.schema
        if isinstance(schema, type) and hasattr(schema, "model_validate"):
            return schema.model_validate(fixed_json)
        return fixed_json


class RedBearChatModel(RemoteRedBearChatModel):
    """km Chat 壳：``ainvoke``/``astream`` 走模型服务，凭据不出 km。

    包内适配器已按 ``isinstance`` 校验 transport 类型，故这里只能传真子类
    （``KBAsyncInvokeTransport`` / ``KBSyncInvokeTransport``，带 km 错误词表）。
    """

    #: langchain 流式开关（``BaseChatModel`` 无此字段）：进 ``model_fields_set`` 后由
    #: ``_should_stream`` 正向判定，graph 节点 ``ainvoke`` 才走 ``_astream``（``astream_events``
    #: 的 token 事件来源）。``for_invoke_ref`` 恒显式传入：False 即「不流式」。
    streaming: bool = False

    @classmethod
    def for_invoke_ref(
        cls,
        ref: RemoteInvokeRef,
        *,
        pool: ModelInvokeRuntime,
        params: Mapping[str, Any] | None = None,
        streaming: bool = False,
    ) -> RedBearChatModel:
        """远端异步模式：只带配置 id 与租户（设计 §2.2），凭据与选路在模型服务。"""

        default_params = dict(params or {})
        _warn_non_wire_params(default_params)
        return cls(
            ref.config_id,
            # 通道懒取：构造可能发生在无事件循环的同步现场，首次调用才落池
            transport=lambda: pool.invoke_client.async_transport,
            # 归因按调用时刻取：用量 contextvar 与 trace 都是当次请求的事实
            target=lambda: invoke_target(context_from_ref(ref)),
            default_params=default_params,
            streaming=streaming,
        )

    @classmethod
    def for_invoke_sync_ref(
        cls,
        ref: RemoteInvokeRef,
        *,
        pool: ModelInvokeRuntime,
        params: Mapping[str, Any] | None = None,
    ) -> RedBearChatModel:
        """同步远端模式：只给纯 sync 站点（无事件循环，celery ``parse_document`` 等）。

        ``invoke()`` 走阻塞信封（媒体档站点传媒体池取媒体档超时）；``stream``/异步面
        不装（未装方向照旧响亮拒止）。
        """

        default_params = dict(params or {})
        _warn_non_wire_params(default_params)
        return cls(
            ref.config_id,
            transport=lambda: pool.invoke_sync_client.sync_transport,
            target=lambda: invoke_target(context_from_ref(ref)),
            default_params=default_params,
        )

    async def call_structured(
        self, input: Any, schema: dict[str, Any] | type, **kwargs: Any
    ) -> Any:
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
            f"{feature}不可用：本壳未装该方向的 transport（同步站点请用 for_invoke_sync_ref）"
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
        # 同步面只在 for_invoke_sync_ref（装 KBSyncInvokeTransport）时委派包实现；
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
        # 同步流式无消费方（km 异步流式走 _astream）：两个壳都拒止
        raise self._unsupported("同步流式调用")


__all__ = ["RedBearChatModel", "StructResponse", "message_text", "message_token_count"]
