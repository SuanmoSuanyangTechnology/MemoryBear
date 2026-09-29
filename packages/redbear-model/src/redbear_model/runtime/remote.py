"""Remote invoke transport: SSE/JSON frame contract for the model service.

The package side of the M8 invoke seam. It knows *how* to talk to
``POST {base}/internal/v1/invoke`` and nothing about *who* to call: no
credentials, no channel selection, no retries, no provider knowledge. Selection,
decryption, failover and usage emission all live behind the endpoint.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, NoReturn
from urllib.parse import quote
from uuid import UUID

import httpx
from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    ChatMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from redbear_model.contracts import ModelType
from redbear_model.errors import (
    RemoteInvokeFailedError,
    RemoteInvokeIdleTimeoutError,
    RemoteInvokeProtocolError,
    RemoteInvokeUnavailableError,
)

INVOKE_PATH = "/internal/v1/invoke"

logger = logging.getLogger(__name__)

# 单帧上限：只防契约违规（超长/未终止的行），不是流量整形。
MAX_EVENT_BYTES = 4 * 1024 * 1024

HEADER_TENANT_ID = "X-Model-Tenant-ID"
HEADER_ACTOR_ID = "X-Model-Actor-ID"
HEADER_ACTOR_NAME = "X-Model-Actor-Name"
HEADER_WORKSPACE_ID = "X-Model-Workspace-ID"
HEADER_SOURCE = "X-Model-Source"
HEADER_RESOURCE_TYPE = "X-Model-Resource-Type"
HEADER_RESOURCE_ID = "X-Model-Resource-ID"
HEADER_TRACE_ID = "X-Trace-Id"

EVENT_STREAM_CONTENT_TYPE = "text/event-stream"

_FRAME_KINDS = frozenset({"chunk", "result", "usage", "error", "done"})
_DONE_STATUSES = frozenset({"ok", "fallback_succeeded"})


@dataclass(frozen=True, slots=True)
class InvokeChunkFrame:
    """One streamed ``AIMessageChunk`` dump (``seq`` 严格递增，用于检出乱序/重复）。"""

    message: dict[str, Any]
    seq: int


@dataclass(frozen=True, slots=True)
class InvokeResultFrame:
    """Single-result families (embedding / rerank) and non-streamed llm output."""

    data: Any


@dataclass(frozen=True, slots=True)
class InvokeUsageFrame:
    """Bypass fact: the emitted ``UsageEvent`` stream dict. Callers may ignore it."""

    event: dict[str, Any]


@dataclass(frozen=True, slots=True)
class InvokeDoneFrame:
    """Control-plane terminal status."""

    status: str


InvokeFrame = (
    InvokeChunkFrame | InvokeResultFrame | InvokeUsageFrame | InvokeDoneFrame
)


class InvokeTimeouts(BaseModel):
    """Connect / inter-frame idle / write budgets.

    ``idle_s`` maps onto httpx's ``read`` timeout, which applies per read
    operation -- for a streaming response that *is* the gap between frames. The
    host management-plane ``read`` budget is a whole-response semantic and must
    not be reused here: a long generation would trip it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    connect_s: float = 5.0
    idle_s: float = 60.0
    write_s: float = 60.0
    pool_s: float | None = None

    def httpx_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.connect_s,
            read=self.idle_s,
            write=self.write_s,
            pool=self.pool_s,
        )


