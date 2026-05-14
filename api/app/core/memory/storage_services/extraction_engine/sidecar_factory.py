"""SidecarStepFactory — decorator-based registry for sidecar (non-critical) steps.

New sidecar modules self-register via ``@SidecarStepFactory.register`` and are
automatically discovered and instantiated by the orchestrator without any
changes to orchestrator code.
"""

import logging
from enum import Enum
from typing import Any, Dict, List, Tuple, Type

from .steps.base import ExtractionStep, StepContext

logger = logging.getLogger(__name__)


class SidecarTiming(str, Enum):
    """Declares when a sidecar step runs relative to the main pipeline."""

    AFTER_STATEMENT = "after_statement"
    AFTER_TRIPLET = "after_triplet"


class SidecarStepFactory:
    """Factory that manages sidecar step registration and creation.

    Registry maps ``config_key`` → ``(step_class, timing)``.
    Adding a new sidecar only requires the ``@register`` decorator on the
    step class — no orchestrator modifications needed.
    """

    _registry: Dict[str, Tuple[Type[ExtractionStep], SidecarTiming]] = {}

    @classmethod
    def register(cls, config_key: str, timing: SidecarTiming):
        """Class decorator that registers a sidecar step.

        Args:
            config_key: Configuration flag name (e.g. ``"emotion_enabled"``).
                The step is instantiated only when this flag is ``True``.
            timing: When the sidecar runs relative to the main pipeline.

        Returns:
            The original class, unmodified.
        """

        def decorator(step_class: Type[ExtractionStep]):
            cls._registry[config_key] = (step_class, timing)
            logger.debug(
                "Registered sidecar '%s' (config_key=%s, timing=%s)",
                step_class.__name__,
                config_key,
                timing.value,
            )
            return step_class

        return decorator

    @classmethod
    def create_sidecars(
        cls, config: Any, context: StepContext
    ) -> Dict[SidecarTiming, List[ExtractionStep]]:
        """Instantiate enabled sidecar steps, grouped by timing.

        Args:
            config: Pipeline configuration object.  Each registered
                ``config_key`` is looked up via ``getattr(config, key, False)``.
            context: Shared :class:`StepContext` injected into every step.

        Returns:
            A dict keyed by :class:`SidecarTiming`, each value a list of
            instantiated sidecar steps whose config flag is ``True``.
        """
        result: Dict[SidecarTiming, List[ExtractionStep]] = {
            timing: [] for timing in SidecarTiming
        }
        for config_key, (step_class, timing) in cls._registry.items():
            if getattr(config, config_key, False):
                step = step_class(context)
                result[timing].append(step)
                logger.debug(
                    "Created sidecar '%s' (timing=%s)",
                    step_class.__name__,
                    timing.value,
                )
            else:
                logger.debug(
                    "Skipped sidecar '%s' (config_key=%s is disabled)",
                    step_class.__name__,
                    config_key,
                )
        return result

    @classmethod
    def clear_registry(cls) -> None:
        """Remove all registered sidecars.  Useful for testing."""
        cls._registry.clear()
