from typing import Any

from app.core.config import settings
from app.core.models import RedBearEmbeddings, RedBearModelConfig
from app.models.models_model import ModelProvider


class _LimitedAsyncEmbeddingClient:
    async def create(self, *, input: list[Any], **_: Any) -> dict[str, Any]:
        if len(input) > 10:
            raise ValueError("batch size must not exceed 10")
        return {
            "data": [
                {"embedding": [float(index)]}
                for index, _item in enumerate(input)
            ]
        }


async def test_openai_compatible_embedding_uses_configured_batch_size(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "EMBEDDING_BATCH_SIZE", 10)
    embedding = RedBearEmbeddings(
        RedBearModelConfig(
            model_name="test-embedding",
            provider=ModelProvider.SPEEDBEAR,
            api_key="test-key",
            base_url="https://example.com/v1",
        )
    )
    embedding._model.async_client = _LimitedAsyncEmbeddingClient()

    vectors = await embedding.aembed_documents(
        [f"projection-{index}" for index in range(11)]
    )

    assert len(vectors) == 11
