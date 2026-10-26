"""Interest-distribution generation pipeline.

Straight port of the API's ``generate_interest_distribution``: candidate entities
come from Neo4j, one structured LLM call groups them into interests, and a
second evidence-backed call runs only when the first pass yields two or fewer
interests. The total time budget, degradation order and cacheability decision
are preserved because the API's cache stores the empty result too.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

from pydantic import BaseModel, Field

from src.common.normalization import collapse_whitespace
from src.common.timing import remaining_seconds
from src.infrastructure.model_service import ProfileChatModel
from src.modules.analytics.repository.generation_graph import GenerationGraphRepository
from src.prompts import render_interest_filter_prompt

logger = logging.getLogger(__name__)

INTEREST_DISTRIBUTION_LLM_TIMEOUT_SECONDS = 30
INTEREST_DISTRIBUTION_TOTAL_TIMEOUT_SECONDS = 45
INTEREST_DISTRIBUTION_MIN_FALLBACK_TIMEOUT_SECONDS = 5
INTEREST_CANDIDATE_LIMIT = 40
INTEREST_DISPLAY_LIMIT = 5
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

#: 本流水线的总预算绑定；通用计算见 ``src.common.timing``。
_remaining_interest_budget = partial(
    remaining_seconds,
    budget_seconds=INTEREST_DISTRIBUTION_TOTAL_TIMEOUT_SECONDS,
)


class InterestEntityCandidate(BaseModel):
    """带真实 Statement 频次的兴趣候选实体。"""

    entity_id: str
    name: str
    entity_type: str = ""
    frequency: int = Field(..., ge=1)


class InterestStatementEvidence(BaseModel):
    """候选实体关联的 Statement 语境证据。"""

    statement_id: str
    entity_ids: list[str]
    statement_text: str


class InterestGroup(BaseModel):
    """LLM 归纳出的兴趣及其候选来源。"""

    name: str
    entity_ids: list[str]


class InterestTags(BaseModel):
    """用于接收 LLM 归纳后的兴趣活动列表。"""

    interests: list[InterestGroup] = Field(default_factory=list)


class InterestDistributionGeneration(BaseModel):
    """兴趣分布生成结果及其缓存决策。"""

    items: list[tuple[str, int]] = Field(default_factory=list)
    cacheable: bool = True


async def _await_with_interest_budget(
    operation: Callable[[], Awaitable[Any]],
    started_at: float,
) -> Any:
    remaining = _remaining_interest_budget(started_at)
    if remaining <= 0:
        raise TimeoutError("interest distribution total time budget exhausted")
    return await asyncio.wait_for(operation(), timeout=remaining)


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


def validate_interest_groups(
    candidates: list[InterestEntityCandidate],
    groups: list[InterestGroup],
) -> list[InterestGroup]:
    """Keep only entity ids the backend actually returned and deduplicate claims."""

    valid_entity_ids = {candidate.entity_id for candidate in candidates}
    claimed_entity_ids: set[str] = set()
    validated_groups: dict[str, InterestGroup] = {}
    has_named_interest = False

    for group in groups:
        name = collapse_whitespace(group.name)
        if not name:
            continue

        has_named_interest = True
        accepted_entity_ids: list[str] = []
        for entity_id in group.entity_ids:
            if entity_id not in valid_entity_ids or entity_id in claimed_entity_ids:
                continue
            claimed_entity_ids.add(entity_id)
            accepted_entity_ids.append(entity_id)
        if not accepted_entity_ids:
            continue

        normalized_name = name.casefold()
        validated_group = validated_groups.get(normalized_name)
        if validated_group is None:
            validated_groups[normalized_name] = InterestGroup(
                name=name,
                entity_ids=accepted_entity_ids,
            )
        else:
            validated_group.entity_ids.extend(accepted_entity_ids)

    if has_named_interest and not validated_groups:
        raise ValueError("LLM returned interests without valid source entity IDs")

    return list(validated_groups.values())


def aggregate_interest_groups(
    candidates: list[InterestEntityCandidate],
    groups: list[InterestGroup],
    limit: int = INTEREST_DISPLAY_LIMIT,
) -> list[tuple[str, int]]:
    candidate_frequency = {
        candidate.entity_id: candidate.frequency for candidate in candidates
    }
    validated_groups = validate_interest_groups(candidates, groups)

    result = [
        (
            group.name,
            sum(candidate_frequency[entity_id] for entity_id in group.entity_ids),
        )
        for group in validated_groups
    ]
    result.sort(key=lambda item: (-item[1], collapse_whitespace(item[0]).casefold()))
    return result[:limit]


async def filter_interests_with_llm(
    llm: ProfileChatModel,
    candidates: list[InterestEntityCandidate],
    end_user_id: str,
    language: str = "zh",
    evidence: list[InterestStatementEvidence] | None = None,
    existing_interests: list[str] | None = None,
    timeout_seconds: float = INTEREST_DISTRIBUTION_LLM_TIMEOUT_SECONDS,
) -> InterestTags:
    """Group candidate entities into user interests via one structured call."""

    rendered_prompt = render_interest_filter_prompt(
        [candidate.model_dump() for candidate in candidates],
        language=language,
        evidence=[item.model_dump() for item in evidence] if evidence else None,
        existing_interests=existing_interests,
    )
    messages = [{"role": "user", "content": rendered_prompt}]
    started_at = time.monotonic()
    stage = "llm_fallback" if evidence else "llm_call"
    if timeout_seconds <= 0:
        raise TimeoutError(f"interest distribution {stage} has no remaining time budget")
    try:
        raw_response = await asyncio.wait_for(
            llm.call_structured(messages, InterestTags),
            timeout=timeout_seconds,
        )
        response = (
            raw_response
            if isinstance(raw_response, InterestTags)
            else InterestTags.model_validate(raw_response)
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
    return response


async def generate_interest_distribution(
    graph: GenerationGraphRepository,
    llm: ProfileChatModel,
    end_user_id: str,
    limit: int = INTEREST_DISPLAY_LIMIT,
    language: str = "zh",
) -> InterestDistributionGeneration:
    """Generate interests with the API's shared budget and degradation order."""

    started_at = time.monotonic()
    second_call_executed = False
    primary_items: list[tuple[str, int]] = []
    failure_stage = "input_validation"
    if not end_user_id or not end_user_id.strip():
        raise ValueError(
            "end_user_id is required. Please provide a valid end_user_id or user_id."
        )

    try:
        failure_stage = "candidate_query"
        try:
            candidate_records = await _await_with_interest_budget(
                lambda: graph.interest_candidates(
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
                    if _remaining_interest_budget(started_at) <= 0
                    else "candidate_query_failed"
                ),
            )
            raise
        failure_stage = "candidate_validation"
        candidates = [
            InterestEntityCandidate.model_validate(record)
            for record in candidate_records
        ]
        if _remaining_interest_budget(started_at) <= 0:
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
            return InterestDistributionGeneration(items=[], cacheable=True)

        primary_timeout = min(
            INTEREST_DISTRIBUTION_LLM_TIMEOUT_SECONDS,
            _remaining_interest_budget(started_at),
        )
        failure_stage = "primary_llm"
        try:
            interests = await _await_with_interest_budget(
                lambda: filter_interests_with_llm(
                    llm,
                    candidates,
                    end_user_id,
                    language=language,
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
                    if _remaining_interest_budget(started_at) <= 0
                    else "primary_llm_failed"
                ),
            )
            raise
        if _remaining_interest_budget(started_at) <= 0:
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
        validated_primary_groups = validate_interest_groups(
            candidates,
            interests.interests,
        )
        combined_groups = list(validated_primary_groups)
        primary_items = aggregate_interest_groups(
            candidates,
            validated_primary_groups,
            limit=INTEREST_DISPLAY_LIMIT,
        )
        if _remaining_interest_budget(started_at) <= 0:
            _log_interest_budget(
                end_user_id=end_user_id,
                stage="primary_validation",
                started_at=started_at,
                second_call_executed=second_call_executed,
                degradation_reason="total_budget_exhausted",
            )
            return InterestDistributionGeneration(
                items=primary_items[:limit],
                cacheable=False,
            )

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
                    evidence_records = await _await_with_interest_budget(
                        lambda: graph.interest_statement_evidence(
                            end_user_id,
                            entity_ids=[
                                candidate.entity_id for candidate in evidence_candidates
                            ],
                            per_entity_limit=INTEREST_EVIDENCE_PER_ENTITY_LIMIT,
                            limit=INTEREST_EVIDENCE_LIMIT,
                            max_chars=INTEREST_EVIDENCE_MAX_CHARS,
                        ),
                        started_at,
                    )
                    evidence = [
                        InterestStatementEvidence.model_validate(record)
                        for record in evidence_records
                    ]
                    if _remaining_interest_budget(started_at) <= 0:
                        _log_interest_budget(
                            end_user_id=end_user_id,
                            stage="statement_evidence",
                            started_at=started_at,
                            second_call_executed=second_call_executed,
                            degradation_reason="total_budget_exhausted",
                        )
                        return InterestDistributionGeneration(
                            items=primary_items[:limit],
                            cacheable=False,
                        )
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
                            remaining = _remaining_interest_budget(started_at)
                            if remaining < INTEREST_DISTRIBUTION_MIN_FALLBACK_TIMEOUT_SECONDS:
                                _log_interest_budget(
                                    end_user_id=end_user_id,
                                    stage="before_second_llm",
                                    started_at=started_at,
                                    second_call_executed=second_call_executed,
                                    degradation_reason="total_budget_exhausted",
                                )
                                return InterestDistributionGeneration(
                                    items=primary_items[:limit],
                                    cacheable=False,
                                )
                            second_call_executed = True
                            fallback_timeout = min(
                                INTEREST_DISTRIBUTION_LLM_TIMEOUT_SECONDS,
                                remaining,
                            )
                            failure_stage = "second_llm"
                            supplemental_interests = await _await_with_interest_budget(
                                lambda: filter_interests_with_llm(
                                    llm,
                                    fallback_candidates,
                                    end_user_id,
                                    language=language,
                                    evidence=evidence,
                                    existing_interests=[
                                        name for name, _ in primary_items
                                    ],
                                    timeout_seconds=fallback_timeout,
                                ),
                                started_at,
                            )
                            if _remaining_interest_budget(started_at) <= 0:
                                _log_interest_budget(
                                    end_user_id=end_user_id,
                                    stage="second_llm",
                                    started_at=started_at,
                                    second_call_executed=second_call_executed,
                                    degradation_reason="total_budget_exhausted",
                                )
                                return InterestDistributionGeneration(
                                    items=primary_items[:limit],
                                    cacheable=False,
                                )
                            combined_groups.extend(supplemental_interests.interests)
                except Exception as exc:
                    _log_interest_budget(
                        end_user_id=end_user_id,
                        stage="statement_fallback",
                        started_at=started_at,
                        second_call_executed=second_call_executed,
                        degradation_reason=(
                            "total_budget_exhausted"
                            if _remaining_interest_budget(started_at) <= 0
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
                    return InterestDistributionGeneration(
                        items=primary_items[:limit],
                        cacheable=False,
                    )

        failure_stage = "final_validation"
        items = aggregate_interest_groups(candidates, combined_groups, limit=limit)
        if _remaining_interest_budget(started_at) <= 0:
            _log_interest_budget(
                end_user_id=end_user_id,
                stage="final_validation",
                started_at=started_at,
                second_call_executed=second_call_executed,
                degradation_reason="total_budget_exhausted",
            )
            return InterestDistributionGeneration(
                items=primary_items[:limit],
                cacheable=False,
            )
        _log_interest_budget(
            end_user_id=end_user_id,
            stage="completed",
            started_at=started_at,
            second_call_executed=second_call_executed,
        )
        return InterestDistributionGeneration(items=items, cacheable=bool(items))
    except Exception as exc:
        fallback_items = primary_items[:limit]
        cacheable = not fallback_items
        logger.error(
            "interest_distribution stage=%s_failed end_user_id=%s language=%s "
            "elapsed=%.3fs second_call_executed=%s fallback=%s cacheable=%s "
            "error_type=%s error=%r",
            failure_stage,
            end_user_id,
            language,
            time.monotonic() - started_at,
            second_call_executed,
            "primary_items" if fallback_items else "empty",
            cacheable,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return InterestDistributionGeneration(
            items=fallback_items,
            cacheable=cacheable,
        )