class InvokeRequest(BaseModel):
    """``config_id`` + family + params. Never carries credentials or model identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    config_id: UUID
    type: ModelType
    params: dict[str, Any] = Field(default_factory=dict)
    stream: bool = True

    def to_body(self) -> dict[str, Any]:
        return {
            "config_id": str(self.config_id),
            "type": self.type.value,
            "stream": self.stream,
            "params": self.params,
        }


@dataclass(frozen=True, slots=True)
class InvokeTarget:
    """Attribution headers: who is calling, on behalf of which tenant/business fact."""

    tenant_id: UUID
    source: str
    actor_id: UUID | None = None
    actor_name: str | None = None
    workspace_id: UUID | None = None
    resource_type: str | None = None
    resource_id: UUID | None = None
    trace_id: str | None = None

    def headers(self) -> dict[str, str]:
        headers = {
            HEADER_TENANT_ID: str(self.tenant_id),
            HEADER_SOURCE: self.source,
        }
        if self.actor_id is not None:
            headers[HEADER_ACTOR_ID] = str(self.actor_id)
        if self.actor_name:
            # 头值须为 ASCII（RFC 9110）：UTF-8 百分号编码承载，服务侧 unquote 还原
            headers[HEADER_ACTOR_NAME] = quote(self.actor_name, safe="")
        if self.workspace_id is not None:
            headers[HEADER_WORKSPACE_ID] = str(self.workspace_id)
        if self.resource_type:
            headers[HEADER_RESOURCE_TYPE] = self.resource_type
        if self.resource_id is not None:
            headers[HEADER_RESOURCE_ID] = str(self.resource_id)
        if self.trace_id:
            headers[HEADER_TRACE_ID] = self.trace_id
        return headers


class SSEEventDecoder:
    """Line-by-line SSE field accumulation, shared by the sync and async readers."""

    def __init__(self) -> None:
        self._event: str | None = None
        self._data: list[str] = []
        self._size = 0

    def feed(self, line: str) -> tuple[str, str] | None:
        """Consume one line; return a completed ``(event, data)`` pair if any."""

        if line == "":
            return self.flush()
        if line.startswith(":"):
            return None
        if line.startswith("event:"):
            self._event = line[len("event:") :].strip()
            return None
        if line.startswith("data:"):
            value = line[len("data:") :].removeprefix(" ")
            self._size += len(value)
            if self._size > MAX_EVENT_BYTES:
                raise RemoteInvokeProtocolError(
                    f"SSE event exceeds {MAX_EVENT_BYTES} bytes"
                )
            self._data.append(value)
        # id/retry 等其余字段与契约无关，忽略
        return None

    def flush(self) -> tuple[str, str] | None:
        if self._event is None and not self._data:
            return None
        event, data = self._event or "message", "\n".join(self._data)
        self._event, self._data, self._size = None, [], 0
        return event, data


def parse_sse_events(lines: Iterable[str]) -> Iterator[tuple[str, str]]:
    """Decode SSE fields into ``(event, data)`` pairs.

    A trailing event without its terminating blank line is still flushed: the
    final frame arrives only after the peer closes, and a truncated payload is
    caught by JSON decoding rather than silently dropped.
    """

    decoder = SSEEventDecoder()
    for line in lines:
        pair = decoder.feed(line)
        if pair is not None:
            yield pair
    trailing = decoder.flush()
    if trailing is not None:
        yield trailing


async def parse_sse_events_async(
    lines: AsyncIterator[str],
) -> AsyncIterator[tuple[str, str]]:
    """Async twin of :func:`parse_sse_events` for ``response.aiter_lines()``."""

    decoder = SSEEventDecoder()
    async for line in lines:
        pair = decoder.feed(line)
        if pair is not None:
            yield pair
    trailing = decoder.flush()
    if trailing is not None:
        yield trailing


def _loads(data: str) -> Any:
    try:
        return json.loads(data)
    except ValueError as exc:
        raise RemoteInvokeProtocolError(f"malformed frame JSON: {exc}") from exc


def _as_dict(value: Any, *, kind: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RemoteInvokeProtocolError(
            f"{kind} frame payload must be an object, got {type(value).__name__}"
        )
    return value


def decode_frame(event: str, data: str) -> InvokeFrame:
    """One SSE event → frame. ``error`` raises instead of yielding a frame."""

    if event not in _FRAME_KINDS:
        raise RemoteInvokeProtocolError(f"unknown frame kind: {event!r}")
    payload = _loads(data)
    if event == "chunk":
        body = _as_dict(payload, kind="chunk")
        if "message" not in body:
            raise RemoteInvokeProtocolError("chunk frame is missing 'message'")
        seq = body.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool):
            raise RemoteInvokeProtocolError(f"chunk frame has non-integer seq: {seq!r}")
        return InvokeChunkFrame(message=_as_dict(body["message"], kind="chunk"), seq=seq)
    if event == "result":
        body = _as_dict(payload, kind="result")
        if "data" not in body:
            raise RemoteInvokeProtocolError("result frame is missing 'data'")
        return InvokeResultFrame(data=body["data"])
    if event == "usage":
        return InvokeUsageFrame(event=_as_dict(payload, kind="usage"))
    if event == "done":
        status = _as_dict(payload, kind="done").get("status")
        if status not in _DONE_STATUSES:
            raise RemoteInvokeProtocolError(f"unknown done status: {status!r}")
        return InvokeDoneFrame(status=status)
    failure = _as_dict(payload, kind="error")
    raise RemoteInvokeFailedError(
        code=failure.get("code"),
        message=str(failure.get("message") or ""),
        attempts=_optional_int(failure.get("attempts")),
        channel_id=_optional_uuid(failure.get("channel_id")),
        retryable=bool(failure.get("retryable")),
    )


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def envelope_failure(payload: dict[str, Any], http_status: int) -> RemoteInvokeFailedError:
    """Failure envelope (``code != 0``) → terminal error."""

    message = payload.get("msg") or payload.get("message") or ""
    return RemoteInvokeFailedError(
        code=payload.get("code"),
        message=str(message),
        http_status=http_status,
        retryable=http_status >= 500,
    )


def _parse_envelope(raw: bytes) -> dict[str, Any] | None:
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _unwrap_envelope(raw: bytes, http_status: int) -> Any:
    """``{code, msg, data}`` → ``data``; a missing ``code`` is a contract breach."""

    payload = _parse_envelope(raw)
    if payload is None:
        raise RemoteInvokeProtocolError(f"malformed envelope (status={http_status})")
    code = payload.get("code")
    if code is None:
        raise RemoteInvokeProtocolError("envelope is missing 'code'")
    if code:
        raise envelope_failure(payload, http_status)
    return payload.get("data")


class _FrameStream:
    """Shared frame sequencing: strictly increasing ``seq``, ``done`` terminates."""

    def __init__(self) -> None:
        self._last_seq: int | None = None
        self._done = False

    @property
    def done(self) -> bool:
        return self._done

    def feed(self, event: str, data: str) -> InvokeFrame:
        frame = decode_frame(event, data)
        if isinstance(frame, InvokeChunkFrame):
            if self._last_seq is not None and frame.seq <= self._last_seq:
                raise RemoteInvokeProtocolError(
                    f"chunk seq went backwards: {frame.seq} after {self._last_seq}"
                )
            self._last_seq = frame.seq
        elif isinstance(frame, InvokeDoneFrame):
            self._done = True
        return frame

    def assert_complete(self) -> None:
        if not self._done:
            raise RemoteInvokeProtocolError("stream ended before the done frame")


def _request_headers(target: InvokeTarget, *, accept: str) -> dict[str, str]:
    return {
        **target.headers(),
        "Content-Type": "application/json",
        "Accept": accept,
    }


class AsyncInvokeTransport:
    """Async SSE/JSON invoke client. The caller owns the injected ``httpx`` client."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str | None = None,
        timeouts: InvokeTimeouts | None = None,
    ):
        self._client = client
        self._base_url = base_url.rstrip("/") if base_url else None
        self._timeouts = timeouts or InvokeTimeouts()

    def _timeout(self) -> httpx.Timeout:
        return self._timeouts.httpx_timeout()

    def _url(self) -> str:
        if self._base_url is None:
            return INVOKE_PATH
        return f"{self._base_url}{INVOKE_PATH}"

    async def _send(
        self, request: InvokeRequest, target: InvokeTarget, *, stream: bool
    ) -> httpx.Response:
        """Response form is one decision: ``Accept`` and body ``stream`` move together.

        The service picks its response form from the body flag while the client decodes
        it from ``Accept``; a mismatch reads SSE as an envelope (or the reverse), so both
        are bound here and callers never pair them by hand.
        """

        accept = EVENT_STREAM_CONTENT_TYPE if stream else "application/json"
        body = request.model_copy(update={"stream": stream}).to_body()
        http_request = self._client.build_request(
            "POST",
            self._url(),
            headers=_request_headers(target, accept=accept),
            json=body,
            timeout=self._timeout(),
        )
        try:
            return await self._client.send(http_request, stream=True)
        except httpx.ReadTimeout as exc:
            raise RemoteInvokeIdleTimeoutError(self._timeouts.idle_s) from exc
        except httpx.TimeoutException as exc:
            raise RemoteInvokeUnavailableError(
                f"remote invoke timed out: {type(exc).__name__}"
            ) from exc
        except httpx.RequestError as exc:
            raise RemoteInvokeUnavailableError(
                f"remote invoke connection failed: {type(exc).__name__}"
            ) from exc

    async def _raise_for_non_stream(self, response: httpx.Response) -> NoReturn:
        """Non-SSE response: parse the failure envelope, else call it a protocol breach."""

        raw = await response.aread()
        payload = _parse_envelope(raw)
        if payload is None or payload.get("code") in (0, None):
            raise RemoteInvokeProtocolError(
                "expected an SSE stream, got status="
                f"{response.status_code} content-type="
                f"{response.headers.get('content-type')!r}"
            )
        raise envelope_failure(payload, response.status_code)

    async def stream(
        self, request: InvokeRequest, target: InvokeTarget
    ) -> AsyncIterator[InvokeFrame]:
        """Iterate frames until ``done``. Abandoning the iterator cancels upstream."""

        response = await self._send(request, target, stream=True)
        try:
            content_type = response.headers.get("content-type", "")
            if EVENT_STREAM_CONTENT_TYPE not in content_type.lower():
                await self._raise_for_non_stream(response)
            state = _FrameStream()
            try:
                async for event, data in parse_sse_events_async(response.aiter_lines()):
                    yield state.feed(event, data)
                    if state.done:
                        return
            except httpx.ReadTimeout as exc:
                raise RemoteInvokeIdleTimeoutError(self._timeouts.idle_s) from exc
            except httpx.RequestError as exc:
                raise RemoteInvokeUnavailableError(
                    f"remote invoke stream broke: {type(exc).__name__}"
                ) from exc
            state.assert_complete()
        finally:
            await response.aclose()

    async def call(self, request: InvokeRequest, target: InvokeTarget) -> Any:
        """Non-stream call: returns the family result carried by ``data``."""

        response = await self._send(request, target, stream=False)
        try:
            raw = await response.aread()
        except httpx.ReadTimeout as exc:
            raise RemoteInvokeIdleTimeoutError(self._timeouts.idle_s) from exc
        except httpx.RequestError as exc:
            raise RemoteInvokeUnavailableError(
                f"remote invoke read failed: {type(exc).__name__}"
            ) from exc
        finally:
            await response.aclose()
        return _unwrap_envelope(raw, response.status_code)


