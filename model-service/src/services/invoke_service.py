"""运行面 invoke 门面（M8 §2.2–§2.6）：解析 → 租户交叉校验 → 选路/解密/调用 → 归因。

调用序（一次 invoke 的完整代价）：
1. 可见性预载 ``get_by_id_async(tenant_id=...)``（本租户 ∪ is_public）：不可见与不存在同归
   404，不泄露存在性（§2.8）。租户边界在服务侧强制，不依赖调用方自律。
2. 解析 + 换渠道计划：``resolve_config_plan_async(config_row=...)`` 复用第 1 步的 ORM 行，
   查询次数与单次解析一致；组合 config 走 ``resolve_composite_plan_async`` 成员编排。
3. 编排 ``run_candidate_fallback_async``（§2.5 责任边界）：同候选瞬时重试 ≤2、仅换渠道前
   换候选、terminal/不可分类原样透传、空链/耗尽抛包内错误。
4. 归因 ``UsageEvent``：config_id 恒为入口 config（组合链成员同样归到入口）；
   provider/model_name/channel_id 取实际尝试/命中候选（成功与失败同口径）；终态经
   ``usage_publisher`` 旁路发射进 ``model:usage``（§2.6：发射失败仅计数告警，不影响调用）。

凭据边界：明文只经 ``SecretStr`` 在包内解密点与 runtime 构造器之间流转；本模块不入日志、
不进返回值、不进 usage（失败 usage 的 error_type 只取异常类名，不取 message）。

失败一律 ``InvokeFailure``（BizCode + attempts + channel_id + 失败 usage）：可见性/入参类
失败未发起调用（usage_event=None，不发 usage 帧）；已发起调用的失败带 usage_event
（attempts 至少计 1 次请求，供告警面统计）。

llm 族（G2 非流式 / G3 流式 + 工具）：逐请求参数并进 ``provider_params`` 后重跑能力仲裁，
与配置期解析同源；``tools`` / ``tool_choice`` 走 ``bind_tools``（不进 provider_params——
provider 构造器对未知字段静默忽略，绑错通道即工具静默失效）；token 归因取自返回消息的
``usage_metadata``（流式取最后一个带 usage 的块；缺则记 0，不臆造）。

流式（G3）：``chunk_sink`` 非空 = 增量下发，runtime 的块经 ``_drain_stream`` 归一后推入
有界队列（``await put`` 背压）；首块由候选循环内 eager 拉取（``open_astream``），故首块前
失败照常同候选重试 / 换渠道，首块产出后交棒（§2.5：不再换渠道）。首帧前总档由
``invoke()`` 的 ``first_frame_timeout_s`` 施加（不含首帧后的排流），块间空闲档由
``idle_timeout_s`` 逐块计时。超时 → 504（SERVICE_UNAVAILABLE）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessageChunk, BaseMessage
from redbear_model import (
    ChannelSwitchExhaustedError,
    CredentialDecryptError,
    ModelConfigDeprecatedError,
    ModelConfigInactiveError,
    ModelProvider,
    ModelType,
    NoAvailableChannelError,
    ResolvedModelConfig,
    SpeedbearChannelMissingError,
    UsageEvent,
    UsageStatus,
    open_astream,
    publish_usage_safely,
    run_candidate_fallback_async,
)
from redbear_model.runtime import (
    RedBearEmbeddings,
    RedBearLLM,
    RedBearRerank,
    messages_from_wire,
    normalize_runtime_flags,
)
from redbear_model.runtime.client_pool import ModelClientPool
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import BizCode, http_status_for
from ..repositories.model_repository import ModelConfigRepository
from ..schemas.invoke_schema import (
    EmbeddingInvokeRequest,
    EmbeddingParams,
    InvokeRequestBody,
    LLMInvokeRequest,
    LLMParams,
    RerankInvokeRequest,
    RerankParams,
)
from ..sensitive import SensitiveDataFilter
from .channel_registry import (
    SOURCE,
    resolve_composite_plan_async,
    resolve_config_plan_async,
)
from .channel_service import cipher_from_env
from .usage_publisher import usage_publisher

logger = logging.getLogger(__name__)

# §2.8：不可见/不存在一律 404（本门面给定 HTTP 语义，端点不二次解释）
_NOT_FOUND_STATUS = 404


class InvokeFailure(Exception):
    """invoke 终态失败：码面 + 归因，供端点渲染信封或 SSE ``error`` 帧。

    ``retryable`` 按宿主既有口径派生（HTTP ≥ 500），与信封渲染同源。
    """

    def __init__(
        self,
        *,
        code: BizCode,
        message: str,
        http_status: int | None = None,
        attempts: int = 0,
        channel_id: uuid.UUID | None = None,
        usage_event: UsageEvent | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status_for(code) if http_status is None else http_status
        self.retryable = self.http_status >= 500
        self.attempts = attempts
        self.channel_id = channel_id
        self.usage_event = usage_event


@dataclass(frozen=True)
class InvokeAttribution:
    """请求头归因（设计 §2.6：头通道而非 body 字段）。"""

    source_service: str
    request_id: str | None = None
    resource_type: str | None = None
    resource_id: uuid.UUID | None = None


@dataclass(frozen=True)
class InvokeOutcome:
    data: dict[str, Any]
    usage_event: UsageEvent
    status: UsageStatus


# ---------------- 分族调用 ----------------


async def _invoke_embedding(
    resolved: ResolvedModelConfig,
    params: EmbeddingParams,
    *,
    client_pool: ModelClientPool | None = None,
) -> dict[str, Any]:
    """embedding 族：统一走包内 RedBearEmbeddings（火山走 embed_batch 多模态批入口）。

    ``client_pool`` 由调用方注入进程级池（连接复用 + 并发闸门）；缺省自建（aclose 按
    所有权判定，借用池不会被本实例关闭）。rerank 族包内 runtime 不支持池注入。
    """
    runtime = RedBearEmbeddings(resolved, client_pool=client_pool)
    try:
        embed: Callable[[list[str]], list[list[float]]] = (
            runtime.embed_batch
            if resolved.provider is ModelProvider.VOLCANO
            else runtime.embed_documents
        )
        vectors = await asyncio.to_thread(embed, list(params.input))
    finally:
        await runtime.aclose()
    return {
        "vectors": [list(vector) for vector in vectors],
        "usage": {
            "count": len(vectors),
            "dimension": len(vectors[0]) if vectors else 0,
        },
    }


async def _invoke_rerank(resolved: ResolvedModelConfig, params: RerankParams) -> dict[str, Any]:
    """rerank 族：包内 RedBearRerank（provider 适配与 qwen3-vl 分支在包内）。"""
    runtime = RedBearRerank(resolved)
    try:
        results = await asyncio.to_thread(
            runtime.rerank,
            list(params.documents),
            params.query,
            top_n=-1 if params.top_n is None else params.top_n,
        )
    finally:
        await runtime.aclose()
    return {
        "results": [
            {"index": item["index"], "relevance_score": item["relevance_score"]}
            for item in results
        ]
    }


#: 逐请求参数并进 ``provider_params`` 的键（与宿主 LLM 节点 extra_params 同口径）：
#: 采样/开关类由 provider 层分桶（dashscope 进 extra_body，其余进构造器）；tools/tool_choice
#: 不在此列（经 ``bind_tools`` 绑定，落进 provider_params 会被构造器静默吞掉），
#: default_headers 也不在（请求头不过线）。
_LLM_MERGE_KEYS = (
    "response_format",
    "temperature",
    "top_p",
    "top_k",
    "max_tokens",
    "stop",
    "seed",
    "repetition_penalty",
    "frequency_penalty",
    "presence_penalty",
    "enable_search",
    "deep_thinking",
    "thinking_budget_tokens",
    "json_output",
)


def _with_llm_params(
    resolved: ResolvedModelConfig,
    params: LLMParams,
    *,
    streaming: bool = False,
) -> ResolvedModelConfig:
    """逐请求参数并进 ``provider_params``，并按能力事实重跑仲裁。

    与配置期解析（resolver ``_runtime_flags`` + ``normalize_runtime_flags``）同源：能力
    不允许的开关被降级并告警，而不是把矛盾参数交给 provider。

    ``streaming=True``（流式请求）把 ``streaming`` 提示并进 provider_params：openai 兼容族
    借它开 ``stream_usage``（末帧带 usage，token 归因不丢），三族 provider 都把它当
    config-only（不进构造器）。
    """
    merged = dict(resolved.provider_params)
    for key in _LLM_MERGE_KEYS:
        value = getattr(params, key)
        if value is not None:
            merged[key] = value
    if streaming:
        merged["streaming"] = True
    raw_budget = merged.get("thinking_budget_tokens")
    deep_thinking, thinking_budget, json_output = normalize_runtime_flags(
        resolved.profile.features,
        bool(merged.get("deep_thinking", False)),
        int(raw_budget) if raw_budget is not None else None,
        bool(merged.get("json_output", False)),
        resolved.model_name,
    )
    return resolved.model_copy(
        update={
            "provider_params": merged,
            "deep_thinking": deep_thinking,
            "thinking_budget_tokens": thinking_budget,
            "json_output": json_output,
        }
    )


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _token_counts(message: Any) -> dict[str, int] | None:
    """``usage_metadata`` 优先，兜底 ``response_metadata.token_usage``（OpenAI 原生字段）。

    两者皆缺（供应商不回 usage）→ ``None``：宿主按「无 token 事实」处理，不臆造 0。
    """
    usage = getattr(message, "usage_metadata", None)
    if isinstance(usage, Mapping) and (
        usage.get("input_tokens") is not None or usage.get("output_tokens") is not None
    ):
        input_tokens = _as_int(usage.get("input_tokens"))
        output_tokens = _as_int(usage.get("output_tokens"))
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": _as_int(usage.get("total_tokens")) or (input_tokens + output_tokens),
        }
    metadata = getattr(message, "response_metadata", None)
    token_usage = (
        metadata.get("token_usage") if isinstance(metadata, Mapping) else None
    )
    if isinstance(token_usage, Mapping):
        input_tokens = _as_int(token_usage.get("prompt_tokens"))
        output_tokens = _as_int(token_usage.get("completion_tokens"))
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": _as_int(token_usage.get("total_tokens"))
            or (input_tokens + output_tokens),
        }
    return None


def _result_tokens(data: Mapping[str, Any]) -> tuple[int, int]:
    """结果体里的 token 归因（``usage_metadata`` 缺失 → (0, 0)，仅非 llm 族会发生）。"""
    usage = data.get("usage_metadata")
    if not isinstance(usage, Mapping):
        return 0, 0
    return _as_int(usage.get("input_tokens")), _as_int(usage.get("output_tokens"))


def _tool_calls_dump(message: Any) -> list[dict[str, Any]]:
    """``AIMessage.tool_calls`` → wire 形状（与 ``_serialize_message`` 同口径）。

    工具绑定后 provider 在消息里回 tool_calls；流式下的增量片段由宿主按 chunk 自行拼装。
    """
    calls = getattr(message, "tool_calls", None) or []
    return [
        {"name": call.get("name"), "args": call.get("args") or {}, "id": call.get("id")}
        for call in calls
        if isinstance(call, Mapping)
    ]


class InvokeTimeout(TimeoutError):
    """门面内部超时身份：与供应商超时（同为 TimeoutError）区分，精确映射 504。"""


def _bound_runtime(runtime: Any, params: LLMParams) -> Any:
    """``tools`` / ``tool_choice`` 经 ``bind_tools`` 施加（不进 provider_params）。

    落进 provider_params 会被 provider 构造器静默吞掉（ChatOpenAI ``extra='ignore'``），
    工具静默失效比报错更坏；底层 runtime 无 ``bind_tools``（如 OllamaLLM）时响亮拒止。
    """
    if not params.tools:
        return runtime
    bind = getattr(runtime, "bind_tools", None)
    if not callable(bind):
        raise InvokeFailure(
            code=BizCode.INVALID_PARAMETER,
            message="当前模型运行时不支持工具调用（底层 runtime 无 bind_tools）",
        )
    return bind(params.tools, tool_choice=params.tool_choice)


def _chunk_payload(chunk: Any) -> dict[str, Any]:
    """流式块 → wire 形状（``AIMessageChunk.model_dump(mode="json")``）。

    ChatOpenAI/ChatBedrock 的 ``astream`` 出 ``AIMessageChunk``；``BaseLLM``（OllamaLLM）
    出 ``GenerationChunk``（``.message`` 即 AIMessageChunk）。两者之外属实现变更，
    响亮报错好过静默丢块。
    """
    if isinstance(chunk, AIMessageChunk):
        return chunk.model_dump(mode="json")
    message = getattr(chunk, "message", None)
    if isinstance(message, AIMessageChunk):
        return message.model_dump(mode="json")
    text = getattr(chunk, "text", None)
    if isinstance(text, str):
        return AIMessageChunk(content=text).model_dump(mode="json")
    raise TypeError(f"unsupported stream chunk type: {type(chunk).__name__}")


async def _drain_stream(
    stream: AsyncIterator[Any],
    sink: asyncio.Queue[dict[str, Any]],
    *,
    idle_timeout_s: float | None,
) -> dict[str, Any]:
    """逐块归一 → ``sink.put``（有界队列背压）；返回 ``{"usage_metadata": counts}``。

    计数取最后一个带 usage 的块（openai 兼容族 ``stream_usage=True`` 末帧带 usage）；
    供应商不回 usage → counts 为 None，``_result_tokens`` 记 0，不臆造。

    ``idle_timeout_s`` 按块间计时（每块重新起算）：供应商长时间无增量 → ``InvokeTimeout``
    （宿主拿到结构化 error 帧，好过裸 idle 断连）。超时/取消路径在 ``finally`` 里
    ``aclose()`` 归还上游连接（取消态下挂起即抛，由 GC 兜底）。
    """
    counts: dict[str, int] | None = None
    try:
        while True:
            try:
                if idle_timeout_s is None:
                    chunk = await stream.__anext__()
                else:
                    chunk = await asyncio.wait_for(
                        stream.__anext__(), timeout=idle_timeout_s
                    )
            except StopAsyncIteration:
                break
            except TimeoutError as exc:
                raise InvokeTimeout(
                    f"流式响应空闲超时（>{idle_timeout_s:g}s 无增量）"
                ) from exc
            message = (
                chunk
                if isinstance(chunk, AIMessageChunk)
                else getattr(chunk, "message", chunk)
            )
            chunk_counts = _token_counts(message)
            if chunk_counts is not None:
                counts = chunk_counts
            await sink.put(_chunk_payload(chunk))
    finally:
        with contextlib.suppress(Exception):
            await stream.aclose()
    return {"usage_metadata": counts}


async def _invoke_llm(
    resolved: ResolvedModelConfig,
    params: LLMParams,
    messages: list[BaseMessage],
    *,
    client_pool: ModelClientPool | None = None,
) -> dict[str, Any]:
    """llm 族（非流式）：包内 RedBearLLM（``ainvoke`` 原生 async，首块档超时可真正取消上游）。

    返回体是 ``AIMessage`` 的可校验 dump：调用方（包内 ``RemoteRedBearChatModel``）按
    ``_result_message`` 还原；``usage_metadata`` 同时供本门面归因 token。
    """
    runtime = RedBearLLM(_with_llm_params(resolved, params), client_pool=client_pool)
    message = await _bound_runtime(runtime, params).ainvoke(messages)
    return {
        "content": message.content,
        "tool_calls": _tool_calls_dump(message),
        "additional_kwargs": dict(getattr(message, "additional_kwargs", None) or {}),
        "response_metadata": dict(getattr(message, "response_metadata", None) or {}),
        "usage_metadata": _token_counts(message),
    }


async def _invoke_llm_stream(
    resolved: ResolvedModelConfig,
    params: LLMParams,
    messages: list[BaseMessage],
    *,
    client_pool: ModelClientPool | None = None,
) -> AsyncIterator[Any]:
    """llm 族（流式）：``open_astream`` 在候选循环内 eager 拉首块（首块前失败可换渠道）。

    返回惰性异步迭代器（首块已拉出并被链回）；排流由调用方经 ``_drain_stream`` 完成。
    ``streaming`` 提示并入 provider_params：末帧带 usage，token 归因不丢。
    """
    runtime = RedBearLLM(
        _with_llm_params(resolved, params, streaming=True), client_pool=client_pool
    )
    bound = _bound_runtime(runtime, params)
    return await open_astream(lambda: bound.astream(messages))


def _family_invoker(
    request: InvokeRequestBody,
    *,
    client_pool: ModelClientPool | None = None,
) -> Callable[[ResolvedModelConfig], Awaitable[Any]]:
    """按族取调用闭包；族未开放 / 入参不可支持一律终态拒止（不做静默忽略）。

    灰度现开：llm（G2 非流式 / G3 流式 + 工具）、embedding / rerank（G1）；
    多模态族待 G4 逐族接入本表。llm 流式的返回物是异步迭代器（``invoke`` 里再排流）。
    """
    if isinstance(request, LLMInvokeRequest):
        try:
            messages = messages_from_wire(request.params.messages)
        except ValueError as exc:
            # 载荷还原失败是调用方契约违规（零尝试、不占候选、不发 usage）
            raise InvokeFailure(
                code=BizCode.INVALID_PARAMETER,
                message=f"messages 载荷非法: {SensitiveDataFilter.filter_string(str(exc))}",
            ) from exc
        params = request.params
        if request.stream:
            return lambda resolved: _invoke_llm_stream(
                resolved, params, messages, client_pool=client_pool
            )
        return lambda resolved: _invoke_llm(
            resolved, params, messages, client_pool=client_pool
        )
    if isinstance(request, EmbeddingInvokeRequest):
        if request.params.dimensions is not None:
            raise _unsupported_param("embedding dimensions")
        params = request.params
        return lambda resolved: _invoke_embedding(resolved, params, client_pool=client_pool)
    if isinstance(request, RerankInvokeRequest):
        if request.params.instruct is not None:
            raise _unsupported_param("rerank instruct")
        params = request.params
        return lambda resolved: _invoke_rerank(resolved, params)
    raise InvokeFailure(
        code=BizCode.INVALID_PARAMETER,
        message=(
            f"invoke 暂未开放 type={request.type} 族"
            "（当前灰度：llm / embedding / rerank）"
        ),
    )


def _unsupported_param(name: str) -> InvokeFailure:
    return InvokeFailure(
        code=BizCode.INVALID_PARAMETER,
        message=f"{name} 暂不支持：该参数无供应商无关的实现，静默忽略会返回错误形态结果",
    )


# ---------------- 失败映射 ----------------


def _not_found(config_id: uuid.UUID) -> InvokeFailure:
    return InvokeFailure(
        code=BizCode.MODEL_NOT_FOUND,
        message=f"模型配置不存在或无权访问: {config_id}",
        http_status=_NOT_FOUND_STATUS,
    )


_FAILURE_CODES: tuple[tuple[type[BaseException], BizCode], ...] = (
    (ModelConfigDeprecatedError, BizCode.MODEL_DEPRECATED),
    (ModelConfigInactiveError, BizCode.CHANNEL_DISABLED),
    (SpeedbearChannelMissingError, BizCode.SPEEDBEAR_CHANNEL_MISSING),
    (ChannelSwitchExhaustedError, BizCode.NO_AVAILABLE_CHANNEL),
    (NoAvailableChannelError, BizCode.NO_AVAILABLE_CHANNEL),
    (CredentialDecryptError, BizCode.CREDENTIAL_DECRYPT_ERROR),
)


_STATUS_IN_TEXT = re.compile(r"status_code:\s*(\d{3})\b")


def _provider_status(exc: BaseException) -> int | None:
    """异常链上的供应商 HTTP 状态：对象属性优先（与包内谓词同源），文案兜底。

    dashscope 原生链路（langchain 的 DashScopeEmbeddings / DashScopeRerank）对 400/401
    抛裸 ValueError/RuntimeError，状态码只在 ``"status_code: 401 \\n code: ..."`` 文案里，
    无属性可取；宿主旧口径同样按文本标记识别认证失败（见 app/services/model_service.py
    的 _AUTH_ERROR_MARKERS）。429/5xx 走 HTTPError(response=...) 携带属性，不受此影响。
    """
    current: BaseException | None = exc
    for _ in range(8):
        if current is None:
            break
        status = getattr(current, "status_code", None)
        if status is None:
            response = getattr(current, "response", None)
            status = response.get("status_code") if isinstance(response, dict) else getattr(
                response, "status_code", None
            )
        if status is not None:
            try:
                return int(status)
            except (TypeError, ValueError):
                return None
        current = current.__cause__ or current.__context__
    match = _STATUS_IN_TEXT.search(str(exc))
    return int(match.group(1)) if match else None


def _failure_code(exc: BaseException) -> BizCode:
    for error_type, code in _FAILURE_CODES:
        if isinstance(exc, error_type):
            return code
    status = _provider_status(exc)
    if status == 429:
        return BizCode.RATE_LIMITED
    if status in (401, 403):
        return BizCode.API_KEY_INVALID
    if status is not None and 400 <= status < 500:
        return BizCode.INVALID_PARAMETER
    return BizCode.INTERNAL_ERROR


def _last_channel_id(exc: BaseException) -> uuid.UUID | None:
    """聚合报错的最后一个候选渠道（§2.4 error 帧的 channel_id）。"""
    failures = getattr(exc, "failures", None)
    if not failures:
        return None
    return failures[-1][0]


def _failure_from_exception(exc: BaseException, *, attempts: int) -> InvokeFailure:
    """包内错误 → 门面失败：码面透传不再发明语义，文案过敏感过滤（供应商可能回显凭据）。"""
    return InvokeFailure(
        code=_failure_code(exc),
        message=SensitiveDataFilter.filter_string(str(exc)),
        attempts=attempts,
        channel_id=_last_channel_id(exc),
    )


# ---------------- usage 归因 ----------------


def _usage_event(
    *,
    request: InvokeRequestBody,
    attribution: InvokeAttribution,
    tenant_id: uuid.UUID,
    config_id: uuid.UUID,
    channel_id: uuid.UUID | None,
    provider: ModelProvider,
    model_name: str,
    attempts: int,
    latency_ms: int,
    status: UsageStatus,
    error_type: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> UsageEvent:
    return UsageEvent(
        source_service=attribution.source_service,
        tenant_id=tenant_id,
        config_id=config_id,
        channel_id=channel_id,
        provider=provider,
        model_name=model_name,
        capability=ModelType(request.type),
        stream=request.stream,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        status=status,
        error_type=error_type,
        attempts=max(1, attempts),
        request_id=attribution.request_id,
        resource_type=attribution.resource_type,
        resource_id=attribution.resource_id,
    )


async def _report_usage(event: UsageEvent) -> None:
    """旁路发射（§2.6）：XADD 走线程池（事件循环内直调阻塞，同 channel_registry GC#11），
    失败由 ``publish_usage_safely`` 兜底（计数 + 告警，绝不抛回业务）。"""

    await asyncio.to_thread(publish_usage_safely, usage_publisher, event)


# ---------------- 门面 ----------------


async def invoke(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    request: InvokeRequestBody,
    attribution: InvokeAttribution,
    first_result_timeout_s: float | None = None,
    first_frame_timeout_s: float | None = None,
    idle_timeout_s: float | None = None,
    chunk_sink: asyncio.Queue[dict[str, Any]] | None = None,
    client_pool: ModelClientPool | None = None,
) -> InvokeOutcome:
    """单次 invoke：解析 → 选路/解密/调用 → 归因（终态旁路发射 usage）。失败抛 ``InvokeFailure``。

    终态（成功 / 已发起调用的失败）经 ``model:usage`` 旁路发射；零尝试失败无调用事实，不发射
    （§2.6：发射失败仅计数告警，不影响调用返回）。

    ``client_pool`` = 进程级模型客户端池（端点注入 ``runtime.model_runtime.pool``）：
    缺省时各 runtime 自建 httpx 客户端，逐请求握手且绕过进程并发闸门。

    ``first_result_timeout_s`` = 首块档超时（§2.9 服务侧第一档），**按候选生效**：
    超时归瞬时错误 → 同候选重试 → 仍失败则换渠道。非流式族（G1 embedding/rerank、G2 llm）
    的「首块」即整段结果，故该档等于单次调用上界——llm 档位较宽（见 config 的 llm 例外），
    慢响应超时后没有换渠道余量。流式族的「首块」= 连接 + 首个 chunk（``open_astream`` 在
    候选循环内 eager 拉出），换渠道/重试语义与非流式一致。

    ``first_frame_timeout_s`` = 首帧前总档（含选路/解密/换渠道），只包住「解析 + 首块」，
    **不包排流**（否则长回复被总档截断）；超时 → ``InvokeTimeout`` → 504。

    ``chunk_sink`` 非空 = llm 流式：增量经 ``_drain_stream`` 归一后推入有界队列（``await put``
    背压，宿主实时收帧），成功返回体是 ``{"usage_metadata": counts}``；``idle_timeout_s``
    是块间空闲档（每块重新起算）。
    """
    if not attribution.source_service.strip():
        raise InvokeFailure(
            code=BizCode.INVALID_PARAMETER,
            message="缺少来源标识 X-Model-Source（usage 归因必填）",
        )
    run_family = _family_invoker(request, client_pool=client_pool)

    row = await ModelConfigRepository.get_by_id_async(
        db, request.config_id, tenant_id=tenant_id
    )
    if row is None:
        raise _not_found(request.config_id)
    entry = SOURCE.config_snapshot(row)

    started = time.perf_counter()
    attempts = 0
    last_resolved: ResolvedModelConfig | None = None

    async def _call(resolved: ResolvedModelConfig) -> Any:
        nonlocal attempts, last_resolved
        attempts += 1
        last_resolved = resolved
        if first_result_timeout_s is None:
            return await run_family(resolved)
        # wait_for 取消失败的 await：llm 的原生 async 调用真取消上游，embedding/rerank 的
        # to_thread 只能弃等（请求线程跑完自灭）；超时文案带预算供排障
        try:
            return await asyncio.wait_for(
                run_family(resolved), timeout=first_result_timeout_s
            )
        except TimeoutError as exc:
            raise TimeoutError(
                f"first result timeout after {first_result_timeout_s:g}s"
            ) from exc

    async def _resolve_and_call() -> Any:
        if entry.provider is ModelProvider.COMPOSITE:
            resolved_with_plan = await resolve_composite_plan_async(
                db, row, tenant_id=tenant_id
            )
        else:
            resolved_with_plan = await resolve_config_plan_async(
                db, request.config_id, tenant_id, config_row=row
            )
        if resolved_with_plan is None:
            raise _not_found(request.config_id)
        plan = resolved_with_plan.plan
        return await run_candidate_fallback_async(
            plan.candidates,
            entry=plan.entry,
            tenant_id=tenant_id,
            cipher=cipher_from_env(),
            invoke=_call,
        )

    try:
        if first_frame_timeout_s is None:
            outcome = await _resolve_and_call()
        else:
            # 首帧前总档只包住「解析 + 首块」：排流在档外，长回复不被截断
            try:
                outcome = await asyncio.wait_for(
                    _resolve_and_call(), timeout=first_frame_timeout_s
                )
            except TimeoutError as exc:
                raise InvokeTimeout(
                    f"首帧超时（>{first_frame_timeout_s:g}s 未产出）"
                ) from exc
        if chunk_sink is None:
            result_data: dict[str, Any] = outcome.result
        else:
            result_data = await _drain_stream(
                outcome.result, chunk_sink, idle_timeout_s=idle_timeout_s
            )
    except InvokeFailure:
        raise
    except Exception as exc:
        logger.warning(
            "invoke failed for config %s (type=%s, attempts=%s): %s",
            request.config_id,
            request.type,
            attempts,
            SensitiveDataFilter.filter_string(str(exc)),
        )
        if isinstance(exc, InvokeTimeout):
            failure = InvokeFailure(
                code=BizCode.SERVICE_UNAVAILABLE,
                message=SensitiveDataFilter.filter_string(str(exc)),
                http_status=504,
                attempts=attempts,
                # 超时无聚合异常的 failures 可取：渠道归因取实际最后尝试的候选（排流超时时
                # 即胜出候选），与失败 usage 口径一致
                channel_id=(
                    last_resolved.channel_id if last_resolved is not None else None
                ),
            )
        else:
            failure = _failure_from_exception(exc, attempts=attempts)
        if attempts and last_resolved is not None:
            # 已发起调用的失败要计数（告警面按 usage 的 failed 聚合）；零尝试失败无调用事实。
            # 归因取实际尝试的候选：入口 config 在组合链/换渠道下 provider/model_name 会落错列；
            # channel_id 优先聚合异常的末候选（exc.failures），terminal 异常无该属性时回落到
            # 实际尝试候选（否则失败事件丢渠道归因，与成功路径口径不一致）
            failure.usage_event = _usage_event(
                request=request,
                attribution=attribution,
                tenant_id=tenant_id,
                config_id=entry.model_config_id,
                channel_id=failure.channel_id or last_resolved.channel_id,
                provider=last_resolved.provider,
                model_name=last_resolved.model_name,
                attempts=attempts,
                latency_ms=int((time.perf_counter() - started) * 1000),
                status=UsageStatus.FAILED,
                error_type=type(exc).__name__,
            )
            await _report_usage(failure.usage_event)
        raise failure from exc

    latency_ms = int((time.perf_counter() - started) * 1000)
    status = UsageStatus.FALLBACK_SUCCEEDED if outcome.switched else UsageStatus.OK
    input_tokens, output_tokens = _result_tokens(result_data)
    usage_event = _usage_event(
        request=request,
        attribution=attribution,
        tenant_id=tenant_id,
        config_id=entry.model_config_id,
        channel_id=outcome.resolved.channel_id,
        provider=outcome.resolved.provider,
        model_name=outcome.resolved.model_name,
        attempts=outcome.attempts,
        latency_ms=latency_ms,
        status=status,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    await _report_usage(usage_event)
    return InvokeOutcome(data=result_data, status=status, usage_event=usage_event)


__all__ = ["InvokeAttribution", "InvokeFailure", "InvokeOutcome", "invoke"]
