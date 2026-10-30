"""Process composition root.

The container exposes one facade per task domain; shared adapters stay private.
Tasks ask for a use case through ``get_container().<domain>`` and never assemble
an adapter, a Unit of Work or a Neo4j reader themselves.
"""

from src.composition.container import Container, get_container, set_container

__all__ = ["Container", "get_container", "set_container"]