class SyncInvokeTransport:
    """Synchronous twin. The caller owns the injected ``httpx.Client``."""

    def __init__(
        self,
        client: httpx.Client,
        *,
        base_url: str | None = None,
        timeouts: InvokeTimeouts | None = None,
    ):
        self._client = client
        self._base_url = base_url.rstrip("/") if base_url else None
        self._timeouts = timeouts or InvokeTimeouts()

    def _timeout(self) -> httpx.Timeout:
        return self._timeouts.httpx_timeout()

    def _url(self) -> str:
        if self._base_url is None:
            return INVOKE_PATH
        return f"{self._base_url}{INVOKE_PATH}"

    def _send(
        self, request: InvokeRequest, target: InvokeTarget, *, stream: bool
    ) -> httpx.Response:
        """Response form is one decision: ``Accept`` and body ``stream`` move together."""

        accept = EVENT_STREAM_CONTENT_TYPE if stream else "application/json"
        body = request.model_copy(update={"stream": stream}).to_body()
        http_request = self._client.build_request(
            "POST",
            self._url(),
            headers=_request_headers(target, accept=accept),
            json=body,
            timeout=self._timeout(),
        )
        try:
            return self._client.send(http_request, stream=True)
        except httpx.ReadTimeout as exc:
            raise RemoteInvokeIdleTimeoutError(self._timeouts.idle_s) from exc
        except httpx.TimeoutException as exc:
            raise RemoteInvokeUnavailableError(
                f"remote invoke timed out: {type(exc).__name__}"
            ) from exc
        except httpx.RequestError as exc:
            raise RemoteInvokeUnavailableError(
                f"remote invoke connection failed: {type(exc).__name__}"
            ) from exc

    def _raise_for_non_stream(self, response: httpx.Response) -> NoReturn:
        raw = response.read()
        payload = _parse_envelope(raw)
        if payload is None or payload.get("code") in (0, None):
            raise RemoteInvokeProtocolError(
                "expected an SSE stream, got status="
                f"{response.status_code} content-type="
                f"{response.headers.get('content-type')!r}"
            )
        raise envelope_failure(payload, response.status_code)

    def stream(
        self, request: InvokeRequest, target: InvokeTarget
    ) -> Iterator[InvokeFrame]:
        response = self._send(request, target, stream=True)
        try:
            content_type = response.headers.get("content-type", "")
            if EVENT_STREAM_CONTENT_TYPE not in content_type.lower():
                self._raise_for_non_stream(response)
            state = _FrameStream()
            try:
                for event, data in parse_sse_events(response.iter_lines()):
                    yield state.feed(event, data)
                    if state.done:
                        return
            except httpx.ReadTimeout as exc:
                raise RemoteInvokeIdleTimeoutError(self._timeouts.idle_s) from exc
            except httpx.RequestError as exc:
                raise RemoteInvokeUnavailableError(
                    f"remote invoke stream broke: {type(exc).__name__}"
                ) from exc
            state.assert_complete()
        finally:
            response.close()

    def call(self, request: InvokeRequest, target: InvokeTarget) -> Any:
        response = self._send(request, target, stream=False)
        try:
            raw = response.read()
        except httpx.ReadTimeout as exc:
            raise RemoteInvokeIdleTimeoutError(self._timeouts.idle_s) from exc
        except httpx.RequestError as exc:
            raise RemoteInvokeUnavailableError(
                f"remote invoke read failed: {type(exc).__name__}"
            ) from exc
        finally:
            response.close()
        return _unwrap_envelope(raw, response.status_code)


