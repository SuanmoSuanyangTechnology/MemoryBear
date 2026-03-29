# -*- coding: UTF-8 -*-
"""Unit tests for MiniMax LLM provider integration.

Tests cover:
- ModelProvider enum registration
- MiniMaxClient temperature clamping, think-tag stripping
- LLMClientFactory.create("minimax") dispatching

Run: cd api && python -m pytest tests/test_minimax_provider.py -v
"""

import os
import sys
import json
import importlib
import importlib.util
from enum import Enum
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

# ---- Ensure app package is importable ----
API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)

# ---- StrEnum compat for Python < 3.11 ----
if not hasattr(Enum, '__str_members__'):
    try:
        from enum import StrEnum  # noqa: F401
    except ImportError:
        import enum
        class StrEnum(str, enum.Enum):
            pass
        enum.StrEnum = StrEnum


# ---------------------------------------------------------------------------
# Helper: isolated import of models_model
# ---------------------------------------------------------------------------

def _import_models_model():
    """Import models_model.py directly, bypassing app.models.__init__."""
    spec = importlib.util.spec_from_file_location(
        "models_model_isolated",
        os.path.join(API_DIR, "app", "models", "models_model.py"),
    )
    mod = importlib.util.module_from_spec(spec)

    # Stub out heavy deps
    sa_stub = MagicMock()
    pg_stub = MagicMock()
    pg_stub.UUID = MagicMock(return_value=MagicMock())
    pg_stub.JSON = MagicMock()
    db_stub = MagicMock()
    db_stub.Base = type("Base", (), {"metadata": MagicMock()})

    originals = {}
    stubs = {
        "sqlalchemy": sa_stub,
        "sqlalchemy.orm": sa_stub.orm,
        "sqlalchemy.sql": sa_stub.sql,
        "sqlalchemy.dialects": sa_stub.dialects,
        "sqlalchemy.dialects.postgresql": pg_stub,
        "app.db": db_stub,
    }
    for m, s in stubs.items():
        originals[m] = sys.modules.get(m)
        sys.modules[m] = s

    try:
        spec.loader.exec_module(mod)
    finally:
        for m, v in originals.items():
            if v is None:
                sys.modules.pop(m, None)
            else:
                sys.modules[m] = v
    return mod


def _import_llm_client():
    """Import llm_client.py directly with minimal stubs."""
    spec = importlib.util.spec_from_file_location(
        "llm_client_isolated",
        os.path.join(API_DIR, "app", "services", "llm_client.py"),
    )
    mod = importlib.util.module_from_spec(spec)

    logger_stub = MagicMock()
    logging_mod = MagicMock()
    logging_mod.get_business_logger = MagicMock(return_value=logger_stub)
    saved = sys.modules.get("app.core.logging_config")
    sys.modules["app.core.logging_config"] = logging_mod
    try:
        spec.loader.exec_module(mod)
    finally:
        if saved is None:
            sys.modules.pop("app.core.logging_config", None)
        else:
            sys.modules["app.core.logging_config"] = saved
    return mod


# ===========================================================================
# 1. ModelProvider enum
# ===========================================================================

class TestModelProviderEnum:

    def test_minimax_in_model_provider(self):
        mod = _import_models_model()
        assert hasattr(mod.ModelProvider, "MINIMAX")
        assert mod.ModelProvider.MINIMAX == "minimax"

    def test_minimax_is_str(self):
        mod = _import_models_model()
        assert isinstance(mod.ModelProvider.MINIMAX, str)

    def test_minimax_not_composite(self):
        mod = _import_models_model()
        assert mod.ModelProvider.MINIMAX != mod.ModelProvider.COMPOSITE

    def test_all_original_providers_preserved(self):
        mod = _import_models_model()
        for name in ["OPENAI", "DASHSCOPE", "OLLAMA", "XINFERENCE", "GPUSTACK", "BEDROCK", "COMPOSITE"]:
            assert hasattr(mod.ModelProvider, name), f"Missing provider: {name}"


# ===========================================================================
# 2. MiniMaxClient
# ===========================================================================

