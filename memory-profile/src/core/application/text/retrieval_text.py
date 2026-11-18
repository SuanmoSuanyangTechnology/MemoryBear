"""Query preprocessing and retrieval summarization.

Ported from the host's ``read_services/generate_engine``. Both steps degrade
gracefully: a failed model call records a :class:`MemoryRetrievalBusinessError`
and falls back to the original query/content instead of failing the read.

The query splitter keeps the host's text pipeline (``ainvoke`` + ``StructResponse``
repair). The retrieval summary uses the explicit structured-output channel:
``call_structured(messages, RetrievalSummary)`` asks the provider for
``{"summary": "..."}`` and its built-in fallback repairs/validates the same
schema when the provider cannot enforce tools.

Prompt resources are rendered through :class:`PromptRenderer`, so the exact
prompt text lives in ``src/core/resources/prompts/`` (no API import).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from src.core.application.text.structured import StructResponse
from src.core.domain.errors import (
    MemoryRetrievalBusinessError,
    RetrievalModelType,
    RetrievalStage,
)
from src.core.domain.models import QuestionSplit, RetrievalSummary
from src.core.domain.ports import ChatClient, PromptRenderer

logger = logging.getLogger(__name__)

#: Pronouns the host replaces with the generic user label before retrieval.
_USER_PRONOUNS = ("我", "本人", "在下", "自己", "咱", "鄙人", "吴", "余")
_USER_LABEL = "用户"
_PRONOUN_PATTERN = re.compile("|".join(_USER_PRONOUNS))
_QUESTION_SPLIT_PROMPT = "problem_split"
_RETRIEVAL_SUMMARY_PROMPT = "retrieval_summary"


def process_query(query: str) -> str:
    """Normalize the query the way the host pipeline does (__ENTITY__ scrubbing)."""

    text = query.strip()
    if not text:
        return text
    return _PRONOUN_PATTERN.sub(_USER_LABEL, text)


class QueryPreprocessor:
    """Split one user question into retrieval sub-questions."""

    def __init__(
        self,
        *,
        prompts: PromptRenderer,
        today: Callable[[], str],
    ) -> None:
        self._prompts = prompts
        self._today = today

    async def split(
        self,
        query: str,
        history: list[dict[str, Any]],
        memory_l0_str: str,
        llm_client: ChatClient,
        on_error: Callable[[MemoryRetrievalBusinessError], None] | None = None,
    ) -> list[str]:
        system_prompt = self._prompts.render(
            _QUESTION_SPLIT_PROMPT, datetime=self._today()
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"<history>{history}</history>"
                    f"<memory>{memory_l0_str}</memory>"
                    f"<query>{query}</query>"
                ),
            },
        ]
        try:
            response = await llm_client.ainvoke(messages, config={"callbacks": []})
        except Exception as error:  # noqa: BLE001 - degrade, never fail the read
            logger.error("[QueryPreprocessor] sub-question segmentation failed - %s", error)
            if on_error is not None:
                on_error(
                    MemoryRetrievalBusinessError.model_call_failed(
                        RetrievalStage.QUERY_PROCESS,
                        error,
                        model_type=RetrievalModelType.LLM,
                    )
                )
            return [query]

        try:
            sub_queries = response | StructResponse(QuestionSplit)
            queries = [str(question) for question in sub_queries.questions]
        except Exception as error:  # noqa: BLE001 - degrade, never fail the read
            logger.error("[QueryPreprocessor] sub-question segmentation failed - %s", error)
            if on_error is not None:
                on_error(
                    MemoryRetrievalBusinessError.structured_result_parse_failed(
                        RetrievalStage.QUERY_PROCESS,
                        error,
                        model_type=RetrievalModelType.LLM,
                    )
                )
            queries = [query]
        return queries or [query]


def _summary_failure(error: Exception) -> MemoryRetrievalBusinessError:
    """Classify a failed structured summary call (validation vs model failure)."""

    if isinstance(error, ValueError):
        # pydantic's ValidationError and json-repair errors subclass ValueError.
        return MemoryRetrievalBusinessError.structured_result_parse_failed(
            RetrievalStage.SUMMARY,
            error,
            model_type=RetrievalModelType.LLM,
        )
    return MemoryRetrievalBusinessError.model_call_failed(
        RetrievalStage.SUMMARY,
        error,
        model_type=RetrievalModelType.LLM,
    )


class RetrievalSummaryProcessor:
    """Summarize merged retrieval content for the final answer."""

    def __init__(self, *, prompts: PromptRenderer) -> None:
        self._prompts = prompts

    async def summary(
        self,
        query: str,
        content: str,
        memory_l0_str: str,
        llm_client: ChatClient,
        on_error: Callable[[MemoryRetrievalBusinessError], None] | None = None,
    ) -> str:
        system_prompt = self._prompts.render(_RETRIEVAL_SUMMARY_PROMPT)
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"<query>{query}</query>"
                    f"<content>{content}{memory_l0_str}</content>"
                ),
            },
        ]
        try:
            structured = await llm_client.call_structured(
                messages, RetrievalSummary
            )
        except Exception as error:  # noqa: BLE001 - degrade to raw content
            logger.error(
                "Failed to generate reply summary, returning original content",
                exc_info=True,
            )
            if on_error is not None:
                on_error(_summary_failure(error))
            return content

        if isinstance(structured, RetrievalSummary):
            summary = structured.summary
        elif isinstance(structured, dict):
            summary = str(structured.get("summary") or "")
        else:  # pragma: no cover - defensive against a non-schema client
            summary = str(structured)
        return summary if summary.strip() else content


__all__ = ["QueryPreprocessor", "RetrievalSummaryProcessor", "process_query"]