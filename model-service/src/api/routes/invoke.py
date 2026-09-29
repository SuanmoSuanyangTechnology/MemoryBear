"""运行面 invoke 端点（``POST /internal/v1/invoke``，设计 §2.1/§2.4/§2.9）。

形态由 body ``stream`` 决定：``true`` → ``text/event-stream``（帧序见 §2.4）；
``false`` → 管理面同构 JSON 信封（``{"code":0,"data":{...}}``）。

身份：运行面 actor 可缺省（Celery 等后台调用方无用户身份，§2.2），tenant/source 仍必填。

错误边界：**中间件与入参校验**层失败（无内部凭据 401、body 422、缺来源头）发生在路由体
之外，只能是普通 JSON 响应；**业务失败**（可见性 404、空链/耗尽、供应商错误）在 SSE 下
200 已下发，改由 ``error`` 帧承载码面（``code`` 取 ``BizCode`` 名，§2.4「透传不重编码」），
``stream=false`` 下仍按 ``InvokeFailure.http_status`` 渲染信封 —— 两条路径的码面同源。

帧序：非流式族（G1 的 embedding/rerank、G2 的 llm）不发 ``chunk``，以 ``result`` 承载
结果体；llm 流式（G3）出 ``chunk*``（带严格递增 ``seq``）→ ``usage`` → ``done``，**不发
``result`` 帧**（宿主对该形态直接判契约违规）。失败先发 ``usage``(failed) 归因（仅已发起
调用时存在）再发 ``error``；``usage`` 是旁路事实，宿主不得把「缺 usage 帧」当失败。

超时三档（§2.9）：**上游首块档**在门面按候选生效（超时 → 瞬时错误 → 重试 → 换渠道）；
**首帧档**兜底整次请求（含选路/解密/换渠道），必须小于宿主 idle 预算——宿主先断则服务
白跑。非流式族的首帧即结果帧（llm 族单开宽档，见 config.py 的 llm 例外）；llm 流式的该档
只包住「解析 + 首 chunk」（排流在档外，长回复不被截断），块间由**空闲档**逐块计时。

断连（ASGI 取消生成器）在 ``finally`` 里 ``task.cancel()`` + ``await`` 收尾，避免泄漏
供应商连接与进程并发配额。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from functools import partial
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...errors import BizCode
from ...schemas.invoke_schema import InvokeRequestBody, LLMInvokeRequest
from ...services.invoke_service import (
    InvokeAttribution,
    InvokeFailure,
    InvokeOutcome,
    invoke,
)
from ..dependencies import InvokePrincipal, get_async_db, get_invoke_principal
from ..schemas.common import fail, success

logger = logging.getLogger(__name__)

RESOURCE_TYPE_HEADER = "X-Model-Resource-Type"
RESOURCE_ID_HEADER = "X-Model-Resource-ID"

EVENT_RESULT = "result"
EVENT_CHUNK = "chunk"
EVENT_USAGE = "usage"
EVENT_ERROR = "error"
EVENT_DONE = "done"
# embedding/rerank 无增量语义，以 result 帧承载整段结果；llm 流式（G3）出 chunk 帧（带 seq）

#: llm 流式的增量队列上界：够吸收供应商突发，又不掩盖消费方停滞（put 背压传导到上游）
_CHUNK_QUEUE_MAXSIZE = 256

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",  # 反代缓冲会让块卡在代理层，客户端整段才出
}

_SOURCE_REQUIRED = "缺少来源标识 X-Model-Source（usage 归因必填）"

DbSession = Annotated[AsyncSession, Depends(get_async_db)]
Caller = Annotated[InvokePrincipal, Depends(get_invoke_principal)]
InvokeCall = Callable[[], Awaitable[InvokeOutcome]]

router = APIRouter(prefix="/invoke", tags=["Invoke"])


def _frame(event: str, payload: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _error_frame_payload(failure: InvokeFailure) -> dict[str, Any]:
    """§2.4 error 帧载荷：码名 + 归因 + 重试提示（不含异常文案之外的内部细节）。"""
    return {
        "code": failure.code.name if isinstance(failure.code, BizCode) else str(failure.code),
        "message": failure.message,
        "attempts": failure.attempts,
        "channel_id": None if failure.channel_id is None else str(failure.channel_id),
        "retryable": failure.retryable,
    }


def _failure_frames(failure: InvokeFailure) -> list[str]:
    frames = []
    if failure.usage_event is not None:
        # 已发起调用才计数（告警面按 usage.failed 聚合）；未发起调用无调用事实
        frames.append(_frame(EVENT_USAGE, failure.usage_event.to_stream_dict()))
    frames.append(_frame(EVENT_ERROR, _error_frame_payload(failure)))
    return frames


def _result_frames(outcome: InvokeOutcome) -> list[str]:
    return [
        _frame(EVENT_RESULT, {"data": outcome.data}),
        _frame(EVENT_USAGE, outcome.usage_event.to_stream_dict()),
        _frame(EVENT_DONE, {"status": outcome.status.value}),
    ]


def _chunk_done_frames(outcome: InvokeOutcome) -> list[str]:
    """llm 流式收尾：usage → done，**不发 result 帧**（llm 流收 result 即契约违规）。"""
    return [
        _frame(EVENT_USAGE, outcome.usage_event.to_stream_dict()),
        _frame(EVENT_DONE, {"status": outcome.status.value}),
    ]


def _attribution(request: Request, principal: InvokePrincipal) -> InvokeAttribution:
    """归因头解析（§2.6 头通道）：来源必填，resource_id 非 UUID 属入参错误。"""
    source = (principal.source or "").strip()
    if not source:
        raise InvokeFailure(code=BizCode.INVALID_PARAMETER, message=_SOURCE_REQUIRED)
    raw_resource_id = (request.headers.get(RESOURCE_ID_HEADER) or "").strip()
    resource_id: UUID | None = None
    if raw_resource_id:
        try:
            resource_id = uuid.UUID(raw_resource_id)
        except ValueError as exc:
            raise InvokeFailure(
                code=BizCode.INVALID_PARAMETER,
                message=f"{RESOURCE_ID_HEADER} 必须是 UUID",
            ) from exc
    resource_type = (request.headers.get(RESOURCE_TYPE_HEADER) or "").strip() or None
    return InvokeAttribution(
        source_service=source,
        request_id=getattr(request.state, "trace_id", None),
        resource_type=resource_type,
        resource_id=resource_id,
    )


def _failure_response(failure: InvokeFailure) -> JSONResponse:
    """非流式失败：按门面给定的 HTTP 语义渲染信封。

    不经 ``http_status_for``：``MODEL_NOT_FOUND`` 在宿主 HTTP_MAPPING 里是 400，
    而 invoke 的可见性语义是 404（不泄露存在性，§2.8），门面已显式给定。
    """
    return JSONResponse(
        status_code=failure.http_status,
        content=fail(failure.code.value, failure.message, error=failure.message),
    )


async def _guarded(call: InvokeCall, *, timeout_s: float) -> InvokeOutcome:
    """首帧上限（§2.9）：整次 invoke 必须在预算内产出结果，超时即取消上游并终态。

    上游按候选的超时在门面内，本档是请求级兜底：宿主以 idle 计时，服务先答才不会白跑。
    """
    try:
        return await asyncio.wait_for(call(), timeout=timeout_s)
    except TimeoutError as exc:
        raise InvokeFailure(
            code=BizCode.SERVICE_UNAVAILABLE,
            message=f"模型调用超时（>{timeout_s:g}s 未产出结果）",
            http_status=504,
        ) from exc


async def _stream(task: asyncio.Task[InvokeOutcome]) -> AsyncIterator[str]:
    """SSE 帧序；上游以独立 task 持有，客户端断连时取消并等待收尾（§2.9）。"""
    closing = False
    try:
        try:
            outcome = await task
        except InvokeFailure as failure:
            for frame in _failure_frames(failure):
                yield frame
            return
        for frame in _result_frames(outcome):
            yield frame
    except GeneratorExit:
        # ASGI 2.4+ 断连走生成器 aclose：GeneratorExit 下再挂起即 RuntimeError，故只取消不等
        closing = True
        raise
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # 门面契约外逸出：给宿主终态帧，好过静默截断（宿主会判协议违规）
        logger.exception("invoke stream crashed: %s", type(exc).__name__)
        yield _frame(
            EVENT_ERROR,
            {
                "code": BizCode.INTERNAL_ERROR.name,
                "message": "模型调用内部错误",
                "attempts": 0,
                "channel_id": None,
                "retryable": True,
            },
        )
    finally:
        if not task.done():
            task.cancel()
        if not closing:
            # 收尾：吞掉被取消/已处理 task 的结局，不污染正在传播的取消
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


async def _chunk_stream(
    task: asyncio.Task[InvokeOutcome],
    sink: asyncio.Queue[dict[str, Any]],
) -> AsyncIterator[str]:
    """llm 流式帧序（§2.4）：chunk* → usage → done；上游以独立 task 持有。

    门面把增量推入有界队列（``await put`` 背压）；本层与 task 竞速：队列先可用即下发
    （保实时性），task 先结束则排空缓存块后收尾——此后不会再有 put，``get_nowait`` 安全
    （哨兵方案在队列满时 ``finally`` 里无法 await，会挂死路由）。失败帧序与 ``_stream`` 同源。
    """
    closing = False
    seq = 0
    try:
        try:
            while True:
                getter: asyncio.Task[dict[str, Any]] = asyncio.ensure_future(sink.get())
                try:
                    done, _ = await asyncio.wait(
                        {getter, task}, return_when=asyncio.FIRST_COMPLETED
                    )
                except BaseException:
                    # 天折（取消/断连）：未决 getter 必须收回，否则挂在队列上
                    getter.cancel()
                    raise
                if getter in done:
                    yield _frame(EVENT_CHUNK, {"message": getter.result(), "seq": seq})
                    seq += 1
                    continue
                getter.cancel()
                while True:
                    try:
                        payload = sink.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    yield _frame(EVENT_CHUNK, {"message": payload, "seq": seq})
                    seq += 1
                outcome = await task  # 正常返回或抛 InvokeFailure（缓存块已尽数下发）
                for frame in _chunk_done_frames(outcome):
                    yield frame
                return
        except InvokeFailure as failure:
            for frame in _failure_frames(failure):
                yield frame
        except GeneratorExit:
            # ASGI 2.4+ 断连走生成器 aclose：GeneratorExit 下再挂起即 RuntimeError，故只取消不等
            closing = True
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 门面契约外逸出：给宿主终态帧，好过静默截断
            logger.exception("invoke chunk stream crashed: %s", type(exc).__name__)
            yield _frame(
                EVENT_ERROR,
                {
                    "code": BizCode.INTERNAL_ERROR.name,
                    "message": "模型调用内部错误",
                    "attempts": 0,
                    "channel_id": None,
                    "retryable": True,
                },
            )
    finally:
        if not task.done():
            task.cancel()
        if not closing:
            # 收尾：吞掉被取消/已处理 task 的结局，不污染正在传播的取消
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


@router.post("", response_model=None)
async def invoke_model(
    request: Request,
    body: InvokeRequestBody,
    db: DbSession,
    principal: Caller,
) -> Response:
    """单次模型调用：服务侧解析/选路/解密/调用/归因（§2.2 调用方不持有凭据）。"""
    runtime = request.app.state.runtime
    settings = runtime.settings
    try:
        attribution = _attribution(request, principal)
    except InvokeFailure as failure:
        return _failure_response(failure)

    # llm 族单开档：生成整段回复天然慢（config.py 的 llm 例外），结构化族沿用短档
    if isinstance(body, LLMInvokeRequest):
        first_result_timeout_s = settings.invoke_llm_first_result_timeout_s
        total_timeout_s = settings.invoke_llm_total_timeout_s
    else:
        first_result_timeout_s = settings.invoke_first_result_timeout_s
        total_timeout_s = settings.invoke_total_timeout_s
    if isinstance(body, LLMInvokeRequest) and body.stream:
        # llm 流式：首帧总档下沉门面（只包解析 + 首块，不截排流），块间空闲档由门面逐块计时；
        # 本层不做整次 wait_for（会把长回复截断），也不发 result 帧（宿主按契约违规拒收）
        sink: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_CHUNK_QUEUE_MAXSIZE)
        task = asyncio.ensure_future(
            invoke(
                db,
                tenant_id=principal.tenant_id,
                request=body,
                attribution=attribution,
                first_result_timeout_s=first_result_timeout_s,
                first_frame_timeout_s=total_timeout_s,
                idle_timeout_s=settings.invoke_llm_idle_timeout_s,
                chunk_sink=sink,
                client_pool=runtime.model_runtime.pool,
            )
        )
        return StreamingResponse(
            _chunk_stream(task, sink),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )

    call = partial(
        invoke,
        db,
        tenant_id=principal.tenant_id,
        request=body,
        attribution=attribution,
        first_result_timeout_s=first_result_timeout_s,
        client_pool=runtime.model_runtime.pool,
    )
    if body.stream:
        task = asyncio.ensure_future(_guarded(call, timeout_s=total_timeout_s))
        return StreamingResponse(
            _stream(task), media_type="text/event-stream", headers=_SSE_HEADERS
        )
    try:
        outcome = await _guarded(call, timeout_s=total_timeout_s)
    except InvokeFailure as failure:
        return _failure_response(failure)
    return JSONResponse(status_code=200, content=success(data=outcome.data))


__all__ = ["router"]
