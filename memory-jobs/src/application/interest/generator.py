"""Interest-distribution generation with the API's budget and degradation order.

The pure grouping rules live in ``domain.interest.services``; this module owns
the provider-shaped parts (structured-output schema, prompt calls), the two-pass
strategy and the time budget.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from pydantic import BaseModel, Field

from src.application.ports.chat_model import ChatModel
from src.application.ports.prompts import PromptCatalog
from src.domain.interest.model import (
    Interest,
    InterestDistribution,
    InterestGroup,
)
from src.domain.interest.read_models import InterestCandidate, StatementEvidence
from src.domain.interest.repository import InterestSourceReader
from src.domain.interest.services import (
    INTEREST_DISPLAY_LIMIT,
    aggregate_interest_groups,
    validate_interest_groups,
)
from src.domain.shared.ids import WorkspaceId
from src.domain.shared.language import Language

logger = logging.getLogger(__name__)

INTEREST_DISTRIBUTION_LLM_TIMEOUT_SECONDS = 30
INTEREST_DISTRIBUTION_TOTAL_TIMEOUT_SECONDS = 45
INTEREST_DISTRIBUTION_MIN_FALLBACK_TIMEOUT_SECONDS = 5
INTEREST_CANDIDATE_LIMIT = 40
INTEREST_EVIDENCE_ENTITY_LIMIT = 12
INTEREST_EVIDENCE_PER_ENTITY_LIMIT = 2
INTEREST_EVIDENCE_LIMIT = 20
INTEREST_EVIDENCE_MAX_CHARS = 300
INTEREST_EXCLUDED_NAMES = [
    "用户",
    "user",
    "ai",
    "ai助手",
    "助手",
    "assistant",
    "ai回复",
    "系统",
    "system",
    "时间戳",
    "timestamp",
]
INTEREST_ISO_DATETIME_PATTERN = (
    r"(?i)^\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:z|[+-]\d{2}:?\d{2})?$"
)
INTEREST_UNIX_TIMESTAMP_PATTERN = r"^\d{10}(?:\d{3})?$"

#: Builds a chat model whose per-call idle timeout is the given budget.
ModelFactory = Callable[[float], ChatModel]


class InterestGroupOutput(BaseModel):
    """LLM-proposed interest and the candidate entity ids it claims."""

    name: str
    entity_ids: list[str] = Field(default_factory=list)


class InterestTagsOutput(BaseModel):
    interests: list[InterestGroupOutput] = Field(default_factory=list)


def _candidate_payload(candidate: InterestCandidate) -> dict[str, object]:
    return {
        "entity_id": candidate.entity_id,
        "name": candidate.name,
        "entity_type": candidate.entity_type,
        "frequency": candidate.frequency,
    }


def _evidence_payload(evidence: StatementEvidence) -> dict[str, object]:
    return {
        "statement_id": evidence.statement_id,
        "entity_ids": list(evidence.entity_ids),
        "statement_text": evidence.statement_text,
    }


def _remaining_budget(started_at: float) -> float:
    return max(
        0.0,
        INTEREST_DISTRIBUTION_TOTAL_TIMEOUT_SECONDS - (time.monotonic() - started_at),
    )


def _with_interest_budget[T](operation: Callable[[], T], started_at: float) -> T:
    if _remaining_budget(started_at) <= 0:
        raise TimeoutError("interest distribution total time budget exhausted")
    return operation()


def _log_interest_budget(
    *,
    end_user_id: str,
    stage: str,
    started_at: float,
    second_call_executed: bool,
    degradation_reason: str | None = None,
) -> None:
    elapsed = time.monotonic() - started_at
    remaining = max(0.0, INTEREST_DISTRIBUTION_TOTAL_TIMEOUT_SECONDS - elapsed)
    log_method = logger.warning if degradation_reason else logger.info
    log_method(
        "interest_distribution_budget end_user_id=%s stage=%s elapsed=%.3fs "
        "remaining=%.3fs second_call_executed=%s degradation_reason=%s",
        end_user_id,
        stage,
        elapsed,
        remaining,
        second_call_executed,
        degradation_reason or "none",
    )


def filter_interests_with_llm(
    prompts: PromptCatalog,
    model_factory: ModelFactory,
    candidates: list[InterestCandidate],
    end_user_id: str,
    language: str = "zh",
    evidence: list[StatementEvidence] | None = None,
    existing_interests: list[str] | None = None,
    timeout_seconds: float = INTEREST_DISTRIBUTION_LLM_TIMEOUT_SECONDS,
) -> list[InterestGroup]:
    """Group candidate entities into user interests via one structured call."""

    if timeout_seconds <= 0:
        raise TimeoutError("interest distribution LLM call has no remaining time budget")
    rendered_prompt = prompts.interest_filter(
        [_candidate_payload(candidate) for candidate in candidates],
        language=language,
        evidence=[_evidence_payload(item) for item in evidence] if evidence else None,
        existing_interests=existing_interests,
    )
    messages = [{"role": "user", "content": rendered_prompt}]
    started_at = time.monotonic()
    stage = "llm_fallback" if evidence else "llm_call"
    try:
        raw_response = model_factory(timeout_seconds).call_structured(
            messages, InterestTagsOutput
        )
        response = (
            raw_response
            if isinstance(raw_response, InterestTagsOutput)
            else InterestTagsOutput.model_validate(raw_response)
        )
    except Exception as exc:
        logger.error(
            "interest_distribution stage=%s_failed end_user_id=%s "
            "candidate_count=%s evidence_count=%s timeout=%.3fs elapsed=%.3fs "
            "error_type=%s error=%r",
            stage,
            end_user_id,
            len(candidates),
            len(evidence or []),
            timeout_seconds,
            time.monotonic() - started_at,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        raise

    logger.info(
        "interest_distribution stage=%s end_user_id=%s candidate_count=%s "
        "evidence_count=%s interest_count=%s timeout=%.3fs elapsed=%.3fs",
        stage,
        end_user_id,
        len(candidates),
        len(evidence or []),
        len(response.interests),
        timeout_seconds,
        time.monotonic() - started_at,
    )
    return [
        InterestGroup(name=group.name, entity_ids=tuple(group.entity_ids))
        for group in response.interests
    ]


def generate_interest_distribution(
    source: InterestSourceReader,
    model_factory: ModelFactory,
    prompts: PromptCatalog,
    end_user_id: str,
    workspace_id: WorkspaceId,
    language: Language,
    limit: int = INTEREST_DISPLAY_LIMIT,
) -> InterestDistribution:
    """Generate interests with the API's shared budget and degradation order."""

    started_at = time.monotonic()
    second_call_executed = False
    primary_items: list[Interest] = []
    failure_stage = "input_validation"
    if not end_user_id or not end_user_id.strip():
        raise ValueError(
            "end_user_id is required. Please provide a valid end_user_id or user_id."
        )

    def result(items: list[Interest], cacheable: bool) -> InterestDistribution:
        return InterestDistribution(
            end_user_id=end_user_id,
            workspace_id=workspace_id,
            language=language,
            items=tuple(items),
            cacheable=cacheable,
        )

    try:
        failure_stage = "candidate_query"
        try:
            candidates = _with_interest_budget(
                lambda: source.candidates(
                    end_user_id,
                    limit=INTEREST_CANDIDATE_LIMIT,
                    excluded_names=INTEREST_EXCLUDED_NAMES,
                    iso_datetime_pattern=INTEREST_ISO_DATETIME_PATTERN,
                    unix_timestamp_pattern=INTEREST_UNIX_TIMESTAMP_PATTERN,
                ),
                started_at,
            )
        except Exception:
            _log_interest_budget(
                end_user_id=end_user_id,
                stage="candidate_query",
                started_at=started_at,
                second_call_executed=second_call_executed,
                degradation_reason=(
                    "total_budget_exhausted"
                    if _remaining_budget(started_at) <= 0
                    else "candidate_query_failed"
                ),
            )
            raise
        failure_stage = "candidate_validation"
        if _remaining_budget(started_at) <= 0:
            _log_interest_budget(
                end_user_id=end_user_id,
                stage="candidate_validation",
                started_at=started_at,
                second_call_executed=second_call_executed,
                degradation_reason="total_budget_exhausted",
            )
            raise TimeoutError(
                "interest distribution total time budget exhausted before primary validation"
            )
        if not candidates:
            _log_interest_budget(
                end_user_id=end_user_id,
                stage="no_candidates",
                started_at=started_at,
                second_call_executed=second_call_executed,
            )
            return result([], cacheable=True)

        primary_timeout = min(
            INTEREST_DISTRIBUTION_LLM_TIMEOUT_SECONDS,
            _remaining_budget(started_at),
        )
        failure_stage = "primary_llm"
        try:
            interests = _with_interest_budget(
                lambda: filter_interests_with_llm(
                    prompts,
                    model_factory,
                    candidates,
                    end_user_id,
                    language=language.value,
                    timeout_seconds=primary_timeout,
                ),
                started_at,
            )
        except Exception:
            _log_interest_budget(
                end_user_id=end_user_id,
                stage="primary_llm",
                started_at=started_at,
                second_call_executed=second_call_executed,
                degradation_reason=(
                    "total_budget_exhausted"
                    if _remaining_budget(started_at) <= 0
                    else "primary_llm_failed"
                ),
            )
            raise
        if _remaining_budget(started_at) <= 0:
            _log_interest_budget(
                end_user_id=end_user_id,
                stage="primary_llm",
                started_at=started_at,
                second_call_executed=second_call_executed,
                degradation_reason="total_budget_exhausted",
            )
            raise TimeoutError(
                "interest distribution total time budget exhausted before primary validation"
            )
        failure_stage = "primary_validation"
        validated_primary_groups = validate_interest_groups(candidates, interests)
        combined_groups = list(validated_primary_groups)
        primary_items = aggregate_interest_groups(
            candidates,
            validated_primary_groups,
            limit=INTEREST_DISPLAY_LIMIT,
        )
        if _remaining_budget(started_at) <= 0:
            _log_interest_budget(
                end_user_id=end_user_id,
                stage="primary_validation",
                started_at=started_at,
                second_call_executed=second_call_executed,
                degradation_reason="total_budget_exhausted",
            )
            return result(primary_items[:limit], cacheable=False)

        if len(primary_items) <= 2:
            claimed_entity_ids = {
                entity_id
                for group in validated_primary_groups
                for entity_id in group.entity_ids
            }
            evidence_candidates = [
                candidate
                for candidate in candidates
                if candidate.entity_id not in claimed_entity_ids
            ][:INTEREST_EVIDENCE_ENTITY_LIMIT]

            if evidence_candidates:
                try:
                    failure_stage = "statement_evidence"
                    evidence = _with_interest_budget(
                        lambda: source.evidence(
                            end_user_id,
                            entity_ids=[
                                candidate.entity_id
                                for candidate in evidence_candidates
                            ],
                            per_entity_limit=INTEREST_EVIDENCE_PER_ENTITY_LIMIT,
                            limit=INTEREST_EVIDENCE_LIMIT,
                            max_chars=INTEREST_EVIDENCE_MAX_CHARS,
                        ),
                        started_at,
                    )
                    if _remaining_budget(started_at) <= 0:
                        _log_interest_budget(
                            end_user_id=end_user_id,
                            stage="statement_evidence",
                            started_at=started_at,
                            second_call_executed=second_call_executed,
                            degradation_reason="total_budget_exhausted",
                        )
                        return result(primary_items[:limit], cacheable=False)
                    if evidence:
                        evidenced_entity_ids = {
                            entity_id
                            for item in evidence
                            for entity_id in item.entity_ids
                        }
                        fallback_candidates = [
                            candidate
                            for candidate in evidence_candidates
                            if candidate.entity_id in evidenced_entity_ids
                        ]
                        if fallback_candidates:
                            remaining = _remaining_budget(started_at)
                            if remaining < INTEREST_DISTRIBUTION_MIN_FALLBACK_TIMEOUT_SECONDS:
                                _log_interest_budget(
                                    end_user_id=end_user_id,
                                    stage="before_second_llm",
                                    started_at=started_at,
                                    second_call_executed=second_call_executed,
                                    degradation_reason="total_budget_exhausted",
                                )
                                return result(primary_items[:limit], cacheable=False)
                            second_call_executed = True
                            fallback_timeout = min(
                                INTEREST_DISTRIBUTION_LLM_TIMEOUT_SECONDS,
                                remaining,
                            )
                            failure_stage = "second_llm"
                            supplemental_interests = _with_interest_budget(
                                lambda: filter_interests_with_llm(
                                    prompts,
                                    model_factory,
                                    fallback_candidates,
                                    end_user_id,
                                    language=language.value,
                                    evidence=evidence,
                                    existing_interests=[
                                        item.name for item in primary_items
                                    ],
                                    timeout_seconds=fallback_timeout,
                                ),
                                started_at,
                            )
                            if _remaining_budget(started_at) <= 0:
                                _log_interest_budget(
                                    end_user_id=end_user_id,
                                    stage="second_llm",
                                    started_at=started_at,
                                    second_call_executed=second_call_executed,
                                    degradation_reason="total_budget_exhausted",
                                )
                                return result(primary_items[:limit], cacheable=False)
                            # Merged raw, exactly like the API: the final
                            # ``aggregate_interest_groups`` re-validates the
                            # combined list with a fresh claim set, so primary
                            # entities are claimed first and supplementary
                            # duplicates (or hallucinated ids) are dropped there.
                            combined_groups.extend(supplemental_interests)
                except Exception as exc:
                    _log_interest_budget(
                        end_user_id=end_user_id,
                        stage="statement_fallback",
                        started_at=started_at,
                        second_call_executed=second_call_executed,
                        degradation_reason=(
                            "total_budget_exhausted"
                            if _remaining_budget(started_at) <= 0
                            else "statement_fallback_failed"
                        ),
                    )
                    logger.warning(
                        "interest_distribution stage=statement_fallback_failed "
                        "end_user_id=%s error_type=%s error=%r",
                        end_user_id,
                        type(exc).__name__,
                        exc,
                        exc_info=True,
                    )
                    return result(primary_items[:limit], cacheable=False)

        failure_stage = "final_validation"
        items = aggregate_interest_groups(candidates, combined_groups, limit=limit)
        if _remaining_budget(started_at) <= 0:
            _log_interest_budget(
                end_user_id=end_user_id,
                stage="final_validation",
                started_at=started_at,
                second_call_executed=second_call_executed,
                degradation_reason="total_budget_exhausted",
            )
            return result(primary_items[:limit], cacheable=False)
        _log_interest_budget(
            end_user_id=end_user_id,
            stage="completed",
            started_at=started_at,
            second_call_executed=second_call_executed,
        )
        return result(items, cacheable=bool(items))
    except Exception as exc:
        fallback_items = primary_items[:limit]
        cacheable = not fallback_items
        logger.error(
            "interest_distribution stage=%s_failed end_user_id=%s language=%s "
            "elapsed=%.3fs second_call_executed=%s fallback=%s cacheable=%s "
            "error_type=%s error=%r",
            failure_stage,
            end_user_id,
            language.value,
            time.monotonic() - started_at,
            second_call_executed,
            "primary_items" if fallback_items else "empty",
            cacheable,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return result(fallback_items, cacheable=cacheable)
