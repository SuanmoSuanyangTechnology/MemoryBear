"""Synchronous Neo4j lifecycle for Celery worker processes."""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal

from neo4j import Driver, GraphDatabase, Session

from src.config import JobsSettings

_DISABLED_NOTIFICATION_CLASSIFICATIONS = ["UNRECOGNIZED"]

logger = logging.getLogger(__name__)

AccessMode = Literal["READ", "WRITE"]


class Neo4jManager:
    """Own one blocking Neo4j driver per worker process.

    The driver is thread-safe and maintains its own connection pool, so tasks
    open short-lived sessions (one per unit of work) over the shared driver.
    """

    def __init__(self, settings: JobsSettings) -> None:
        self._settings = settings
        self._driver: Driver | None = None
        self._lock = threading.Lock()

    @property
    def initialized(self) -> bool:
        return self._driver is not None

    @property
    def database(self) -> str | None:
        return self._settings.neo4j_database

    def driver(self) -> Driver:
        if self._driver is not None:
            return self._driver
        with self._lock:
            if self._driver is None:
                self._driver = GraphDatabase.driver(
                    self._settings.neo4j_uri,
                    auth=(self._settings.neo4j_username, self._settings.neo4j_password),
                    max_connection_pool_size=self._settings.neo4j_max_pool_size,
                    connection_acquisition_timeout=(
                        self._settings.neo4j_acquisition_timeout_seconds
                    ),
                    max_connection_lifetime=(
                        self._settings.neo4j_max_connection_lifetime_seconds
                    ),
                    connection_timeout=self._settings.neo4j_connection_timeout_seconds,
                    notifications_disabled_classifications=(
                        _DISABLED_NOTIFICATION_CLASSIFICATIONS
                    ),
                )
            return self._driver

    @contextmanager
    def session(self, access_mode: AccessMode = "WRITE") -> Iterator[Session]:
        with self.driver().session(
            database=self.database, default_access_mode=access_mode
        ) as session:
            yield session

    def execute_query(self, query: str, **parameters: Any) -> list[dict[str, Any]]:
        """Run one auto-commit query and return its records as dictionaries."""

        with self.session(access_mode="WRITE") as session:
            return session.run(query, **parameters).data()

    def ping(self) -> bool:
        self.driver().verify_connectivity()
        return True

    def pool_snapshot(self) -> dict[str, object] | None:
        """Return in-memory connection-pool counts without issuing a query."""

        driver = self._driver
        if driver is None:
            return None
        try:
            pool = getattr(driver, "_pool", None)
            connections = getattr(pool, "connections", None)
            if not isinstance(connections, dict):
                return None
            total = 0
            in_use = 0
            for address_connections in connections.values():
                for connection in address_connections:
                    total += 1
                    if getattr(connection, "in_use", False):
                        in_use += 1
            return {
                "total": total,
                "in_use": in_use,
                "max_size": self._settings.neo4j_max_pool_size,
            }
        except Exception as exc:
            logger.warning(
                "Failed to read Neo4j pool state: %s",
                type(exc).__name__,
                extra={"event": "neo4j_pool_snapshot_failed"},
            )
            return None

    def reset_after_fork(self) -> None:
        """Discard connections inherited from the parent process."""

        self.close()

    def close(self) -> None:
        driver = self._driver
        self._driver = None
        if driver is not None:
            driver.close()
