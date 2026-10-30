"""Services genuinely shared by more than one task domain.

Only cross-domain concerns live here: settings, the unit of work, the chat-model
factory, the prompt catalog and the unit publisher. An adapter that serves a
single domain is assembled by that domain's own ``build_*_dependencies`` (in
``profile_cache.py`` / ``interest.py``) from the runtime's lazy clients, so a
domain owns everything it uses and removing it leaves nothing behind.

Every adapter here is stateless or holds only a lazy client provider, so one
instance is safe to share across the worker's threads.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.application.ports.publishing import UnitPublisher
from src.config import JobsSettings
from src.infrastructure.llm.model_service_factory import ModelServiceChatModelFactory
from src.infrastructure.persistence.postgres.uow import SqlAlchemyUnitOfWork
from src.infrastructure.prompts.jinja_prompt_catalog import JinjaPromptCatalog
from src.runtime import JobsRuntime


@dataclass(frozen=True, slots=True)
class SharedDependencies:
    """Process-wide services every domain wiring may use."""

    settings: JobsSettings
    unit_of_work: SqlAlchemyUnitOfWork
    chat_models: ModelServiceChatModelFactory
    prompts: JinjaPromptCatalog
    publisher: UnitPublisher


def _celery_publisher() -> UnitPublisher:
    # Celery is the delivery mechanism and lives in the interface layer; the
    # import stays local so loading the task package cannot cycle through us.
    from src.interfaces.tasks.publishing import CeleryUnitPublisher

    return CeleryUnitPublisher()


def build_shared_dependencies(runtime: JobsRuntime) -> SharedDependencies:
    """Construct the services shared by every domain wiring.

    Connections stay lazy inside the runtime's clients; nothing here connects.
    """

    return SharedDependencies(
        settings=runtime.settings,
        unit_of_work=SqlAlchemyUnitOfWork(runtime.postgres.session_factory()),
        chat_models=ModelServiceChatModelFactory(runtime.model_service),
        prompts=JinjaPromptCatalog(),
        publisher=_celery_publisher(),
    )
