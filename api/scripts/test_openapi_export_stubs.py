"""Run with python -m unittest discover -s scripts -p 'test_openapi_export_stubs.py'."""

import importlib
import socket
import sys
import unittest
from unittest.mock import patch

from openapi_export_stubs import (
    ExportIsolationError, FORBIDDEN_IMPORTS, isolate_runtime,
)


class RuntimeIsolationTests(unittest.TestCase):
    def test_heavy_imports_are_unavailable(self):
        with isolate_runtime():
            for name in FORBIDDEN_IMPORTS:
                with self.subTest(name=name), self.assertRaises(ModuleNotFoundError):
                    importlib.import_module(name)

    def test_allowed_symbols_fail_on_execution(self):
        for module, symbol in (
            ("app.core.rag.chunk.router", "FileTypeRouter"),
            ("modelscope.hub.errors", "raise_for_http_status"),
            ("modelscope.hub.mcp_api", "MCPApi"),
        ):
            with self.subTest(symbol=symbol), self.assertRaises(ExportIsolationError):
                with isolate_runtime():
                    getattr(importlib.import_module(module), symbol)()

    def test_swallowed_exception_still_fails_export(self):
        with self.assertRaisesRegex(ExportIsolationError, "Isolation violations"):
            with isolate_runtime():
                import tiktoken
                try:
                    tiktoken.get_encoding("cl100k_base").encode("test")
                except RuntimeError:
                    pass

    def test_unknown_symbols_are_not_fabricated(self):
        with isolate_runtime():
            module = importlib.import_module("app.core.rag.chunk.router")
            with self.assertRaises(AttributeError):
                getattr(module, "UnexpectedRuntimeSymbol")

    def test_patches_restored(self):
        import tiktoken
        original = tiktoken.get_encoding
        finders = list(sys.meta_path)
        with isolate_runtime():
            self.assertIsNot(tiktoken.get_encoding, original)
        self.assertIs(tiktoken.get_encoding, original)
        self.assertEqual(sys.meta_path, finders)
        self.assertNotIn("app.core.rag.chunk.router", sys.modules)

    def test_preloaded_heavy_module_rejected(self):
        with patch.dict(sys.modules, {"torch": object()}):
            with self.assertRaisesRegex(ExportIsolationError, "already loaded"):
                with isolate_runtime():
                    pass

    def test_network_disabled(self):
        with self.assertRaisesRegex(ExportIsolationError, "network connection"):
            with isolate_runtime(), socket.socket() as connection:
                connection.connect(("127.0.0.1", 9))

    def test_swallowed_network_error_still_fails(self):
        with self.assertRaisesRegex(ExportIsolationError, "Isolation violations"):
            with isolate_runtime(), socket.socket() as connection:
                try:
                    connection.connect(("127.0.0.1", 9))
                except RuntimeError:
                    pass

    def test_native_postgres_connection_blocked(self):
        with self.assertRaisesRegex(ExportIsolationError, "psycopg2.connect"):
            with isolate_runtime():
                import psycopg2
                psycopg2.connect("")

    def test_patches_restored_after_failure(self):
        import tiktoken
        original = tiktoken.get_encoding
        finders = list(sys.meta_path)
        with self.assertRaises(ExportIsolationError):
            with isolate_runtime():
                tiktoken.get_encoding("cl100k_base").decode([])
        self.assertIs(tiktoken.get_encoding, original)
        self.assertEqual(sys.meta_path, finders)


if __name__ == "__main__":
    unittest.main()
