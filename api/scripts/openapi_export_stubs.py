"""Explicit, fail-closed runtime boundaries for schema export only."""

from contextlib import contextmanager
from importlib.abc import MetaPathFinder
import sys
from types import ModuleType
from unittest.mock import patch


FORBIDDEN_IMPORTS = frozenset({
    "torch", "onnxruntime", "cv2", "xgboost", "modelscope",
    "transformers", "sentence_transformers", "graspologic",
    "sklearn", "scipy", "matplotlib",
})


class ExportIsolationError(RuntimeError):
    pass


class RuntimeGuard:
    def __init__(self):
        self.violations = []

    def fail(self, name):
        self.violations.append(name)
        raise ExportIsolationError(f"Runtime operation during OpenAPI export: {name}")

    def callable(self, name):
        def blocked(*args, **kwargs):
            return self.fail(name)
        return blocked

    def assert_unused(self):
        if self.violations:
            raise ExportIsolationError(f"Isolation violations: {self.violations}")


class _HeavyImportBlocker(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in FORBIDDEN_IMPORTS:
            raise ModuleNotFoundError(
                f"Heavy dependency excluded from OpenAPI export: {fullname}", name=fullname
            )
        return None


@contextmanager
def isolate_runtime():
    guard = RuntimeGuard()
    loaded = FORBIDDEN_IMPORTS.intersection(name.split(".")[0] for name in sys.modules)
    if loaded:
        raise ExportIsolationError(f"Heavy modules already loaded: {sorted(loaded)}")
    router = ModuleType("app.core.rag.chunk.router")
    router.FileTypeRouter = guard.callable("FileTypeRouter")
    errors = ModuleType("modelscope.hub.errors")
    errors.raise_for_http_status = guard.callable("modelscope.raise_for_http_status")
    mcp_api = ModuleType("modelscope.hub.mcp_api")
    mcp_api.MCPApi = guard.callable("modelscope.MCPApi")
    guard.stubbed_modules = [router.__name__, errors.__name__, mcp_api.__name__]

    class Encoder:
        encode = staticmethod(guard.callable("encoder.encode"))
        decode = staticmethod(guard.callable("encoder.decode"))

    blocker = _HeavyImportBlocker()
    sys.meta_path.insert(0, blocker)
    def no_network(*args, **kwargs):
        guard.fail("network connection")

    try:
        with patch.dict(sys.modules, {
            module.__name__: module for module in (router, errors, mcp_api)
        }), \
             patch("tiktoken.get_encoding", return_value=Encoder()), \
             patch("socket.socket.connect", no_network), \
             patch("socket.socket.connect_ex", no_network), \
             patch("psycopg2.connect", guard.callable("psycopg2.connect")), \
             patch("asyncpg.connect", guard.callable("asyncpg.connect")):
            yield guard
            guard.assert_unused()
    finally:
        sys.meta_path.remove(blocker)
