from collections.abc import Iterable

from app.core.memory.storage.enums import MemoryNodeLabel, MemoryNodeType
from app.core.memory.storage.models import (
    FilterCondition,
    FilterOperator,
    NodeFilter,
)


def build_content_search_filters(
    labels: Iterable[MemoryNodeLabel],
    end_user_id: str,
) -> dict[MemoryNodeLabel, NodeFilter]:
    """Build per-label filters for memory content retrieval."""
    common_filter = NodeFilter.all_of(
        FilterCondition(field="end_user_id", value=end_user_id),
        FilterCondition(
            field="delete_at",
            operator=FilterOperator.EXISTS,
            value=False,
        ),
    )

    return {
        label: (
            NodeFilter.all_of(
                common_filter,
                FilterCondition(field="write_mode", value="fast"),
            )
            if label == MemoryNodeType.DIALOGUE
            else common_filter
        )
        for label in labels
    }
