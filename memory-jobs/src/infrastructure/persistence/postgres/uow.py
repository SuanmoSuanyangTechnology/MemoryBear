"""SQLAlchemy Unit of Work.

One session per scope. A read scope ends with a rollback (no writes are
possible); a write scope commits on success and rolls back on any exception.
Repositories are constructed per scope, so they can never outlive the session
they were bound to.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session, sessionmaker

from src.application.ports.uow import Repositories
from src.infrastructure.persistence.postgres.end_user_repository import (
    SqlAlchemyEndUserDirectoryRepository,
)
from src.infrastructure.persistence.postgres.insight_repository import (
    SqlAlchemyMemoryInsightRepository,
)
from src.infrastructure.persistence.postgres.model_config_repository import (
    SqlAlchemyModelConfigRepository,
)
from src.infrastructure.persistence.postgres.profile_refresh_repository import (
    SqlAlchemyProfileRefreshReader,
)
from src.infrastructure.persistence.postgres.summary_repository import (
    SqlAlchemyUserSummaryRepository,
)
from src.infrastructure.persistence.postgres.workspace_repository import (
    SqlAlchemyWorkspaceRepository,
)


class SqlAlchemyUnitOfWork:
    """Create scoped repository bundles from a session factory."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @contextmanager
    def read(self) -> Iterator[Repositories]:
        with self._scope(commit=False) as session:
            yield self._repositories(session)

    @contextmanager
    def transaction(self) -> Iterator[Repositories]:
        with self._scope(commit=True) as session:
            yield self._repositories(session)

    @contextmanager
    def _scope(self, *, commit: bool) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            if commit:
                session.commit()
            else:
                session.rollback()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def _repositories(session: Session) -> Repositories:
        return Repositories(
            workspaces=SqlAlchemyWorkspaceRepository(session),
            model_configs=SqlAlchemyModelConfigRepository(session),
            end_users=SqlAlchemyEndUserDirectoryRepository(session),
            profile_refresh=SqlAlchemyProfileRefreshReader(session),
            insights=SqlAlchemyMemoryInsightRepository(session),
            summaries=SqlAlchemyUserSummaryRepository(session),
        )
