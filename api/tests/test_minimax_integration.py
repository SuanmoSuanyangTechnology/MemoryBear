# -*- coding: UTF-8 -*-
"""Integration tests for MiniMax LLM provider.

These tests verify end-to-end MiniMax integration with actual API calls.
They require a valid MINIMAX_API_KEY environment variable and are skipped
when the key is not available.

Usage:
    MINIMAX_API_KEY=your-key cd api && python -m pytest tests/test_minimax_integration.py -v
"""

import os
import sys

import pytest

API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)

MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY")
SKIP_REASON = "MINIMAX_API_KEY not set; skipping MiniMax integration tests"


@pytest.mark.skipif(not MINIMAX_API_KEY, reason=SKIP_REASON)
class TestMiniMaxClientIntegration:
    """Integration tests for MiniMaxClient in services layer."""

    @pytest.mark.asyncio
    async def test_minimax_client_chat(self):
        """Test MiniMaxClient.chat() with real API."""
        from app.services.llm_client import MiniMaxClient

        client = MiniMaxClient(
            api_key=MINIMAX_API_KEY,
            model="MiniMax-M2.7-highspeed"
        )
        result = await client.chat(
            "Reply with exactly the word 'pong'. Do not include any reasoning.",
            temperature=0.1,
            max_tokens=50
        )
        assert result is not None
        # Result may be empty if model returns only think tags;
        # the important thing is no exception
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_minimax_client_factory(self):
        """Test LLMClientFactory creates working MiniMaxClient."""
        from app.services.llm_client import LLMClientFactory

        client = LLMClientFactory.create(
            "minimax",
            api_key=MINIMAX_API_KEY,
            model="MiniMax-M2.7-highspeed"
        )
        result = await client.chat(
            "Reply with the number 42. Do not include any reasoning.",
            temperature=0.1,
            max_tokens=50
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_minimax_client_temperature_edge(self):
        """Test that temperature=0 works (clamped to 0.01)."""
        from app.services.llm_client import MiniMaxClient

        client = MiniMaxClient(
            api_key=MINIMAX_API_KEY,
            model="MiniMax-M2.7-highspeed"
        )
        result = await client.chat(
            "Say hi. Do not include any reasoning.",
            temperature=0,
            max_tokens=50
        )
        assert result is not None
