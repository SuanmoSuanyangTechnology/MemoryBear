"""Queue names shared with the API's Celery producers.

Workers are started per queue (``--queues=...``), so these constants are the
contract between task routing in this service and the deployment manifests.
Keep the values identical to ``api/app/celery_app.py``: producers in the API
route by these exact names.
"""

from __future__ import annotations

from enum import StrEnum


class QueueName(StrEnum):
    """Logical queues the API already produces into."""

    MEMORY = "memory_tasks"
    MEMORY_FAST = "memory_fast_tasks"
    MEMORY_HEAVY = "memory_heavy_tasks"
    MEMORY_PROJECTION = "memory_projection"
    DOCUMENT = "document_tasks"
    GRAPH_RAG = "graphrag_tasks"
    PERIODIC = "periodic_tasks"
    REFLECTION = "reflection_tasks"
    WORKFLOW_TRIGGER = "workflow_trigger_tasks"


DEFAULT_QUEUE = QueueName.MEMORY
