"""Pure interest grouping and aggregation rules.

Ported from the API helper: only entity ids the backend actually returned are
accepted, each entity is claimed once, group names are merged case-insensitively,
and the display list is ordered by summed frequency then name.
"""

from __future__ import annotations

from collections.abc import Sequence

from src.domain.interest.model import Interest, InterestGroup
from src.domain.interest.read_models import InterestCandidate
from src.domain.shared.errors import InvalidValue
from src.domain.shared.text import collapse_whitespace

INTEREST_DISPLAY_LIMIT = 5


def validate_interest_groups(
    candidates: Sequence[InterestCandidate],
    groups: Sequence[InterestGroup],
) -> list[InterestGroup]:
    """Keep only entity ids the backend returned and deduplicate claims."""

    valid_entity_ids = {candidate.entity_id for candidate in candidates}
    claimed_entity_ids: set[str] = set()
    validated_groups: dict[str, tuple[str, list[str]]] = {}
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
        existing = validated_groups.get(normalized_name)
        if existing is None:
            validated_groups[normalized_name] = (name, accepted_entity_ids)
        else:
            existing[1].extend(accepted_entity_ids)

    if has_named_interest and not validated_groups:
        raise InvalidValue(
            "LLM returned interests without valid source entity IDs"
        )

    return [
        InterestGroup(name=name, entity_ids=tuple(entity_ids))
        for name, entity_ids in validated_groups.values()
    ]


def aggregate_interest_groups(
    candidates: Sequence[InterestCandidate],
    groups: Sequence[InterestGroup],
    limit: int = INTEREST_DISPLAY_LIMIT,
) -> list[Interest]:
    candidate_frequency = {
        candidate.entity_id: candidate.frequency for candidate in candidates
    }
    validated_groups = validate_interest_groups(candidates, groups)

    result = [
        Interest(
            name=group.name,
            frequency=sum(
                candidate_frequency[entity_id] for entity_id in group.entity_ids
            ),
        )
        for group in validated_groups
    ]
    result.sort(key=lambda item: (-item.frequency, item.name.casefold()))
    return result[:limit]
