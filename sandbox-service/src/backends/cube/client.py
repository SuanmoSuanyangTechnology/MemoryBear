"""HTTP access to Cube: the control plane (CubeAPI) and the data plane (CubeProxy).

Only transport lives here. Deciding what an error status means for the scheduler is
the backend's job. Error text never carries URLs or addresses, so internal topology
does not leak into API responses.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

import httpx

from .config import CubeConfig

BODY_SNIPPET_CHARS = 300


class HttpStatusError(Exception):
    """The server answered with a 4xx / 5xx status."""

    def __init__(self, status: int, body: str, retry_after_s: float | None = None) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
        self.body = body
        self.retry_after_s = retry_after_s


class TransportFailure(Exception):
    """No usable answer: connect error, timeout, broken stream or malformed payload."""


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after")
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


def _segment(value: str) -> str:
    return quote(value, safe="")


class CubeClient:
    def __init__(
        self, cfg: CubeConfig, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._cfg = cfg
        # trust_env=False: a proxy from the environment must never sit in front of Cube.
        self._http = httpx.AsyncClient(
            trust_env=False,
            transport=transport,
            timeout=httpx.Timeout(cfg.http_timeout_s, connect=cfg.connect_timeout_s),
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- control plane -----------------------------------------------------------

    async def control(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        url = self._cfg.api_url.rstrip("/") + path
        headers = {"Authorization": f"Bearer {self._cfg.api_key}"} if self._cfg.api_key else {}
        kwargs: dict[str, Any] = {}
        if timeout is not None:
            kwargs["timeout"] = httpx.Timeout(timeout, connect=self._cfg.connect_timeout_s)
        try:
            resp = await self._http.request(
                method, url, json=json_body, params=params, headers=headers, **kwargs
            )
        except httpx.HTTPError as exc:
            raise TransportFailure(f"control plane unreachable ({type(exc).__name__})") from exc
        return self._read(resp)

    @staticmethod
    def _read(resp: httpx.Response) -> Any:
        if resp.status_code >= 400:
            raise HttpStatusError(
                resp.status_code, resp.text[:BODY_SNIPPET_CHARS], _retry_after(resp)
            )
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError as exc:
            raise TransportFailure("control plane returned invalid JSON") from exc

    @staticmethod
    def segment(value: str) -> str:
        return _segment(value)

    @staticmethod
    def sandbox_path(sandbox_id: str, suffix: str = "") -> str:
        return f"/sandboxes/{_segment(sandbox_id)}{suffix}"

    # -- data plane --------------------------------------------------------------

    def _data_target(self, sandbox_id: str, path: str) -> tuple[str, dict[str, str]]:
        cfg = self._cfg
        base = f"{cfg.proxy_scheme}://{cfg.proxy_addr}"
        if cfg.route_mode == "path":
            return f"{base}/sandbox/{_segment(sandbox_id)}/{cfg.code_port}{path}", {}
        return base + path, {"Host": f"{cfg.code_port}-{sandbox_id}.{cfg.domain}"}

    async def data_json(
        self, method: str, sandbox_id: str, path: str, *, json_body: Any = None
    ) -> Any:
        url, headers = self._data_target(sandbox_id, path)
        try:
            resp = await self._http.request(method, url, json=json_body, headers=headers)
        except httpx.HTTPError as exc:
            raise TransportFailure(f"data plane unreachable ({type(exc).__name__})") from exc
        return self._read(resp)

    async def execute(
        self, sandbox_id: str, body: dict[str, Any], *, read_timeout: float
    ) -> list[dict[str, Any]]:
        """POST ``/execute`` and return the newline-delimited JSON events."""

        url, headers = self._data_target(sandbox_id, "/execute")
        timeout = httpx.Timeout(read_timeout, connect=self._cfg.connect_timeout_s)
        events: list[dict[str, Any]] = []
        try:
            async with self._http.stream(
                "POST", url, json=body, headers=headers, timeout=timeout
            ) as resp:
                if resp.status_code >= 400:
                    text = (await resp.aread()).decode("utf-8", "replace")
                    raise HttpStatusError(
                        resp.status_code, text[:BODY_SNIPPET_CHARS], _retry_after(resp)
                    )
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError as exc:
                        raise TransportFailure("execute stream carried a malformed line") from exc
                    if isinstance(event, dict):
                        events.append(event)
        except httpx.HTTPError as exc:
            raise TransportFailure(f"execute stream broken ({type(exc).__name__})") from exc
        return events

    # -- envd (files) -------------------------------------------------------------

    def _envd_target(self, sandbox_id: str, path: str) -> tuple[str, dict[str, str]]:
        cfg = self._cfg
        base = f"{cfg.proxy_scheme}://{cfg.proxy_addr}"
        if cfg.route_mode == "path":
            return f"{base}/sandbox/{_segment(sandbox_id)}/{cfg.envd_port}{path}", {}
        host = f"{cfg.envd_port}-{sandbox_id}"
        if cfg.envd_domain:
            host += f".{cfg.envd_domain}"
        return base + path, {"Host": host}

    def _file_path(self, file_path: str) -> str:
        return f"/files?path={quote(file_path, safe='/')}&username={quote(self._cfg.envd_user)}"

    def _file_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(self._cfg.file_timeout_s, connect=self._cfg.connect_timeout_s)

    async def envd_rpc(self, sandbox_id: str, name: str, body: dict[str, Any]) -> Any:
        """One unary Connect call (JSON) of ``filesystem.Filesystem/<name>``."""

        url, headers = self._envd_target(sandbox_id, f"/filesystem.Filesystem/{name}")
        headers.update({"Content-Type": "application/json", "Connect-Protocol-Version": "1"})
        try:
            resp = await self._http.request(
                "POST", url, json=body, headers=headers, timeout=self._file_timeout()
            )
        except httpx.HTTPError as exc:
            raise TransportFailure(f"envd unreachable ({type(exc).__name__})") from exc
        return self._read(resp)

    async def envd_read(self, sandbox_id: str, file_path: str) -> AsyncIterator[bytes]:
        """Stream a file; a 4xx / 5xx status raises before the first chunk."""

        url, headers = self._envd_target(sandbox_id, self._file_path(file_path))
        try:
            async with self._http.stream(
                "GET", url, headers=headers, timeout=self._file_timeout()
            ) as resp:
                if resp.status_code >= 400:
                    text = (await resp.aread()).decode("utf-8", "replace")
                    raise HttpStatusError(
                        resp.status_code, text[:BODY_SNIPPET_CHARS], _retry_after(resp)
                    )
                if resp.status_code >= 300:
                    # CubeProxy answers a blocked Host with a redirect; never follow it.
                    raise HttpStatusError(resp.status_code, "unexpected redirect")
                async for chunk in resp.aiter_bytes():
                    yield chunk
        except httpx.HTTPError as exc:
            raise TransportFailure(f"file stream broken ({type(exc).__name__})") from exc

    async def envd_write(
        self, sandbox_id: str, file_path: str, chunks: AsyncIterator[bytes]
    ) -> int:
        """Stream ``chunks`` into a file (parents are created by envd); returns bytes sent."""

        url, headers = self._envd_target(sandbox_id, self._file_path(file_path))
        headers["Content-Type"] = "application/octet-stream"
        sent = 0

        async def counted() -> AsyncIterator[bytes]:
            nonlocal sent
            async for chunk in chunks:
                sent += len(chunk)
                yield chunk

        try:
            resp = await self._http.post(
                url, headers=headers, content=counted(), timeout=self._file_timeout()
            )
        except httpx.HTTPError as exc:
            raise TransportFailure(f"file upload broken ({type(exc).__name__})") from exc
        if 300 <= resp.status_code < 400:
            raise HttpStatusError(resp.status_code, "unexpected redirect")
        self._read(resp)
        return sent
