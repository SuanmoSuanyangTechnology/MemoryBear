"""Base task class and shared access to the synchronous runtime."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from celery import Task
from elasticsearch import Elasticsearch
from neo4j import Driver
from redis import Redis
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from src.runtime import get_runtime


class JobTask(Task):
    """Base class for memory-jobs tasks.

    Retry policy is transport-only: ``OperationalError`` (connection lost,
    server restarting) is safe to replay, while business errors are not — a
    task must decide those itself. ``acks_late`` pairs with this: the message
    is acknowledged after the task returns, so a crashed worker redelivers it.
    Tasks must therefore be idempotent or guard their own side effects.
    """

    abstract = True

    autoretry_for = (OperationalError,)
    retry_backoff = True
    retry_backoff_max = 600
    retry_jitter = True
    max_retries = 5
    acks_late = True

    @contextmanager
    def db_session(self) -> Iterator[Session]:
        """One committed unit of work; rolls back and re-raises on failure."""

        with get_runtime().postgres.session() as session:
            yield session

    @property
    def redis_client(self) -> Redis:
        return get_runtime().redis.client()

    @property
    def elasticsearch_client(self) -> Elasticsearch:
        return get_runtime().elasticsearch.client()

    @property
    def neo4j_driver(self) -> Driver:
        return get_runtime().neo4j.driver()
