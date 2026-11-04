"""External catalog framework.

Importing the package registers the built-in resource adapters on the shared
``default_registry``; each adapter module appends one import line to
``adapters/__init__.py``.
"""

from . import adapters  # noqa: F401  (registers built-in adapters)

__all__ = ["adapters"]