# ---------------- langchain chat adapter (A2) ----------------

#: 服务侧 ``LLMParams`` 白名单（``extra="forbid"``，多一键即 422）。``messages`` 不在其中：
#: 它由消息序列化产出，不随调用方 kwargs 透传。
_WIRE_PARAM_KEYS = (
    "tools",
    "tool_choice",
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


def _normalize_stop(value: Any) -> list[str] | None:
    """``stop`` 折叠成服务侧要求的 ``list[str]``；空值与无法迭代的值按未提供处理。"""

    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value] or None
    return None


def _pick_wire_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """白名单过滤 + 去 ``None``：显式 ``None`` 视为「未提供」，不覆盖构造期默认。"""

    wire: dict[str, Any] = {}
    for key in _WIRE_PARAM_KEYS:
        value = params.get(key)
        if value is None:
            continue
        if key == "stop":
            stop = _normalize_stop(value)
            if stop is None:
                continue
            wire[key] = stop
            continue
        wire[key] = value
    return wire


def _serialize_message(message: BaseMessage) -> dict[str, Any]:
    """langchain 消息 → 服务侧 ``messages`` 字典。

    不借 ``convert_to_openai_messages``：它产出 OpenAI wire 形状，与服务侧入参契约不同。
    """

    if isinstance(message, HumanMessage):
        role = "user"
    elif isinstance(message, AIMessage):
        role = "assistant"
    elif isinstance(message, SystemMessage):
        role = "system"
    elif isinstance(message, ToolMessage):
        role = "tool"
    elif isinstance(message, ChatMessage):
        role = message.role
    else:
        raise TypeError(
            f"remote chat adapter cannot serialize {type(message).__name__}"
        )
    payload: dict[str, Any] = {"role": role, "content": message.content}
    if isinstance(message, AIMessage) and message.tool_calls:
        payload["tool_calls"] = [
            {
                "name": call.get("name"),
                "args": call.get("args") or {},
                "id": call.get("id"),
            }
            for call in message.tool_calls
        ]
    if isinstance(message, ToolMessage):
        payload["tool_call_id"] = message.tool_call_id
    return payload


