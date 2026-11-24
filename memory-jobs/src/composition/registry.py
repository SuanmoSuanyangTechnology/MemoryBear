"""The task-domain registry the container assembles.

This tuple is the single list of task domains: adding a domain means adding its
``build_*_wiring`` here, and ``Container`` never changes. The order is the
construction order; each ``name`` is the attribute tasks reach through
``get_container().<name>``. ``scripts/check_import_boundaries.py`` derives its
allowlist by parsing this tuple, so a new domain needs no second list.

Every ``build`` must stay assembly-only: construct adapters and facades, but
never open a connection, query, or block. Creating a lazy client object is fine
even when it initializes a driver or a pool (for example SQLAlchemy's
``create_engine`` opens no connection until first use), so ``build`` may hand a
sessionmaker to the Unit of Work; it must not perform network I/O. The container
builds all registered modules eagerly at startup, so one build that blocks would
delay or break every task, not just its own domain.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.composition.admission import build_admission_wiring
from src.composition.emotion import build_emotion_wiring
from src.composition.forgetting import build_forgetting_wiring
from src.composition.interest import build_interest_wiring
from src.composition.metadata_migration import build_metadata_migration_wiring
from src.composition.outbox import build_outbox_wiring
from src.composition.profile_cache import build_profile_cache_wiring
from src.composition.rag_profile import build_rag_profile_wiring
from src.composition.shared import SharedDependencies
from src.composition.working_memory import build_working_memory_wiring
from src.runtime import JobsRuntime


@dataclass(frozen=True, slots=True)
class DomainModule:
    """One task domain: the container attribute name and how to build it."""

    name: str
    #: Pure assembly: adapters and facades only, no I/O (see the module docstring).
    build: Callable[[JobsRuntime, SharedDependencies], Any]


MODULES: tuple[DomainModule, ...] = (
    DomainModule(name="rag_profile", build=build_rag_profile_wiring),
    DomainModule(name="emotion", build=build_emotion_wiring),
    DomainModule(name="forgetting", build=build_forgetting_wiring),
    DomainModule(name="working_memory", build=build_working_memory_wiring),
    DomainModule(name="profile_cache", build=build_profile_cache_wiring),
    DomainModule(name="metadata_migration", build=build_metadata_migration_wiring),
    DomainModule(name="interest", build=build_interest_wiring),
    DomainModule(name="admission", build=build_admission_wiring),
    DomainModule(name="outbox", build=build_outbox_wiring),
)