class TestMiniMaxClient:

    def test_missing_api_key_raises(self):
        mod = _import_llm_client()
        env = {k: v for k, v in os.environ.items() if k != "MINIMAX_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="MiniMax API key"):
                mod.MiniMaxClient(api_key=None)

    def test_env_api_key_used(self):
        mod = _import_llm_client()
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "sk-from-env"}):
            client = mod.MiniMaxClient()
            assert client.api_key == "sk-from-env"

    def test_explicit_api_key_overrides_env(self):
        mod = _import_llm_client()
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "sk-from-env"}):
            client = mod.MiniMaxClient(api_key="sk-explicit")
            assert client.api_key == "sk-explicit"

    def test_default_model(self):
        mod = _import_llm_client()
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "sk-test"}):
            client = mod.MiniMaxClient()
            assert client.model == "MiniMax-M2.7"

    def test_custom_model(self):
        mod = _import_llm_client()
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "sk-test"}):
            client = mod.MiniMaxClient(model="MiniMax-M2.7-highspeed")
            assert client.model == "MiniMax-M2.7-highspeed"

    def test_default_base_url(self):
        mod = _import_llm_client()
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "sk-test"}):
            client = mod.MiniMaxClient()
            assert client.base_url == "https://api.minimax.io/v1"

    def test_temperature_clamping_static(self):
        mod = _import_llm_client()
        clamp = mod.MiniMaxClient._clamp_temperature
        assert clamp(0) == 0.01
        assert clamp(-1.0) == 0.01
        assert clamp(0.5) == 0.5
        assert clamp(1.0) == 1.0
        assert clamp(2.0) == 1.0
        assert clamp(0.01) == 0.01
        assert clamp(0.99) == 0.99

    @pytest.mark.asyncio
    async def test_chat_strips_think_tags(self):
        mod = _import_llm_client()
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "sk-test"}):
            client = mod.MiniMaxClient()
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = "<think>reasoning</think>\nHello!"
            client.client = AsyncMock()
            client.client.chat.completions.create = AsyncMock(return_value=mock_resp)
            result = await client.chat("Hi")
            assert "<think>" not in result
            assert "Hello!" in result

    @pytest.mark.asyncio
    async def test_chat_normal_response(self):
        mod = _import_llm_client()
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "sk-test"}):
            client = mod.MiniMaxClient()
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = "Hello world!"
            client.client = AsyncMock()
            client.client.chat.completions.create = AsyncMock(return_value=mock_resp)
            result = await client.chat("Hi")
            assert result == "Hello world!"

    @pytest.mark.asyncio
    async def test_chat_uses_clamped_temperature(self):
        mod = _import_llm_client()
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "sk-test"}):
            client = mod.MiniMaxClient()
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = "ok"
            client.client = AsyncMock()
            client.client.chat.completions.create = AsyncMock(return_value=mock_resp)
            await client.chat("test", temperature=0)
            call_kwargs = client.client.chat.completions.create.call_args
            assert call_kwargs.kwargs["temperature"] == 0.01

    @pytest.mark.asyncio
    async def test_chat_api_error_propagated(self):
        mod = _import_llm_client()
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "sk-test"}):
            client = mod.MiniMaxClient()
            client.client = AsyncMock()
            client.client.chat.completions.create = AsyncMock(
                side_effect=Exception("API error")
            )
            with pytest.raises(Exception, match="API error"):
                await client.chat("test")

    @pytest.mark.asyncio
    async def test_chat_multiline_think_tag(self):
        """Think tag with multiple lines should be fully stripped."""
        mod = _import_llm_client()
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "sk-test"}):
            client = mod.MiniMaxClient()
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = (
                "<think>\nStep 1: analyze\nStep 2: reason\n</think>\n\nFinal answer."
            )
            client.client = AsyncMock()
            client.client.chat.completions.create = AsyncMock(return_value=mock_resp)
            result = await client.chat("complex question")
            assert "<think>" not in result
            assert "Final answer." in result


# ===========================================================================
# 3. LLMClientFactory
# ===========================================================================

class TestLLMClientFactoryMiniMax:

    def test_create_minimax(self):
        mod = _import_llm_client()
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "sk-test"}):
            client = mod.LLMClientFactory.create("minimax")
            assert isinstance(client, mod.MiniMaxClient)

    def test_create_minimax_uppercase(self):
        mod = _import_llm_client()
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "sk-test"}):
            client = mod.LLMClientFactory.create("MiniMax")
            assert isinstance(client, mod.MiniMaxClient)

    def test_create_from_env_minimax(self):
        mod = _import_llm_client()
        with patch.dict(os.environ, {"LLM_PROVIDER": "minimax", "MINIMAX_API_KEY": "sk-test"}):
            client = mod.LLMClientFactory.create_from_env()
            assert isinstance(client, mod.MiniMaxClient)

    def test_other_providers_still_work(self):
        mod = _import_llm_client()
        client = mod.LLMClientFactory.create("mock")
        assert isinstance(client, mod.MockLLMClient)


# ===========================================================================
# 4. Temperature clamping edge cases
# ===========================================================================

class TestMiniMaxTemperatureClamping:

    def test_boundary_values(self):
        mod = _import_llm_client()
        clamp = mod.MiniMaxClient._clamp_temperature
        assert clamp(0.0) == 0.01
        assert clamp(1.0) == 1.0
        assert clamp(0.001) == 0.001
        assert clamp(0.999) == 0.999
        assert clamp(-100) == 0.01
        assert clamp(100) == 1.0
        assert clamp(1.001) == 1.0

    def test_common_temperatures(self):
        mod = _import_llm_client()
        clamp = mod.MiniMaxClient._clamp_temperature
        for t in [0.1, 0.2, 0.3, 0.5, 0.7, 0.8, 0.9]:
            assert clamp(t) == t, f"Temperature {t} should be preserved"
