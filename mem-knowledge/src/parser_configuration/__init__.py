"""Public conversion boundary for saved knowledge and document configuration."""

from .conversion import (
    KnowledgeContext,
    from_api,
    from_legacy,
    new_configuration,
    to_api,
    to_legacy,
    to_runtime,
)
from .entities import ConfigurationError, DocumentConfiguration, KnowledgeConfiguration
from .fields import Scope

__all__ = [
    "ConfigurationError",
    "DocumentConfiguration",
    "KnowledgeConfiguration",
    "KnowledgeContext",
    "Scope",
    "from_api",
    "from_legacy",
    "new_configuration",
    "to_api",
    "to_legacy",
    "to_runtime",
]
