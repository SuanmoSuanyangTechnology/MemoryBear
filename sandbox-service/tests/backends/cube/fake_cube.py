"""In-process stand-in for Cube, served through ``httpx.MockTransport``.

It speaks just enough of the control plane (CubeAPI) and the data plane (the code
interpreter reached through CubeProxy) for ``CubeBackend`` to run against it. Unit
tests use it directly; the shared backend contract runs on top of it too.

Fault injection: ``intercept`` queues a one-shot (or counted) responder for requests
whose method and path match, ahead of the normal handling.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from src.clock import Clock

API_HOST = "cube-api"
PROXY_HOST = "cube-proxy"
API_URL = f"http://{API_HOST}:3000"
PROXY_ADDR = f"{PROXY_HOST}:8080"

ENVD_PORT = 49983

# The code port is addressed as ``<port>-<id>.cube.app``; envd as ``<port>-<id>`` (no domain),
# because CubeProxy redirects GET /files when the domain is present.
_HOST_RE = re.compile(r"^(\d+)-([^.]+)(?:\.cube\.app)?$")
_PATH_RE = re.compile(r"^/sandbox/([^/]+)/(\d+)(/.*)$")
_RPC_RE = re.compile(r"^/filesystem\.Filesystem/(Stat|ListDir|MakeDir|Remove)$")

Responder = Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]]


def reply(status: int, body: Any = None, headers: dict[str, str] | None = None) -> Responder:
    def respond(_request: httpx.Request) -> httpx.Response:
        if body is None:
            return httpx.Response(status, headers=headers)
        return httpx.Response(status, json=body, headers=headers)

    return respond


def boom(exc: Exception) -> Responder:
    def respond(_request: httpx.Request) -> httpx.Response:
        raise exc

    return respond


def _json(status: int, payload: Any = None, headers: dict[str, str] | None = None):
    return httpx.Response(status, json=payload, headers=headers)


@dataclass
class _Rule:
    method: str
    pattern: re.Pattern[str]
    respond: Responder
    times: int


@dataclass
class _Sandbox:
    sandbox_id: str
    metadata: dict[str, str]
    started_at: float
    state: str = "running"
    contexts: dict[str, str] = field(default_factory=dict)
    files: dict[str, tuple[bytes, int]] = field(default_factory=dict)  # path -> (data, mtime_ms)
    dirs: dict[str, int] = field(default_factory=lambda: {"/workspace": 0})  # path -> mtime_ms


def _ancestors(path: str) -> list[str]:
    parts = [p for p in path.split("/") if p]
    return ["/" + "/".join(parts[:i]) for i in range(1, len(parts))]


class FakeCube:
    def __init__(self, clock: Clock, *, capacity: int | None = None) -> None:
        self._clock = clock
        self.capacity = capacity
        self.sandboxes: dict[str, _Sandbox] = {}
        self.requests: list[httpx.Request] = []
        self._rules: list[_Rule] = []
        self._seq = 0

    # -- test helpers -------------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def intercept(self, method: str, pattern: str, respond: Responder, *, times: int = 1) -> None:
        self._rules.append(_Rule(method.upper(), re.compile(pattern), respond, times))

    def add_foreign(self, metadata: dict[str, str] | None, state: str = "running") -> str:
        """A sandbox created by somebody else (no ``sbx.key`` metadata)."""

        self._seq += 1
        sandbox_id = f"sbx-{self._seq}"
        self.sandboxes[sandbox_id] = _Sandbox(
            sandbox_id, dict(metadata or {}), self._clock.time(), state
        )
        return sandbox_id

    def requests_to(self, method: str, pattern: str) -> list[httpx.Request]:
        regex = re.compile(pattern)
        return [
            r for r in self.requests if r.method == method.upper() and regex.search(r.url.path)
        ]

    # -- transport entry ------------------------------------------------------------

    def __call__(self, request: httpx.Request):
        self.requests.append(request)
        for rule in self._rules:
            if rule.times > 0 and rule.method == request.method and rule.pattern.search(
                request.url.path
            ):
                rule.times -= 1
                return rule.respond(request)
        if request.url.host == API_HOST:
            return self._control(request)
        if request.url.host == PROXY_HOST:
            return self._data(request)
        return httpx.Response(502)

    # -- control plane ----------------------------------------------------------------

    def _control(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path == "/health":
            return _json(200, {"status": "ok", "sandboxes": len(self.sandboxes)})
        if path == "/sandboxes" and method == "POST":
            return self._create(request)
        if path == "/v2/sandboxes" and method == "GET":
            limit = int(request.url.params.get("limit", "100"))
            return _json(200, [self._listed(s) for s in list(self.sandboxes.values())[:limit]])
        match = re.fullmatch(r"/sandboxes/([^/]+)(?:/(pause|connect))?", path)
        if match is None:
            return _json(404, {"message": "no such route"})
        sandbox = self.sandboxes.get(match.group(1))
        if sandbox is None:
            return _json(404, {"message": "sandbox not found"})
        action = match.group(2)
        if action is None and method == "GET":
            return _json(200, self._listed(sandbox))
        if action is None and method == "DELETE":
            del self.sandboxes[sandbox.sandbox_id]
            return httpx.Response(204)
        if action == "pause" and method == "POST":
            sandbox.state = "paused"
            return httpx.Response(204)
        if action == "connect" and method == "POST":
            sandbox.state = "running"
            return _json(200, {"sandboxID": sandbox.sandbox_id})
        return _json(405, {"message": "method not allowed"})

    def _create(self, request: httpx.Request) -> httpx.Response:
        if self.capacity is not None and len(self.sandboxes) >= self.capacity:
            return _json(409, {"message": "no capacity"})
        body = json.loads(request.content)
        self._seq += 1
        sandbox_id = f"sbx-{self._seq}"
        self.sandboxes[sandbox_id] = _Sandbox(
            sandbox_id, dict(body.get("metadata") or {}), self._clock.time()
        )
        return _json(201, {"sandboxID": sandbox_id, "templateID": body["templateID"]})

    @staticmethod
    def _listed(sandbox: _Sandbox) -> dict[str, Any]:
        return {
            "sandboxID": sandbox.sandbox_id,
            "state": sandbox.state,
            "metadata": sandbox.metadata,
            "startedAt": datetime.fromtimestamp(sandbox.started_at, UTC).isoformat(),
        }

    # -- data plane ---------------------------------------------------------------------

    def _data(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        host_match = _HOST_RE.match(request.headers.get("host", ""))
        path_match = _PATH_RE.match(path)
        if host_match is not None:
            port, sandbox_id = int(host_match.group(1)), host_match.group(2)
        elif path_match is not None:
            sandbox_id, port, path = (
                path_match.group(1),
                int(path_match.group(2)),
                path_match.group(3),
            )
        else:
            return _json(404, {"message": "unroutable"})

        sandbox = self.sandboxes.get(sandbox_id)
        if sandbox is None:
            return _json(404, {"message": "sandbox not found"})
        if sandbox.state != "running":
            return _json(503, {"message": "sandbox is paused"}, {"Retry-After": "2"})
        if port == ENVD_PORT:
            return self._envd(sandbox, request, path)

        method = request.method
        if path == "/contexts" and method == "POST":
            language = json.loads(request.content).get("language")
            if language not in (None, "python", "python3", "py"):
                return _json(400, {"detail": "only python language is supported"})
            self._seq += 1
            context_id = f"ctx-{self._seq}"
            sandbox.contexts[context_id] = "python"
            return _json(200, {"id": context_id, "language": "python", "cwd": "/workspace"})
        if path == "/execute" and method == "POST":
            return self._execute(sandbox, json.loads(request.content))
        match = re.fullmatch(r"/contexts/([^/]+)(/restart)?", path)
        if match is not None:
            context_id = match.group(1)
            if context_id not in sandbox.contexts:
                return _json(404, {"detail": f"context {context_id} not found"})
            if match.group(2) is None and method == "DELETE":
                del sandbox.contexts[context_id]
            return _json(200, None)
        return _json(404, {"message": "no such route"})

    # -- envd (files) -----------------------------------------------------------------

    @staticmethod
    def _entry(sandbox: _Sandbox, path: str) -> dict[str, Any] | None:
        if path in sandbox.files:
            data, mtime = sandbox.files[path]
            kind, size = "FILE_TYPE_FILE", len(data)
        elif path in sandbox.dirs:
            mtime = sandbox.dirs[path]
            kind, size = "FILE_TYPE_DIRECTORY", 4096
        else:
            return None
        return {
            "name": path.rsplit("/", 1)[-1],
            "type": kind,
            "path": path,
            "size": str(size),  # int64 travels as a string
            "modifiedTime": datetime.fromtimestamp(mtime / 1000, UTC).isoformat(),
        }

    def _envd(self, sandbox: _Sandbox, request: httpx.Request, path: str) -> httpx.Response:
        if path == "/files":
            return self._envd_file(sandbox, request)
        match = _RPC_RE.match(path)
        if match is None or request.method != "POST":
            return _json(404, {"code": "unimplemented", "message": "no such route"})
        name = match.group(1)
        body = json.loads(request.content)
        target = body["path"]
        now = self._clock.now_ms()
        missing = _json(
            404,
            {
                "code": "not_found",
                "message": f"file not found: lstat {target}: no such file or directory",
            },
        )
        if name == "Stat":
            entry = self._entry(sandbox, target)
            return missing if entry is None else _json(200, {"entry": entry})
        if name == "ListDir":
            if target not in sandbox.dirs:
                return missing
            depth = int(body.get("depth") or 1)
            prefix = target.rstrip("/") + "/"
            entries = [
                self._entry(sandbox, c)
                for c in sorted(set(sandbox.files) | set(sandbox.dirs))
                if c.startswith(prefix) and c[len(prefix) :].count("/") < depth
            ]
            return _json(200, {"entries": entries} if entries else {})  # empty dir: ``{}``
        if name == "MakeDir":
            if target in sandbox.files:
                return _json(409, {"code": "already_exists", "message": "a file is in the way"})
            for p in [*_ancestors(target), target]:
                sandbox.dirs.setdefault(p, now)
            return _json(200, {"entry": self._entry(sandbox, target)})
        # Remove
        if target not in sandbox.files and target not in sandbox.dirs:
            return missing
        prefix = target.rstrip("/") + "/"
        for table in (sandbox.files, sandbox.dirs):
            for c in [c for c in table if c == target or c.startswith(prefix)]:
                del table[c]
        return _json(200, {})

    def _envd_file(self, sandbox: _Sandbox, request: httpx.Request) -> httpx.Response:
        target = request.url.params.get("path", "")
        if target in sandbox.dirs:
            return _json(400, {"code": 400, "message": f"path '{target}' is a directory"})
        if request.method == "GET":
            if target not in sandbox.files:
                return _json(404, {"code": 404, "message": f"path '{target}' does not exist"})
            return httpx.Response(
                200,
                content=sandbox.files[target][0],
                headers={"content-type": "application/octet-stream"},
            )
        if request.method == "POST":
            now = self._clock.now_ms()
            for p in _ancestors(target):
                sandbox.dirs.setdefault(p, now)
            sandbox.files[target] = (request.content, now)
            name = target.rsplit("/", 1)[-1]
            return _json(200, [{"name": name, "path": target, "type": "file"}])
        return _json(405, {"message": "method not allowed"})

    @staticmethod
    def _execute(sandbox: _Sandbox, body: dict[str, Any]) -> httpx.Response:
        context_id = body.get("context_id")
        if context_id not in sandbox.contexts:
            return _json(404, {"detail": f"context {context_id} not found"})
        code = body["code"]
        events: list[dict[str, Any]] = [{"type": "number_of_executions", "execution_count": 1}]
        if code == "RAISE":
            events.append(
                {
                    "type": "error",
                    "name": "ValueError",
                    "value": "boom",
                    "traceback": "\x1b[31mTraceback\x1b[0m\n",
                }
            )
        elif code == "DIE":
            events.append(
                {"type": "error", "name": "UnexpectedEndOfExecution", "value": "x", "traceback": ""}
            )
        elif code.startswith("print("):
            events.append({"type": "stdout", "text": "out\n"})
            events.append({"type": "stderr", "text": "warn\n"})
        elif code == "1+1":
            events.append({"type": "result", "is_main_result": True, "text": "2"})
        events.append({"type": "end_of_execution"})
        payload = "".join(json.dumps(e) + "\n" for e in events)
        return httpx.Response(
            200, content=payload.encode(), headers={"content-type": "application/x-ndjson"}
        )
