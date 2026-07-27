from unittest.mock import patch

from app.core.models.base import (
    RedBearModelConfig,
    RedBearModelFactory,
    get_provider_llm_class,
)
from app.core.models.compatible_chat import CompatibleChatOpenAI
from app.core.models.scripts.loader import get_models_by_provider
from app.models.models_model import ModelProvider


def test_atlascloud_models_are_registered():
    models = get_models_by_provider(ModelProvider.ATLASCLOUD)

    assert {model["name"] for model in models} == {
        "deepseek-ai/deepseek-v4-pro",
        "deepseek-ai/deepseek-v4-flash",
        "qwen/qwen3.5-27b",
    }
    assert all(model["provider"] == "atlascloud" for model in models)
    assert all("function_call" in model["capability"] for model in models)


def test_atlascloud_uses_openai_compatible_chat_client():
    config = RedBearModelConfig(
        model_name="deepseek-ai/deepseek-v4-pro",
        provider=ModelProvider.ATLASCLOUD,
        api_key="test-key",
        base_url="https://api.atlascloud.ai/v1",
    )

    assert get_provider_llm_class(config) is CompatibleChatOpenAI

    sync_client = object()
    async_client = object()
    with patch(
        "app.core.models.base._get_shared_openai_clients",
        return_value=(sync_client, async_client),
    ):
        params = RedBearModelFactory.get_model_params(config)

    assert params["model"] == "deepseek-ai/deepseek-v4-pro"
    assert params["base_url"] == "https://api.atlascloud.ai/v1"
    assert params["api_key"] == "test-key"
    assert params["http_client"] is sync_client
    assert params["http_async_client"] is async_client
