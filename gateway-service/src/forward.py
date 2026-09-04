"""转发目标解析：路径前缀 → 目标服务（首期 static 配置 → K8s Service DNS）。

Forwarder：普通请求转发——外部路径 /api|/v1 → 内部 /internal/v1，白名单请求头
透传 + x-* 身份头透传。凭据头按部署模式处理（设计 4.1.2 / 4.2.1）：
gateway 模式把中间件注入的内部 token 改写为 authorization: Bearer（x-internal-token
不透传，避免双凭据信源）；direct 模式原样透传外部 authorization / x-api-key 供下游自验
（Task 4 追加流式转发）。
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
                 sse_idle_timeout: float = 300.0) -> None:
        self._client = client
        self._circuit = circuit or CircuitBreaker()
        self._streaming_max = streaming_max_connections
        self._active_streams = 0
        self._stream_lock = asyncio.Lock()
        # SSE 空闲看门狗：读无超时上限，但上游超过该时长不发数据即视为挂死，回收流
        self._sse_idle_timeout = sse_idle_timeout

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
        # 身份头（x-user-id / x-tenant-id / ...）已由中间件 _rewrite_headers 注入
        # request.scope["headers"]（claims 权威，设计 2.4），此处从 scope headers 重序列化透传；
        # 内部 token 在 request.state.internal_token，凭据头走下方双模式分支：
        # x-internal-token 仅策略内部使用，不透传（设计 4.1.2）
        internal = getattr(request.state, "internal_token", None)
        for name, value in request.headers.items():
            low = name.lower()
            if low.startswith("x-") and low not in ("x-api-key", "x-internal-token"):
                if internal and low.startswith("x-kb"):
                    # gateway 模式：claims 只注入 x-user-id/x-tenant-id/x-workspace-id，
                    # 客户端 X-KB-* 一律剥除——透传会让 kb 公开路径 fallback 授信客户端
                    # 伪造身份头（跨工作区拉取），下游只见权威身份
                    continue
                headers[name] = value
        if internal:
            # gateway 模式：外部凭据已由中间件终结销毁，转发层改写为
            # authorization: Bearer <内部 token>（设计 4.1.2 凭据行；x-internal-token 不透传）
            headers["authorization"] = f"Bearer {internal}"
        else:
            # direct 模式：透传外部凭据给下游服务自验（设计 4.2.1：透传原样附到上游请求）
            if "authorization" in request.headers:
                headers["authorization"] = request.headers["authorization"]
            if "x-api-key" in request.headers:
                headers["x-api-key"] = request.headers["x-api-key"]
        return headers

    async def forward(self, request: Request, route: TargetRoute) -> Response:
        if self._circuit.is_open():
            gateway_circuit_breaker_state.labels(target=route.service).set(1)
            return JSONResponse(status_code=502, content={"detail": "circuit open"})
        gateway_circuit_breaker_state.labels(target=route.service).set(0)
        # 评审稿 4.1.4：GET/HEAD 对上游 502/503/504 状态码重试 1 次（200ms 抖动），
        # 传输异常（超时/连不上）同规则重试；非幂等（POST 等）不重试。重试的首次
        # 5xx 不计数/不记熔断（breaker 只跟踪传输故障），最终响应才按现状记录。
        # request.url.path 不含查询串，query 须单独拼回，否则分页/过滤等参数静默丢失
        url = route.base_url + self.internal_path(request.url.path)
        if request.url.query:
            url += "?" + request.url.query
        headers = self.build_headers(request)
        body = await request.body()
        retriable = request.method in ("GET", "HEAD")
        for attempt in range(2 if retriable else 1):
            try:
                upstream = await self._client.request(
                    request.method, url, headers=headers, content=body,
                    timeout=httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0),
                )
                if upstream.status_code in (502, 503, 504) and retriable and attempt == 0:
                    gateway_forward_retries_total.labels(
                        target=route.service, method=request.method).inc()
                    await asyncio.sleep(random.uniform(0.15, 0.25))
                    continue
                gateway_forward_requests_total.labels(
                    target=route.service, status_class=f"{upstream.status_code // 100}xx").inc()
                self._circuit.record_success()
                return Response(
                    content=upstream.content,
                    status_code=upstream.status_code,
                    headers={k: v for k, v in upstream.headers.items()
                             if k.lower() not in _DROP_HEADERS},
                )
            except httpx.TimeoutException:
                gateway_upstream_errors_total.labels(
                    target=route.service, error_type="timeout").inc()
                if not retriable or attempt == 1:
                    self._circuit.record_failure()
                    return JSONResponse(status_code=504, content={"detail": "upstream timeout"})
                # 重试间隔 200ms 抖动：避免多个连接同时重试造成惊群
                await asyncio.sleep(random.uniform(0.15, 0.25))
            except httpx.HTTPError:
                gateway_upstream_errors_total.labels(
                    target=route.service, error_type="connect").inc()
                if not retriable or attempt == 1:
                    self._circuit.record_failure()
                    return JSONResponse(status_code=502, content={"detail": "upstream unavailable"})
                # 重试间隔 200ms 抖动：避免多个连接同时重试造成惊群
                await asyncio.sleep(random.uniform(0.15, 0.25))

    async def forward_stream(self, request: Request, route: TargetRoute) -> Response:
        async with self._stream_lock:
            if self._active_streams >= self._streaming_max:
                return JSONResponse(status_code=503, content={"detail": "too many streams"})
            self._active_streams += 1
        gateway_streaming_active_connections.inc()
        active = True

        def release_stream() -> None:
            # 槽位在流真正结束（或建连失败）时释放：forward_stream 返回时流尚未开始，
            # 若在函数 finally 里释放，并发上限与活跃连接 gauge 将恒为 0
            nonlocal active
            if active:
                active = False
                self._active_streams -= 1
                gateway_streaming_active_connections.dec()

        try:
            url = route.base_url + self.internal_path(request.url.path)
            if request.url.query:
                url += "?" + request.url.query
            headers = self.build_headers(request)
            body = await request.body()
            # client.request() 会预读完整响应（send 默认 stream=False）；SSE 是
            # 无限流——预读既让 aiter_raw 抛 StreamConsumed，又会永远挂起等 EOF。
            # 必须 send(stream=True) 拿未消费的流式响应。
            upstream_request = self._client.build_request(
                request.method, url, headers=headers, content=body,
                timeout=httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0),
            )
            try:
                upstream = await self._client.send(upstream_request, stream=True)
            except httpx.HTTPError:
                release_stream()
                return JSONResponse(status_code=502, content={"detail": "upstream unavailable"})

            async def body_iter():
                try:
                    # SSE 读无超时上限，但空闲超过 sse_idle_timeout（默认 300s）即视为
                    # 上游挂死：看门狗回收，finally 释放并发槽位并关闭上游连接
                    it = upstream.aiter_raw()
                    while True:
                        try:
                            chunk = await asyncio.wait_for(
                                it.__anext__(), timeout=self._sse_idle_timeout)
                        except StopAsyncIteration:
                            break
                        except TimeoutError:
                            logger.warning("SSE idle timeout (%.0fs), closing upstream: %s %s",
                                           self._sse_idle_timeout, request.method, url)
                            break
                        yield chunk
                finally:
                    # 客户端断连/空闲超时会取消本生成器 → finally 关闭上游（双向取消）
                    release_stream()
                    await upstream.aclose()
            content_type = upstream.headers.get("content-type", "text/event-stream")
            return StreamingResponse(body_iter(), status_code=upstream.status_code,
                                     media_type=content_type,
                                     headers={"Cache-Control": "no-cache",
                                              "X-Accel-Buffering": "no"})
        except BaseException:
            # 建连阶段（body 读取/握手）异常：释放槽位后原样抛出
            release_stream()
            raise