def _serialize_messages(messages: Sequence[BaseMessage]) -> list[dict[str, Any]]:
    if not messages:
        raise ValueError("remote invoke requires at least one message")
    return [_serialize_message(message) for message in messages]


def messages_from_wire(messages: Sequence[Mapping[str, Any]]) -> list[BaseMessage]:
    """``_serialize_message`` 的逆：wire ``messages`` → langchain 消息（服务侧还原）。

    只还原 wire 契约的四个角色；其余角色（如 ``developer``）无法无损表达，一律
    ``ValueError``——静默降级会改变模型输入却不报错。
    """

    if not messages:
        raise ValueError("wire messages payload is empty")
    return [_message_from_wire(message) for message in messages]


def _message_from_wire(message: Mapping[str, Any]) -> BaseMessage:
    if not isinstance(message, Mapping):
        raise ValueError(
            f"wire message must be a mapping, got {type(message).__name__}"
        )
    role = message.get("role")
    if not isinstance(role, str) or not role:
        raise ValueError("wire message must carry a non-empty string role")
    content = message.get("content", "")
    if role in ("user", "human"):
        return HumanMessage(content=content)
    if role == "system":
        return SystemMessage(content=content)
    if role in ("assistant", "ai"):
        return AIMessage(content=content, tool_calls=_wire_tool_calls(message))
    if role == "tool":
        tool_call_id = message.get("tool_call_id")
        if not isinstance(tool_call_id, str) or not tool_call_id:
            raise ValueError("wire tool message must carry a tool_call_id")
        return ToolMessage(content=content, tool_call_id=tool_call_id)
    raise ValueError(f"unsupported wire message role: {role!r}")


