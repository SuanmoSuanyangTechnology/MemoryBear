from dataclasses import dataclass

from app.core.memory.storage.enums import MemoryNodeLabel
from app.core.memory.storage.models.filter import NodeFilter
from app.core.memory.storage.models.projection import NodeProjection


@dataclass(frozen=True, slots=True)
class NodeSearchSpec:
    """One label-specific search in a provider batch request."""

    label: MemoryNodeLabel
    node_filter: NodeFilter
    projection: NodeProjection | None = None
