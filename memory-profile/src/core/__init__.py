"""Memory-read core: the domain and application layers of memory retrieval.

This package is the service's *core domain* for memory reading. It is deliberately
separate from ``src/services`` (analytics/profile reads) and follows a strict DDD
dependency direction:

    src.modules.memory_read   (transport: HTTP / SSE)
            │
            ▼
    src.core.application      (use cases: non-streaming read, streaming read)
            │
            ▼
    src.core.domain           (entities, value objects, errors, ports)
            ▲
            │ implements
    src.core.adapters         (PostgreSQL / Neo4j / Elasticsearch / Redis / model-service)

``src.core.composition`` is the only module allowed to see both sides; it wires
adapters into use cases from the process runtime. The import-boundary script
enforces the rules.
"""

__all__: list[str] = []