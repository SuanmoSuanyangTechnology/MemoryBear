"""DashScope filetrans protocol. No polling, retries, storage, or media downloads."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
import time
import zlib
from decimal import Decimal
from urllib.parse import quote, urlsplit

import httpx
from pydantic import ValidationError

from redbear_model.contracts import ModelProvider, ModelType, ResolvedModelConfig
from redbear_model.errors import (
    InvalidProviderResponseError,
    MediaCallTimeoutError,
    MediaOutputLimitError,
    MediaProviderError,
    ModelSubmissionUncertainError,
    ModelTaskFailedError,
    ModelTaskNotReadyError,
    UnsupportedModelProviderError,
    UnsupportedMultimodalModelError,
)
from redbear_model.media_contracts import (
    AudioTask,
    AudioTaskRef,
    AudioTaskStatus,
    AudioTranscriptionRequest,
    AudioTranscriptionResult,
    MediaCallOptions,
    MediaUsage,
    TranscriptSentence,
    TranscriptTrack,
    TranscriptWord,
    validate_media_url,
)
from redbear_model.providers._sync_deadline import run_with_timeout
from redbear_model.providers.dashscope import resolve_dashscope_native_base_address
from redbear_model.runtime.client_pool import ModelClientPool

_DEFAULT_BASE = "https://dashscope.aliyuncs.com/api/v1"
_MAX_REDIRECTS = 3
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")


def _safe_identifier(value: object) -> str | None:
    return value if isinstance(value, str) and _IDENTIFIER.fullmatch(value) else None


def resolve_dashscope_asr_base_address(value: str | None) -> str:
    """Validate and normalize a DashScope ASR API root without network I/O."""
    value = value or _DEFAULT_BASE
    validate_media_url(value)
    parsed = urlsplit(value)
    if parsed.query or parsed.path.rstrip("/") not in {
        "/api/v1",
        "/compatible-mode/v1",
        "/compatible-api/v1",
    }:
        raise ValueError("A DashScope API root is required")
    return resolve_dashscope_native_base_address(value.rstrip("/"))


def _object(value: object) -> dict:
    if not isinstance(value, dict):
        raise InvalidProviderResponseError("asr", "expected an object")
    return value


def _list(value: object) -> list:
    if not isinstance(value, list):
        raise InvalidProviderResponseError("asr", "expected an array")
    return value


def _usage(value: object) -> MediaUsage:
    if value is None:
        return MediaUsage()
    raw = _object(value)
    seconds = raw.get("seconds")
    if seconds is None:
        return MediaUsage()
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise InvalidProviderResponseError("asr", "invalid duration")
    duration = Decimal(str(seconds)) * 1000
    if (
        not duration.is_finite()
        or duration < 0
        or duration != duration.to_integral_value()
    ):
        raise InvalidProviderResponseError("asr", "invalid duration precision")
    return MediaUsage(audio_duration_ms=int(duration))


def _result_request(url: str, timeout: dict[str, float]) -> httpx.Request:
    """Pin the validated public address; retain Host and TLS SNI for signed URLs."""
    try:
        validate_media_url(url)
        original = httpx.URL(url)
        addresses = socket.getaddrinfo(
            original.host,
            original.port or (443 if original.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
        ips = {ipaddress.ip_address(item[4][0]) for item in addresses}
        if not ips or any(not ip.is_global for ip in ips):
            raise ValueError("Non-public address")
        address = str(min(ips, key=str))
    except (ValueError, OSError):
        raise MediaProviderError("asr.download") from None
    # A new Request and auth=None avoid all client-level headers, cookies and auth.
    return httpx.Request(
        "GET",
        original.copy_with(host=address),
        headers={
            "Host": original.netloc.decode("ascii"),
            "Connection": "close",
            "Accept-Encoding": "identity",
        },
        extensions={"timeout": timeout, "sni_hostname": original.host},
    )


class _ResponseBody:
    """Bound decoded bytes without buffering network reads or gzip expansion."""

    def __init__(self, encoding: str, limit: int, operation: str):
        self.limit = limit
        self.operation = operation
        self.data = bytearray()
        encoding = encoding.strip().lower()
        if encoding not in {"", "identity", "gzip", "deflate"}:
            raise InvalidProviderResponseError(
                operation, "unsupported content encoding"
            )
        self.decoder = (
            zlib.decompressobj(
                16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS
            )
            if encoding in {"gzip", "deflate"}
            else None
        )

    def feed(self, data: bytes) -> None:
        try:
            while data:
                if self.decoder is None:
                    decoded, data = data, b""
                else:
                    decoded = self.decoder.decompress(
                        data, self.limit - len(self.data) + 1
                    )
                    data = self.decoder.unconsumed_tail
                    if self.decoder.unused_data:
                        raise InvalidProviderResponseError(
                            self.operation, "trailing compressed data"
                        )
                if len(self.data) + len(decoded) > self.limit:
                    raise MediaOutputLimitError(self.operation)
                self.data.extend(decoded)
        except zlib.error:
            raise InvalidProviderResponseError(
                self.operation, "invalid compressed response"
            ) from None

    def finish(self) -> bytes:
        if self.decoder is not None and not self.decoder.eof:
            raise InvalidProviderResponseError(
                self.operation, "truncated compressed response"
            )
        return bytes(self.data)


class DashScopeASRAdapter:
    def __init__(
        self,
        config: ResolvedModelConfig,
        *,
        client_pool: ModelClientPool,
        options: MediaCallOptions,
    ):
        if config.provider is not ModelProvider.DASHSCOPE:
            raise UnsupportedModelProviderError(config.provider.value)
        if config.model_type is not ModelType.ASR:
            raise UnsupportedMultimodalModelError("audio transcription")
        self._base = resolve_dashscope_asr_base_address(config.base_url)
        self._config = config
        self._pool = client_pool
        self._options = options

    def _deadline(self) -> float:
        return time.monotonic() + self._options.call_timeout_ms / 1000

    def _timeout(self, deadline: float) -> dict[str, float]:
        remaining = self._remaining(deadline)
        clients = self._pool.get_http_clients()
        limits = clients.timeout.as_dict() if clients.timeout is not None else {}
        return {
            key: min(
                remaining,
                value if value is not None else self._config.runtime.timeout_s,
            )
            for key, value in (
                (key, limits.get(key)) for key in ("connect", "read", "write", "pool")
            )
        }

    @staticmethod
    def _remaining(deadline: float, operation: str = "asr") -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MediaCallTimeoutError(operation)
        return remaining

    def _request(
        self, method: str, path: str, deadline: float, payload=None
    ) -> httpx.Request:
        headers = {
            "Authorization": f"Bearer {self._config.api_key.get_secret_value()}",
            "Accept-Encoding": "identity",
        }
        if method == "POST":
            headers["X-DashScope-Async"] = "enable"
        return httpx.Request(
            method,
            f"{self._base}{path}",
            json=payload,
            headers=headers,
            extensions={"timeout": self._timeout(deadline)},
        )

    def _decode(self, body: bytes, status_code: int, operation: str) -> dict:
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            if not 200 <= status_code < 300:
                raise MediaProviderError(operation, status_code=status_code) from None
            raise InvalidProviderResponseError(operation, "invalid JSON") from None
        payload = _object(payload)
        if not 200 <= status_code < 300 or payload.get("code"):
            raise MediaProviderError(
                operation,
                status_code=status_code,
                provider_code=_safe_identifier(payload.get("code")),
                provider_request_id=_safe_identifier(payload.get("request_id")),
            )
        return payload

    def _read(self, response: httpx.Response, deadline: float, operation: str) -> dict:
        loaded = response.is_stream_consumed
        body = _ResponseBody(
            "" if loaded else response.headers.get("content-encoding", ""),
            self._options.max_result_bytes,
            operation,
        )
        chunks = (response.content,) if loaded else response.iter_raw()
        for chunk in chunks:
            self._timeout(deadline)
            body.feed(chunk)
        self._timeout(deadline)
        result = self._decode(body.finish(), response.status_code, operation)
        self._timeout(deadline)
        return result

    async def _aread(
        self, response: httpx.Response, deadline: float, operation: str
    ) -> dict:
        loaded = response.is_stream_consumed
        body = _ResponseBody(
            "" if loaded else response.headers.get("content-encoding", ""),
            self._options.max_result_bytes,
            operation,
        )
        if loaded:
            self._timeout(deadline)
            await asyncio.to_thread(body.feed, response.content)
        else:
            async for chunk in response.aiter_raw():
                self._timeout(deadline)
                await asyncio.to_thread(body.feed, chunk)
        self._timeout(deadline)
        decoded = await asyncio.to_thread(body.finish)
        result = await asyncio.to_thread(
            self._decode, decoded, response.status_code, operation
        )
        self._timeout(deadline)
        return result

    @staticmethod
    def _network_error(operation: str, exc: httpx.HTTPError) -> MediaProviderError:
        if operation == "asr.submit":
            return ModelSubmissionUncertainError(operation)
        if isinstance(exc, httpx.TimeoutException):
            return MediaCallTimeoutError(operation)
        return MediaProviderError(operation)

    def _send(self, request: httpx.Request, deadline: float, operation: str) -> dict:
        active_response: list[httpx.Response] = []

        def send_and_read() -> dict:
            response = self._pool.get_http_clients().sync.send(
                request,
                stream=True,
                auth=None,
                follow_redirects=False,
            )
            active_response.append(response)
            try:
                return self._read(response, deadline, operation)
            finally:
                response.close()
                active_response.clear()

        def close_response() -> None:
            if active_response:
                active_response[0].close()

        try:
            return run_with_timeout(
                send_and_read,
                self._remaining(deadline, operation),
                lambda: MediaCallTimeoutError(operation),
                close_response,
            )
        except MediaCallTimeoutError:
            if operation == "asr.submit":
                raise ModelSubmissionUncertainError(operation) from None
            raise
        except httpx.HTTPError as exc:
            raise self._network_error(operation, exc) from None

    async def _asend(
        self, request: httpx.Request, deadline: float, operation: str
    ) -> dict:
        try:
            response = await self._pool.get_http_clients().async_client.send(
                request,
                stream=True,
                auth=None,
                follow_redirects=False,
            )
            try:
                return await self._aread(response, deadline, operation)
            finally:
                await response.aclose()
        except MediaCallTimeoutError:
            if operation == "asr.submit":
                raise ModelSubmissionUncertainError(operation) from None
            raise
        except httpx.HTTPError as exc:
            raise self._network_error(operation, exc) from None

    def _payload(self, request: AudioTranscriptionRequest) -> dict:
        parameters = {
            "channel_id": list(request.channel_ids),
            "enable_itn": request.enable_itn,
            "enable_words": request.enable_words,
        }
        if request.language is not None:
            parameters["language"] = request.language
        return {
            "model": self._config.model_name,
            "input": {"file_url": request.file_url},
            "parameters": parameters,
        }

    def _ref(self, task_id: str) -> AudioTaskRef:
        return AudioTaskRef(
            task_id=task_id,
            tenant_id=self._config.tenant_id,
            model_config_id=self._config.model_config_id,
            channel_id=self._config.channel_id,
            key_id=self._config.key_id,
            model_name=self._config.model_name,
            api_base=self._base,
        )

    def _check_ref(self, ref: AudioTaskRef) -> None:
        if ref != self._ref(ref.task_id):
            raise ValueError(
                "ASR task context does not match the resolved configuration"
            )

    def _task(
        self, payload: dict, expected_id: str | None = None
    ) -> tuple[AudioTask, str | None]:
        try:
            output = _object(payload.get("output"))
            task_id = output.get("task_id")
            status = output.get("task_status")
            if not isinstance(task_id, str) or not isinstance(status, str):
                raise TypeError("Missing task identity")
            if expected_id is not None and task_id != expected_id:
                raise ValueError("Task identity mismatch")
            normalized = status.lower()
            if normalized not in AudioTaskStatus:
                normalized = AudioTaskStatus.UNKNOWN
            result = output.get("result")
            url = (
                _object(result).get("transcription_url") if result is not None else None
            )
            if url is not None and not isinstance(url, str):
                raise ValueError("Invalid result reference")
            return AudioTask(
                ref=self._ref(task_id),
                status=normalized,
                provider_status=_safe_identifier(status) or "UNKNOWN",
                provider_request_id=_safe_identifier(payload.get("request_id")),
                provider_code=_safe_identifier(output.get("code")),
                error_summary="ASR task failed or is unknown"
                if normalized
                in {
                    AudioTaskStatus.FAILED,
                    AudioTaskStatus.UNKNOWN,
                }
                else None,
                usage=_usage(payload.get("usage")),
                result_available=normalized == AudioTaskStatus.SUCCEEDED and bool(url),
            ), url
        except (ValueError, TypeError):
            raise InvalidProviderResponseError(
                "asr.task", "invalid task response"
            ) from None

    def submit(self, request: AudioTranscriptionRequest) -> AudioTask:
        deadline = self._deadline()
        payload = self._send(
            self._request(
                "POST",
                "/services/audio/asr/transcription",
                deadline,
                self._payload(request),
            ),
            deadline,
            "asr.submit",
        )
        return self._task(payload)[0]

    async def asubmit(self, request: AudioTranscriptionRequest) -> AudioTask:
        deadline = self._deadline()
        try:
            async with asyncio.timeout(self._options.call_timeout_ms / 1000):
                payload = await self._asend(
                    self._request(
                        "POST",
                        "/services/audio/asr/transcription",
                        deadline,
                        self._payload(request),
                    ),
                    deadline,
                    "asr.submit",
                )
                return self._task(payload)[0]
        except TimeoutError:
            raise ModelSubmissionUncertainError("asr.submit") from None

    def _query(
        self, ref: AudioTaskRef, deadline: float
    ) -> tuple[AudioTask, str | None]:
        self._check_ref(ref)
        payload = self._send(
            self._request("GET", f"/tasks/{quote(ref.task_id, safe='')}", deadline),
            deadline,
            "asr.get_task",
        )
        return self._task(payload, ref.task_id)

    async def _aquery(
        self, ref: AudioTaskRef, deadline: float
    ) -> tuple[AudioTask, str | None]:
        self._check_ref(ref)
        payload = await self._asend(
            self._request("GET", f"/tasks/{quote(ref.task_id, safe='')}", deadline),
            deadline,
            "asr.get_task",
        )
        return self._task(payload, ref.task_id)

    def get_task(self, ref: AudioTaskRef) -> AudioTask:
        return self._query(ref, self._deadline())[0]

    async def aget_task(self, ref: AudioTaskRef) -> AudioTask:
        try:
            async with asyncio.timeout(self._options.call_timeout_ms / 1000):
                return (await self._aquery(ref, self._deadline()))[0]
        except TimeoutError:
            raise MediaCallTimeoutError("asr.get_task", task_ref=ref) from None

    @staticmethod
    def _ready(task: AudioTask, url: str | None) -> str:
        context = {
            "task_ref": task.ref,
            "usage": task.usage,
            "provider_request_id": task.provider_request_id,
            "provider_code": task.provider_code,
        }
        if task.status in {AudioTaskStatus.PENDING, AudioTaskStatus.RUNNING}:
            raise ModelTaskNotReadyError("asr.fetch_result", **context)
        if task.status != AudioTaskStatus.SUCCEEDED:
            raise ModelTaskFailedError("asr.fetch_result", **context)
        if not url:
            raise InvalidProviderResponseError(
                "asr.fetch_result", "missing result reference"
            )
        return url

    def _download(self, url: str, deadline: float) -> dict:
        for hop in range(_MAX_REDIRECTS + 1):
            current_url = url
            active_response: list[httpx.Response] = []

            def download_hop(
                current_url: str = current_url,
                hop: int = hop,
                active_response: list[httpx.Response] = active_response,
            ) -> tuple[dict | None, str | None]:
                request = _result_request(current_url, self._timeout(deadline))
                request.extensions["timeout"] = self._timeout(deadline)
                response = self._pool.get_http_clients().sync.send(
                    request, stream=True, auth=None, follow_redirects=False
                )
                active_response.append(response)
                try:
                    if response.is_redirect:
                        if hop == _MAX_REDIRECTS or not response.headers.get(
                            "location"
                        ):
                            raise MediaProviderError(
                                "asr.download", status_code=response.status_code
                            )
                        return None, str(
                            httpx.URL(current_url).join(response.headers["location"])
                        )
                    return self._read(response, deadline, "asr.download"), None
                finally:
                    response.close()
                    active_response.clear()

            def close_response(
                active_response: list[httpx.Response] = active_response,
            ) -> None:
                if active_response:
                    active_response[0].close()

            try:
                payload, redirect_url = run_with_timeout(
                    download_hop,
                    self._remaining(deadline, "asr.download"),
                    lambda: MediaCallTimeoutError("asr.download"),
                    close_response,
                )
                if redirect_url is not None:
                    url = redirect_url
                    continue
                if payload is None:
                    raise InvalidProviderResponseError(
                        "asr.download", "missing download payload"
                    )
                return payload
            except httpx.HTTPError as exc:
                raise self._network_error("asr.download", exc) from None
        raise MediaProviderError("asr.download")

    async def _adownload(self, url: str, deadline: float) -> dict:
        for hop in range(_MAX_REDIRECTS + 1):
            request = await asyncio.to_thread(
                _result_request, url, self._timeout(deadline)
            )
            request.extensions["timeout"] = self._timeout(deadline)
            try:
                response = await self._pool.get_http_clients().async_client.send(
                    request, stream=True, auth=None, follow_redirects=False
                )
                try:
                    if response.is_redirect:
                        if hop == _MAX_REDIRECTS or not response.headers.get(
                            "location"
                        ):
                            raise MediaProviderError(
                                "asr.download", status_code=response.status_code
                            )
                        url = str(httpx.URL(url).join(response.headers["location"]))
                        continue
                    return await self._aread(response, deadline, "asr.download")
                finally:
                    await response.aclose()
            except httpx.HTTPError as exc:
                raise self._network_error("asr.download", exc) from None
        raise MediaProviderError("asr.download")

    def _normalize(self, payload: dict, task: AudioTask) -> AudioTranscriptionResult:
        try:
            raw_tracks = payload["transcripts"]
            if not isinstance(raw_tracks, list):
                raise TypeError("Invalid transcripts")
            tracks = []
            for raw_track in raw_tracks:
                raw_track = _object(raw_track)
                sentences = []
                for raw_sentence in _list(raw_track.get("sentences", [])):
                    raw_sentence = _object(raw_sentence)
                    words = tuple(
                        TranscriptWord(
                            text=word["text"],
                            punctuation=word.get("punctuation"),
                            start_ms=word.get("begin_time"),
                            end_ms=word.get("end_time"),
                        )
                        for word in _list(raw_sentence.get("words", []))
                    )
                    sentences.append(
                        TranscriptSentence(
                            text=raw_sentence["text"],
                            sentence_id=raw_sentence.get("sentence_id"),
                            language=raw_sentence.get("language"),
                            start_ms=raw_sentence.get("begin_time"),
                            end_ms=raw_sentence.get("end_time"),
                            words=words,
                        )
                    )
                starts = [
                    sentence.start_ms
                    for sentence in sentences
                    if sentence.start_ms is not None
                ]
                if starts != sorted(starts):
                    raise ValueError("Sentence timestamps are not ordered")
                tracks.append(
                    TranscriptTrack(
                        channel_id=raw_track["channel_id"],
                        text=raw_track["text"],
                        sentences=tuple(sentences),
                    )
                )
            if len({track.channel_id for track in tracks}) != len(tracks):
                raise ValueError("Repeated channel")
            tracks.sort(key=lambda track: track.channel_id)
            text = "\n".join(
                track.text.strip() for track in tracks if track.text.strip()
            )
            if len(text) > self._options.max_text_chars:
                raise MediaOutputLimitError(
                    "asr.fetch_result", task_ref=task.ref, usage=task.usage
                )
            return AudioTranscriptionResult(
                ref=task.ref,
                tracks=tuple(tracks),
                text=text,
                usage=task.usage or MediaUsage(),
                provider_request_id=task.provider_request_id,
            )
        except (KeyError, TypeError, ValueError, AttributeError, ValidationError):
            raise InvalidProviderResponseError(
                "asr.fetch_result", "invalid transcript structure"
            ) from None

    def fetch_result(self, ref: AudioTaskRef) -> AudioTranscriptionResult:
        deadline = self._deadline()
        task, url = self._query(ref, deadline)
        try:
            result = self._normalize(
                self._download(self._ready(task, url), deadline), task
            )
            self._timeout(deadline)
            return result
        except MediaProviderError as exc:
            exc.task_ref = ref
            exc.usage = task.usage
            raise

    async def afetch_result(self, ref: AudioTaskRef) -> AudioTranscriptionResult:
        task = None
        try:
            async with asyncio.timeout(self._options.call_timeout_ms / 1000):
                deadline = self._deadline()
                task, url = await self._aquery(ref, deadline)
                payload = await self._adownload(self._ready(task, url), deadline)
                return await asyncio.to_thread(self._normalize, payload, task)
        except TimeoutError:
            raise MediaCallTimeoutError(
                "asr.fetch_result", task_ref=ref, usage=task.usage if task else None
            ) from None
        except MediaProviderError as exc:
            exc.task_ref = ref
            if task is not None:
                exc.usage = task.usage
            raise
