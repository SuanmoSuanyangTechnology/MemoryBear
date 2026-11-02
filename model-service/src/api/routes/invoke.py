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
白跑。非流式族的首帧即结果帧（llm 族单开宽档、媒体族 asr/image/video 第二宽档，见
config.py 的 llm/媒体例外）；llm 流式的该档只包住「解析 + 首 chunk」（排流在档外，长回复
不被截断），块间由**空闲档**逐块计时。媒体任务式轮询（asr/video）节奏经 ``media_poll_*``
传入门面，轮询上限小于媒体首块档。

断连（ASGI 取消生成器）在 ``finally`` 里 ``task.cancel()`` + ``await`` 收尾，避免泄漏
供应商连接与进程并发配额。
"""
from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...schemas.invoke_schema import InvokeRequestBody
from ...services.invoke_attribution import attribution_from_headers
from ...services.invoke_emitter import chunk_events, sse_frames, stream_events
from ...services.invoke_runner import build_invoke_plan, guarded
from ...services.invoke_service import InvokeFailure, invoke
from ..dependencies import InvokePrincipal, get_async_db, get_invoke_principal
from ..schemas.common import fail, success

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",  # reverse proxies would buffer whole generations
}

DbSession = Annotated[AsyncSession, Depends(get_async_db)]
Caller = Annotated[InvokePrincipal, Depends(get_invoke_principal)]

router = APIRouter(prefix="/invoke", tags=["Invoke"])


def _failure_response(failure: InvokeFailure) -> JSONResponse:
    """Non-stream failure: the facade hands in the HTTP semantics explicitly."""
    return JSONResponse(
        status_code=failure.http_status,
        content=fail(failure.code.value, failure.message, error=failure.message),
    )


@router.post("", response_model=None)
async def invoke_model(
    request: Request,
    body: InvokeRequestBody,
    db: DbSession,
    principal: Caller,
) -> Response:
    """Single model call: parse, route, invoke and attribute on the service side."""
    runtime = request.app.state.runtime
    try:
        attribution = attribution_from_headers(
            request.headers,
            source=principal.source,
            trace_id=getattr(request.state, "trace_id", None),
        )
    except InvokeFailure as failure:
        return _failure_response(failure)

    plan = build_invoke_plan(
        invoke,
        db,
        settings=runtime.settings,
        tenant_id=principal.tenant_id,
        request=body,
        attribution=attribution,
        runtime=runtime,
    )
    if plan.chunk_sink is not None:
        task = asyncio.ensure_future(plan.call())
        return StreamingResponse(
            sse_frames(chunk_events(task, plan.chunk_sink)),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )
    if body.stream:
        task = asyncio.ensure_future(
            guarded(plan.call, timeout_s=plan.total_timeout_s)
        )
        return StreamingResponse(
            sse_frames(stream_events(task)),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )
    try:
        outcome = await guarded(plan.call, timeout_s=plan.total_timeout_s)
    except InvokeFailure as failure:
        return _failure_response(failure)
    return JSONResponse(status_code=200, content=success(data=outcome.data))


__all__ = ["router"]
