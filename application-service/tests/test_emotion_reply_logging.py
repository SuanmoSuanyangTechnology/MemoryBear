"""Reply logs identify injection outcomes without exposing messages or strategies."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import plugins
from src.infrastructure.upstream import UpstreamResponse, UpstreamUnavailableError
from src.memory.emotion import emotion_cache
from src.memory.emotion import emotion_resolver as resolver
from src.memory.emotion_policy import EMOTION_POLICY_PLUGIN, RemoteEmotionPolicyProvider
from src.services import fast_write_emotion_client

PRIVATE_TEXT = "private user message or strategy or error detail"
APP_ID = "1c1cdd30-200f-46e3-b4e3-f1fd44380c89"
TENANT_ID = "c2ae22ba-3190-425c-b540-be21e497842b"
ONTOLOGY_ID = "8c38412f-c8ad-42b2-aa5a-111f1d94f94e"


@pytest.fixture(autouse=True)
def logging_env(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=resolver.__name__)
    caplog.set_level(logging.DEBUG, logger="src.memory.emotion_policy")
    monkeypatch.setattr(plugins, "_plugins", {})
    monkeypatch.setattr(resolver, "_policies_cache", {})
    monkeypatch.setattr(emotion_cache, "set_cached_emotion", AsyncMock())


def event_fields(caplog, event):
    records = [
        record for record in caplog.records
        if record.name in (resolver.__name__, "src.memory.emotion_policy")
    ]
    for record in records:
        assert PRIVATE_TEXT not in record.getMessage()
    matches = [
        (record.levelno, dict(token.split("=", 1) for token in record.getMessage().split()
                             if "=" in token))
        for record in records if f"event={event} " in record.getMessage()
    ]
    assert matches
    return matches[-1]


def detection(result=None, error=None):
    future = asyncio.get_running_loop().create_future()
    if error is None:
        future.set_result(result)
    else:
        future.set_exception(error)
    return future


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["custom", "system_default"])
@pytest.mark.parametrize("write_cache", [True, False])
async def test_detection_callback_and_injection_log_end_to_end(
    monkeypatch, caplog, source, write_cache,
):
    strategy = {
        "ontology_id": ONTOLOGY_ID, "ontology_source": source,
        "tone": PRIVATE_TEXT, "do_list": ["listen"], "avoid_list": ["dismiss"],
    }
    upstream = SimpleNamespace(forward_async=AsyncMock(return_value=UpstreamResponse(
        200, {"code": 0, "data": {"available": True, "strategy": strategy}},
    )))
    plugins.register_plugin(EMOTION_POLICY_PLUGIN, RemoteEmotionPolicyProvider(upstream))
    predict = AsyncMock(return_value=fast_write_emotion_client.FastWriteEmotionResult(
        emotion="anxiety", emotion_score=0.91,
    ))
    monkeypatch.setattr(resolver, "_deployment_allows", lambda: True)
    monkeypatch.setattr(fast_write_emotion_client, "predict", predict)

    task = resolver.start_detection({"emotion_reply": {"enabled": True}}, PRIVATE_TEXT)
    prompt = await resolver.apply_detection(
        "base prompt", task, "message-id", app_id=APP_ID, tenant_id=TENANT_ID,
        write_cache=write_cache,
    )

    assert prompt.startswith("base prompt\n\n")
    assert "## 本轮回复策略" in prompt
    assert PRIVATE_TEXT in prompt
    predict.assert_awaited_once_with(PRIVATE_TEXT)
    upstream.forward_async.assert_awaited_once_with(
        "POST", "/api/internal/app-service/memory/emotion-policy",
        json_body={"app_id": APP_ID, "tenant_id": TENANT_ID, "emotion_code": "anxiety"},
    )
    assert emotion_cache.set_cached_emotion.await_count == int(write_cache)
    level, callback = event_fields(caplog, "policy_callback")
    assert level == logging.INFO
    assert callback["ontology_source"] == source
    assert callback["ontology_id"] == ONTOLOGY_ID
    assert callback["result"] == "hit"
    level, injection = event_fields(caplog, "prompt_injection")
    assert level == logging.INFO
    assert injection["app_id"] == APP_ID
    assert injection["tenant_id"] == TENANT_ID
    assert injection["result"] == "applied"
    assert injection["emotion_code"] == "anxiety"
    assert injection["emotion_score"] == "0.910"
    assert float(injection["elapsed_ms"]) >= 0


@pytest.mark.asyncio
async def test_disabled_feature_logs_at_debug_and_skips_work(monkeypatch, caplog):
    predict = AsyncMock()
    monkeypatch.setattr(fast_write_emotion_client, "predict", predict)
    task = resolver.start_detection({"emotion_reply": {"enabled": False}}, PRIVATE_TEXT)
    assert task is None
    assert await resolver.apply_detection("base", task, None) == "base"
    predict.assert_not_awaited()
    assert event_fields(caplog, "detect")[1]["reason"] == "feature_disabled"
    level, fields = event_fields(caplog, "prompt_injection")
    assert level == logging.DEBUG
    assert fields["reason"] == "feature_disabled"


@pytest.mark.asyncio
@pytest.mark.parametrize("message,reason", [
    (" ", "empty_message"), (PRIVATE_TEXT, "service_unconfigured"),
])
async def test_detection_skip_reason(monkeypatch, caplog, message, reason):
    monkeypatch.setattr(resolver, "_deployment_allows", lambda: False)
    assert await resolver.detect_emotion(message) is None
    assert event_fields(caplog, "detect")[1]["reason"] == reason


@pytest.mark.asyncio
@pytest.mark.parametrize("error,reason", [
    (TimeoutError(PRIVATE_TEXT), "timeout"), (RuntimeError(PRIVATE_TEXT), "service_error"),
])
async def test_detection_failure_is_safe_and_preserves_reply(monkeypatch, caplog, error, reason):
    monkeypatch.setattr(resolver, "_deployment_allows", lambda: True)
    monkeypatch.setattr(fast_write_emotion_client, "predict", AsyncMock(side_effect=error))
    assert await resolver.resolve_and_inject(
        "base", PRIVATE_TEXT, None, app_id=APP_ID, tenant_id=TENANT_ID,
    ) == "base"
    level, fields = event_fields(caplog, "detect")
    assert level == logging.WARNING
    assert fields["reason"] == reason
    level, fields = event_fields(caplog, "prompt_injection")
    assert level == logging.INFO
    assert fields["reason"] == "no_emotion_result"


@pytest.mark.asyncio
@pytest.mark.parametrize("result,policy,error,reason,level", [
    (None, None, None, "no_emotion_result", logging.INFO),
    (SimpleNamespace(emotion="anxiety", emotion_score=0.5), None, None,
     "policy_unavailable", logging.INFO),
    (SimpleNamespace(emotion="anxiety", emotion_score=0.5), {"tone": ""}, None,
     "strategy_not_rendered", logging.INFO),
    (None, None, RuntimeError(PRIVATE_TEXT), "task_error", logging.WARNING),
    (SimpleNamespace(emotion=PRIVATE_TEXT, emotion_score=float("nan")), None, None,
     "invalid_emotion_code", logging.WARNING),
])
async def test_injection_outcomes(monkeypatch, caplog, result, policy, error, reason, level):
    monkeypatch.setattr(resolver, "resolve_policy", AsyncMock(return_value=policy))
    assert await resolver.apply_detection(
        "base", detection(result, error), None, app_id=APP_ID, tenant_id=TENANT_ID,
    ) == "base"
    actual_level, fields = event_fields(caplog, "prompt_injection")
    assert actual_level == level
    assert fields["result"] == "unchanged"
    assert fields["reason"] == reason
    if reason == "task_error":
        assert fields["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_community_fallback_identifies_source(monkeypatch, caplog):
    monkeypatch.setattr(resolver, "_policies_cache", {"anxiety": {"tone": PRIVATE_TEXT}})
    result = SimpleNamespace(emotion="anxiety", emotion_score=0.5)
    assert PRIVATE_TEXT in await resolver.apply_detection("base", detection(result), None)
    level, fields = event_fields(caplog, "policy_lookup")
    assert level == logging.INFO
    assert fields["ontology_source"] == "community_json"
    assert fields["reason"] == "provider_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("response,error,reason,level", [
    (None, UpstreamUnavailableError(PRIVATE_TEXT), "upstream_unavailable", logging.WARNING),
    (UpstreamResponse(403, {"code": 1, "message": PRIVATE_TEXT}), None,
     "upstream_rejected", logging.WARNING),
    (UpstreamResponse(200, {"code": 1}), None, "upstream_rejected", logging.WARNING),
    (UpstreamResponse(200, {"code": 0, "data": {"available": False}}), None,
     "provider_unavailable", logging.WARNING),
    (UpstreamResponse(200, {"code": 0, "data": {"available": True, "strategy": None}}), None,
     "strategy_unavailable", logging.INFO),
])
async def test_callback_failures_do_not_use_community_policy(
    monkeypatch, caplog, response, error, reason, level,
):
    upstream = SimpleNamespace(forward_async=AsyncMock(return_value=response, side_effect=error))
    plugins.register_plugin(EMOTION_POLICY_PLUGIN, RemoteEmotionPolicyProvider(upstream))
    monkeypatch.setattr(resolver, "_policies_cache", {"anxiety": {"tone": PRIVATE_TEXT}})
    result = SimpleNamespace(emotion="anxiety", emotion_score=0.5)
    assert await resolver.apply_detection(
        "base", detection(result), None, app_id=APP_ID, tenant_id=TENANT_ID,
    ) == "base"
    actual_level, fields = event_fields(caplog, "policy_callback")
    assert actual_level == level
    assert fields["reason"] == reason
    assert fields["app_id"] == APP_ID
    assert fields["tenant_id"] == TENANT_ID
    assert fields["result"] == "miss"
    assert event_fields(caplog, "prompt_injection")[1]["reason"] == "policy_unavailable"


@pytest.mark.asyncio
async def test_missing_identity_skips_callback(caplog):
    upstream = SimpleNamespace(forward_async=AsyncMock())
    plugins.register_plugin(EMOTION_POLICY_PLUGIN, RemoteEmotionPolicyProvider(upstream))
    result = SimpleNamespace(emotion="anxiety", emotion_score=0.5)
    assert await resolver.apply_detection("base", detection(result), None) == "base"
    upstream.forward_async.assert_not_awaited()
    assert event_fields(caplog, "policy_lookup")[1]["reason"] == "missing_identity"


@pytest.mark.asyncio
async def test_provider_exception_logs_type_without_detail(caplog):
    provider = SimpleNamespace(get_strategy=AsyncMock(side_effect=RuntimeError(PRIVATE_TEXT)))
    plugins.register_plugin(EMOTION_POLICY_PLUGIN, provider)
    result = SimpleNamespace(emotion="anxiety", emotion_score=0.5)
    assert await resolver.apply_detection(
        "base", detection(result), None, app_id=APP_ID, tenant_id=TENANT_ID,
    ) == "base"
    level, fields = event_fields(caplog, "policy_lookup")
    assert level == logging.WARNING
    assert fields["reason"] == "provider_error"
    assert fields["error_type"] == "RuntimeError"
