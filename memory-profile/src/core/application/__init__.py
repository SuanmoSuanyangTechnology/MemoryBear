"""Application layer of the memory-read core.

Use cases orchestrate the domain ports: they may import ``src.core.domain``,
the standard library and third-party libraries (``pydantic``, ``jinja2``,
``langchain-core``) but never adapters, infrastructure or transport modules.
"""

__all__: list[str] = []