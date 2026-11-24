"""Pure domain layer of the memory-read core.

Only the standard library, ``pydantic`` and modules inside this package may be
imported here. No SQLAlchemy, Neo4j driver, Redis client, FastAPI or runtime
imports are allowed — the boundary script enforces it.
"""

__all__: list[str] = []