def _wire_tool_calls(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = message.get("tool_calls")
    if raw is None:
        return []
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise ValueError("wire assistant tool_calls must be a list")
    calls: list[dict[str, Any]] = []
    for call in raw:
        if not isinstance(call, Mapping):
            raise ValueError("wire tool_call must be a mapping")
        name = call.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("wire tool_call must carry a non-empty name")
        calls.append(
            {
                "name": name,
                "args": call.get("args") or {},
                "id": call.get("id") or "",
                "type": "tool_call",
            }
        )
    return calls


def _chunk_message(message: Mapping[str, Any]) -> AIMessageChunk:
    try:
        return AIMessageChunk.model_validate(message)
    except (ValidationError, TypeError, ValueError) as exc:
        raise RemoteInvokeProtocolError(
            f"chunk frame is not an AIMessageChunk dump: {exc}"
        ) from exc


def _result_message(data: Any) -> AIMessage:
    if not isinstance(data, Mapping) or "content" not in data:
        raise RemoteInvokeProtocolError("result frame is not an AIMessage dump")
    try:
        return AIMessage.model_validate(data)
    except (ValidationError, TypeError, ValueError) as exc:
        raise RemoteInvokeProtocolError(
            f"result frame is not an AIMessage dump: {exc}"
        ) from exc


def _normalize_tool_choice(tool_choice: Any, tool_names: Sequence[str]) -> Any:
    """对齐 OpenAI 形状：工具名 → function dict，``any``/``True`` → ``required``，假值不发键。"""

    if tool_choice is None or tool_choice is False:
        return None
    if tool_choice is True:
        return "required"
    if isinstance(tool_choice, str):
        if tool_choice in tool_names:
            return {"type": "function", "function": {"name": tool_choice}}
        return "required" if tool_choice == "any" else tool_choice
    if isinstance(tool_choice, Mapping):
        return dict(tool_choice)
    raise ValueError(f"unsupported tool_choice: {tool_choice!r}")


def _chunk_from_frame(frame: InvokeFrame) -> ChatGenerationChunk | None:
    """``chunk`` 帧 → 生成块；``usage``/``done`` 在 langchain 侧无对应物，跳过。"""

    if isinstance(frame, InvokeChunkFrame):
        return ChatGenerationChunk(message=_chunk_message(frame.message))
    if isinstance(frame, InvokeResultFrame):
        # 流式 llm 的单结果只能以 chunk 帧抵达（帧契约），result 帧即契约违规
        raise RemoteInvokeProtocolError(
            "llm stream carried a result frame instead of chunk frames"
        )
    return None


class RemoteRedBearChatModel(BaseChatModel):
    """走模型服务 invoke 接缝的 langchain chat 模型（无状态、无凭据、不选路、不重试）。

    ``transport``/``target`` 接受实例或零参可调用（懒构造）：调用方掌握连接池与归因，
    本层只负责 langchain 形状 ↔ 帧契约的翻译。选路、换渠道与重试由包内编排（A3/A4）
    与宿主负责，故流式失败原样抛 :class:`RemoteInvokeFailedError`。
    """

    config_id: str
    default_params: dict[str, Any] = Field(default_factory=dict)

    def __init__(
        self,
        config_id: str | UUID,
        *,
        transport: Any | None = None,
        target: Any | None = None,
        default_params: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if transport is None and target is None:
            raise ValueError(
                "remote chat model needs an invoke transport and an attribution target"
            )
        super().__init__(
            config_id=str(UUID(str(config_id))),
            default_params=dict(default_params or {}),
            **kwargs,
        )
        # 非字段状态：bind()/model_copy() 不经过 __init__，取值处须容忍缺失
        object.__setattr__(self, "_transport", transport)
        object.__setattr__(self, "_target", target)

    @property
    def _llm_type(self) -> str:
        return "remote-redbear-chat"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"config_id": self.config_id}

    def _resolve(self, name: str) -> Any:
        value = getattr(self, name, None)
        return value() if callable(value) else value

    def _invoke_transport(self, expected: type) -> Any:
        transport = self._resolve("_transport")
        if transport is None:
            raise RemoteInvokeUnavailableError(
                "remote chat model has no invoke transport configured"
            )
        if not isinstance(transport, expected):
            raise RemoteInvokeUnavailableError(
                f"remote chat model needs a {expected.__name__}, "
                f"got {type(transport).__name__}"
            )
        return transport

    def _invoke_target(self) -> InvokeTarget:
        target = self._resolve("_target")
        if not isinstance(target, InvokeTarget):
            raise RemoteInvokeUnavailableError(
                "remote chat model has no invoke target configured"
            )
        return target

    def _build_params(
        self,
        messages: Sequence[BaseMessage],
        stop: list[str] | None,
        overrides: Mapping[str, Any],
    ) -> dict[str, Any]:
        merged: dict[str, Any] = dict(self.default_params)
        merged.update(
            {key: value for key, value in overrides.items() if value is not None}
        )
        if stop is not None:
            merged["stop"] = stop
        dropped = sorted(key for key in merged if key not in _WIRE_PARAM_KEYS)
        if dropped:
            logger.debug(
                "remote chat adapter dropped unsupported parameters: %s",
                ", ".join(dropped),
            )
        params = _pick_wire_params(merged)
        params["messages"] = _serialize_messages(messages)
        return params

    def _request(self, params: dict[str, Any], *, stream: bool) -> InvokeRequest:
        return InvokeRequest(
            config_id=UUID(self.config_id),
            type=ModelType.LLM,
            params=params,
            stream=stream,
        )

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> Any:
        formatted = [convert_to_openai_tool(tool) for tool in tools]
        tool_names: list[str] = []
        for entry in formatted:
            function = entry.get("function")
            if isinstance(function, Mapping) and function.get("name"):
                tool_names.append(function["name"])
        normalized = _normalize_tool_choice(tool_choice, tool_names)
        if normalized is not None:
            kwargs["tool_choice"] = normalized
        return self.bind(tools=formatted, **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        transport = self._invoke_transport(SyncInvokeTransport)
        params = self._build_params(messages, stop, kwargs)
        data = transport.call(self._request(params, stream=False), self._invoke_target())
        return ChatResult(generations=[ChatGeneration(message=_result_message(data))])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        transport = self._invoke_transport(AsyncInvokeTransport)
        params = self._build_params(messages, stop, kwargs)
        data = await transport.call(
            self._request(params, stream=False), self._invoke_target()
        )
        return ChatResult(generations=[ChatGeneration(message=_result_message(data))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        transport = self._invoke_transport(SyncInvokeTransport)
        params = self._build_params(messages, stop, kwargs)
        frames = transport.stream(self._request(params, stream=True), self._invoke_target())
        for frame in frames:
            chunk = _chunk_from_frame(frame)
            if chunk is not None:
                yield chunk

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        transport = self._invoke_transport(AsyncInvokeTransport)
        params = self._build_params(messages, stop, kwargs)
        frames = transport.stream(self._request(params, stream=True), self._invoke_target())
        async for frame in frames:
            chunk = _chunk_from_frame(frame)
            if chunk is not None:
                yield chunk


__all__ = [
    "EVENT_STREAM_CONTENT_TYPE",
    "HEADER_RESOURCE_ID",
    "HEADER_RESOURCE_TYPE",
    "HEADER_SOURCE",
    "HEADER_TENANT_ID",
    "HEADER_TRACE_ID",
    "INVOKE_PATH",
    "MAX_EVENT_BYTES",
    "AsyncInvokeTransport",
    "InvokeChunkFrame",
    "InvokeDoneFrame",
    "InvokeFrame",
    "InvokeRequest",
    "InvokeResultFrame",
    "InvokeTarget",
    "InvokeTimeouts",
    "InvokeUsageFrame",
    "RemoteRedBearChatModel",
    "SSEEventDecoder",
    "SyncInvokeTransport",
    "decode_frame",
    "envelope_failure",
    "messages_from_wire",
    "parse_sse_events",
    "parse_sse_events_async",
]
