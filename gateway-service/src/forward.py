"""Forward target resolution: path prefix → target service (static config → K8s Service DNS).

Forwarder: unified forwarding — external paths /api|/v1 → internal /internal/v1,
forward-whitelisted request headers plus all x-* headers (identity headers were
sanitized and re-injected by the middleware after termination). Credential face:
both strategies terminate credentials in the middleware, so this layer never
carries external authorization / x-api-key; the enterprise gateway rewrites the
middleware-injected internal token as authorization: Bearer (the
x-internal-token header itself is not forwarded — avoids a second credential
source downstream).

Streaming/buffered is not route-configured: every request goes send(stream=True)
and the split follows upstream response headers (response-header driven, see
is_streaming_response) — download/export stream chunk by chunk, JSON etc. are
buffered.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Protocol

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from .circuit import CircuitBreaker
from .metrics import (
    gateway_circuit_breaker_state,
    gateway_forward_requests_total,
    gateway_forward_retries_total,
    gateway_streaming_active_connections,
    gateway_upstream_errors_total,
)

logger = logging.getLogger(__name__)

# 透传白名单：网关只透传这些请求头（对齐老单体 transport.request_headers 语义）
_FORWARD_HEADERS = ("accept", "accept-language", "content-type", "content-length", "range")
# 回包剥除的逐跳/长度头：由转发层按块重建（与 Task 3 原逻辑一致）
_DROP_HEADERS = ("content-length", "transfer-encoding", "connection")
# 流式内容类型：命中即逐块透传。KB 现状流式响应即下载/导出
# （file.py/knowledge.py StreamingResponse：application/octet-stream、zip、csv），
# 无 text/event-stream 端点；新增流式类型加进集合即可，无需路由配置
_STREAMING_CONTENT_TYPES = frozenset({
    "application/octet-stream", "application/zip", "text/csv", "text/event-stream",
})


def is_streaming_response(headers) -> bool:
    """响应头驱动分流：流式内容类型或显式 Content-Disposition: attachment 下载头
    → 逐块透传；否则缓冲交付。响应侧事实是唯一依据——下载类请求（fetch/a 标签）
    请求头不携带流式意图，Accept 判别不可行。"""
    content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type in _STREAMING_CONTENT_TYPES:
        return True
    return "attachment" in headers.get("content-disposition", "").lower()


class TargetRoute(BaseModel):
    path_prefix: str
    service: str
    base_url: str      # K8s Service DNS 名，如 http://mem-knowledge:8080
    aud: str           # 内部 token 受众 = 目标服务名


class TargetResolver(Protocol):
    """抽象留扩展点：static → Consul/服务网格 只换实现（设计 4.1.1 / 4.6）。"""

    def resolve(self, path: str) -> TargetRoute | None: ...


class StaticTargetResolver:
    def __init__(self, routes: list[TargetRoute]) -> None:
        # 前缀最长匹配：按 path_prefix 长度降序，首个命中即返回
        self._routes = sorted(routes, key=lambda r: len(r.path_prefix), reverse=True)

    def resolve(self, path: str) -> TargetRoute | None:
        for route in self._routes:
            if path.startswith(route.path_prefix):
                return route
        return None


class Forwarder:
    def __init__(self, client: httpx.AsyncClient,
                 circuit: CircuitBreaker | None = None,
                 streaming_max_connections: int = 100,
                 sse_idle_timeout: float = 300.0,
                 read_timeout: float = 30.0) -> None:
        self._client = client
        self._circuit = circuit or CircuitBreaker()
        self._streaming_max = streaming_max_connections
        self._active_streams = 0
        self._stream_lock = asyncio.Lock()
        # 流式空闲看门狗：读无超时上限，但上游超过该时长不发数据即视为挂死，回收流
        self._sse_idle_timeout = sse_idle_timeout
        # 缓冲分支体读取超时（对齐老 forward() 的 read=30s）：响应头已到、体迟迟
        # 不来/读断时快速失败，不占满看门狗时长
        self._read_timeout = read_timeout

    def internal_path(self, external_path: str) -> str:
        if external_path.startswith("/internal/v1/"):
            return external_path
        if external_path.startswith("/api/"):
            return "/internal/v1" + external_path[len("/api"):]
        if external_path.startswith("/v1/"):
            return "/internal/v1" + external_path[len("/v1"):]
        return external_path

    def build_headers(self, request: Request) -> dict[str, str]:
        headers: dict[str, str] = {}
        for name in _FORWARD_HEADERS:
            value = request.headers.get(name)
            if value is not None:
                headers[name] = value
        # Identity headers (x-user-id / x-tenant-id / x-kb-* / x-model-* ...) were
        # sanitized and injected by the middleware after termination; re-serialize
        # them from the scope here. Credential headers no longer reach this layer:
        # only the enterprise internal token is rewritten as Authorization below,
        # and x-api-key / x-internal-token stay gateway-internal (defense in depth).
        internal = getattr(request.state, "internal_token", None)
        for name, value in request.headers.items():
            low = name.lower()
            if low.startswith("x-") and low not in ("x-api-key", "x-internal-token"):
                headers[name] = value
        if internal:
            # Enterprise gateway mode: rewrite the internal token as
            # authorization: Bearer <internal token> (the x-internal-token header
            # itself is not forwarded — avoids a second credential source downstream).
            headers["authorization"] = f"Bearer {internal}"
        return headers

    async def forward(self, request: Request, route: TargetRoute) -> Response:
        # 熔断门只约束新连接：开路期间新请求直接 502，已建立的流不受影响（评审稿 4.1.4）
        if self._circuit.is_open():
            gateway_circuit_breaker_state.labels(target=route.service).set(1)
            return JSONResponse(status_code=502, content={"detail": "circuit open"})
        gateway_circuit_breaker_state.labels(target=route.service).set(0)
        # request.url.path 不含查询串，query 须单独拼回，否则分页/过滤等参数静默丢失
        url = route.base_url + self.internal_path(request.url.path)
        if request.url.query:
            url += "?" + request.url.query
        headers = self.build_headers(request)
        body = await request.body()
        # 评审稿 4.1.4：GET/HEAD 对上游 502/503/504 状态码重试 1 次（200ms 抖动），
        # 传输异常（超时/连不上）同规则重试；非幂等（POST 等）不重试。重试的首次
        # 5xx 不计数/不记熔断（breaker 只跟踪传输故障），最终响应才按现状记录。
        retriable = request.method in ("GET", "HEAD")
        for attempt in range(2 if retriable else 1):
            # client.request() 会预读完整响应（send 默认 stream=False），流式响应
            # 预读既让 aiter_raw 抛 StreamConsumed，又会永远挂起等 EOF——必须先
            # send(stream=True) 拿未消费的响应；流式/缓冲的分流依据是上游响应头
            # （content-type / content-disposition，见 is_streaming_response），
            # 只有拿到响应头才能判定。响应头等待阶段无读超时（QA 导出等生成型
            # 端点响应头可能晚到；连接建立 connect=5s 兜底）
            upstream_request = self._client.build_request(
                request.method, url, headers=headers, content=body,
                timeout=httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0),
            )
            try:
                upstream = await self._client.send(upstream_request, stream=True)
            except httpx.TimeoutException:
                gateway_upstream_errors_total.labels(
                    target=route.service, error_type="timeout").inc()
                if not retriable or attempt == 1:
                    self._circuit.record_failure()
                    return JSONResponse(status_code=504, content={"detail": "upstream timeout"})
                # 重试间隔 200ms 抖动：避免多个连接同时重试造成惊群
                await asyncio.sleep(random.uniform(0.15, 0.25))
                continue
            except httpx.HTTPError:
                gateway_upstream_errors_total.labels(
                    target=route.service, error_type="connect").inc()
                if not retriable or attempt == 1:
                    self._circuit.record_failure()
                    return JSONResponse(status_code=502, content={"detail": "upstream unavailable"})
                # 重试间隔 200ms 抖动：避免多个连接同时重试造成惊群
                await asyncio.sleep(random.uniform(0.15, 0.25))
                continue
            if upstream.status_code in (502, 503, 504) and retriable and attempt == 0:
                # 5xx 时响应体尚未生成/无意义：关连接重发（流式下载同样受益——
                # 下载 503 重试一次无害）
                gateway_forward_retries_total.labels(
                    target=route.service, method=request.method).inc()
                await upstream.aclose()
                await asyncio.sleep(random.uniform(0.15, 0.25))
                continue
            # 建连成功即计数；流中途失败不 record_failure（熔断只约束新连接，
            # 已建立流的中断不代表上游整体不健康）
            gateway_forward_requests_total.labels(
                target=route.service, status_class=f"{upstream.status_code // 100}xx").inc()
            self._circuit.record_success()
            # 响应头驱动分流：流式类型（下载/导出）逐块透传，其余缓冲交付
            if is_streaming_response(upstream.headers):
                return await self._stream_response(request, route, upstream, url)
            return await self._buffered_response(route, upstream)

    async def _stream_response(self, request: Request, route: TargetRoute,
                               upstream: httpx.Response, url: str) -> Response:
        # 并发槽在确认是流式响应后才申请：缓冲请求不受 _streaming_max 约束（判型
        # 前无法预知流式/缓冲，缓冲请求若也先占槽，JSON 并发会被误限到 100）
        async with self._stream_lock:
            if self._active_streams >= self._streaming_max:
                # 并发槽满：丢弃已建连响应（aclose 中断上游生成），拒收不计失败
                await upstream.aclose()
                return JSONResponse(status_code=503, content={"detail": "too many streams"})
            self._active_streams += 1
        gateway_streaming_active_connections.inc()
        active = True

        def release_stream() -> None:
            # 槽位在流真正结束时释放：_stream_response 返回时流尚未开始，若在
            # finally 里释放，并发上限与活跃连接 gauge 将恒为 0
            nonlocal active
            if active:
                active = False
                self._active_streams -= 1
                gateway_streaming_active_connections.dec()

        async def body_iter():
            try:
                # 读无超时上限，但空闲超过 sse_idle_timeout（默认 300s）即视为
                # 上游挂死：看门狗回收，finally 释放并发槽位并关闭上游连接
                it = upstream.aiter_raw()
                while True:
                    try:
                        chunk = await asyncio.wait_for(
                            it.__anext__(), timeout=self._sse_idle_timeout)
                    except StopAsyncIteration:
                        break
                    except TimeoutError:
                        logger.warning("stream idle timeout (%.0fs), closing upstream: %s %s",
                                       self._sse_idle_timeout, request.method, url)
                        break
                    except httpx.HTTPError:
                        logger.warning("upstream read error, closing stream: %s %s",
                                       request.method, url)
                        break
                    yield chunk
            finally:
                # 客户端断连/空闲超时会取消本生成器 → finally 关闭上游（双向取消）
                release_stream()
                await upstream.aclose()
        # 响应头对齐缓冲分支全透传语义：复制上游头并剥逐跳头（content-length 由
        # 分块传输重建）；Cache-Control 不补写（端到端缓存语义权威在源，大文件
        # 下载的缓存策略不应被网关改写）；X-Accel-Buffering 强写 no（发给入口
        # nginx 的本跳指令，防响应被缓冲导致下载/导出无进展）
        passthrough = {k.lower(): v for k, v in upstream.headers.items()
                       if k.lower() not in _DROP_HEADERS}
        passthrough["x-accel-buffering"] = "no"
        return StreamingResponse(body_iter(), status_code=upstream.status_code,
                                 headers=passthrough)

    async def _buffered_response(self, route: TargetRoute,
                                 upstream: httpx.Response) -> Response:
        # 缓冲交付：响应头已到，体读取给显式 read_timeout（默认 30s，对齐老
        # forward() 的 read=30）；超时/读断快速失败，不占满流式看门狗时长
        try:
            content = await asyncio.wait_for(upstream.aread(), timeout=self._read_timeout)
        except TimeoutError:
            gateway_upstream_errors_total.labels(
                target=route.service, error_type="timeout").inc()
            self._circuit.record_failure()
            await upstream.aclose()
            return JSONResponse(status_code=504, content={"detail": "upstream timeout"})
        except httpx.HTTPError:
            gateway_upstream_errors_total.labels(
                target=route.service, error_type="connect").inc()
            self._circuit.record_failure()
            await upstream.aclose()
            return JSONResponse(status_code=502, content={"detail": "upstream unavailable"})
        return Response(
            content=content,
            status_code=upstream.status_code,
            headers={k.lower(): v for k, v in upstream.headers.items()
                     if k.lower() not in _DROP_HEADERS},
        )
