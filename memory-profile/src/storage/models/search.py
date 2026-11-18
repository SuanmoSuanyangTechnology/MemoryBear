from dataclasses import dataclass

from src.storage.enums import MemoryNodeLabel
from src.storage.models.filter import NodeFilter
from src.storage.models.projection import NodeProjection


@dataclass(frozen=True, slots=True)
class NodeSearchSpec:
    """One label-specific search in a provider batch request."""

    label: MemoryNodeLabel
    node_filter: NodeFilter
    projection: NodeProjection | None = None
