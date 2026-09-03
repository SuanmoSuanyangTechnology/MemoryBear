"""Native DashScope adapter for qwen3-vl rerank requests."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from redbear_model.contracts import (
    ImageEmbeddingContent,
    RerankCandidateView,
    RerankQuery,
    RerankScore,
    ResolvedModelConfig,
    TextEmbeddingContent,
)
from redbear_model.errors import (
    InvalidProviderResponseError,
    MultimodalInputLimitError,
    ProviderDependencyMissingError,
    UnsupportedMultimodalModelError,
)
from redbear_model.providers.dashscope import (
    is_dashscope_multimodal_input_limit,
    is_qwen3_vl_reranker,
    resolve_dashscope_native_base_address,
)


def _value(container: Any, key: str, default: Any = None) -> Any:
    if isinstance(container, Mapping):
        return container.get(key, default)
    return getattr(container, key, default)


def _load_call() -> Callable[..., Any]:
    try:
        from dashscope import TextReRank
    except ModuleNotFoundError as exc:
        raise ProviderDependencyMissingError("dashscope", "dashscope") from exc
    return TextReRank.call


def _query_payload(query: RerankQuery) -> str | dict[str, str]:
    if isinstance(query, TextEmbeddingContent):
        return query.text
    if isinstance(query, ImageEmbeddingContent):
        return {"image": query.data_uri}
    raise TypeError("unsupported rerank query")


class DashScopeMultimodalRerankAdapter:
    def __init__(
        self,
        config: ResolvedModelConfig,
        *,
        call: Callable[..., Any] | None = None,
    ) -> None:
        if not is_qwen3_vl_reranker(config):
            raise UnsupportedMultimodalModelError("qwen3-vl rerank")
        self._config = config
        self._call = call or _load_call()

    def rerank(
        self,
        query: RerankQuery,
        views: Sequence[RerankCandidateView],
        *,
        top_n: int,
    ) -> list[RerankScore]:
        if not views:
            return []
        if top_n < 1 or top_n > len(views):
            raise ValueError("top_n must be within the candidate view count")
        documents = [
            {"text": view.content}
            if view.kind == "text"
            else {"image": view.content}
            for view in views
        ]
        response = self._call(
            model=self._config.model_name,
            query=_query_payload(query),
            documents=documents,
            api_key=self._config.api_key.get_secret_value(),
            base_address=resolve_dashscope_native_base_address(self._config.base_url),
            top_n=top_n,
            return_documents=False,
        )
        if is_dashscope_multimodal_input_limit(response):
            raise MultimodalInputLimitError("rerank")
        if _value(response, "status_code") != 200:
            raise InvalidProviderResponseError("rerank", "non-success status")
        output = _value(response, "output")
        results = _value(output, "results")
        if not isinstance(results, Sequence) or isinstance(results, (str, bytes)):
            raise InvalidProviderResponseError("rerank", "missing results")
        scores: list[RerankScore] = []
        seen: set[int] = set()
        for result in results:
            index = _value(result, "index")
            raw_score = _value(result, "relevance_score")
            if not isinstance(index, int) or isinstance(index, bool):
                raise InvalidProviderResponseError("rerank", "invalid result index")
            if index < 0 or index >= len(views) or index in seen:
                raise InvalidProviderResponseError("rerank", "invalid result index")
            try:
                score = float(raw_score)
            except (TypeError, ValueError) as exc:
                raise InvalidProviderResponseError("rerank", "invalid relevance score") from exc
            if not math.isfinite(score) or score < 0 or score > 1:
                raise InvalidProviderResponseError("rerank", "invalid relevance score")
            seen.add(index)
            scores.append(RerankScore(input_index=index, relevance_score=score))
        return scores


__all__ = ["DashScopeMultimodalRerankAdapter"]
