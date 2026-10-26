"""Synchronous Elasticsearch lifecycle for Celery worker processes."""

from __future__ import annotations

import logging
import threading

from elasticsearch import Elasticsearch

from src.config import JobsSettings

logger = logging.getLogger(__name__)


class ElasticsearchManager:
    """Own one blocking Elasticsearch client per worker process.

    The sync transport is thread-safe and keeps a connection pool per node, so
    a threads-pool worker shares one client.
    """

    def __init__(self, settings: JobsSettings) -> None:
        self._settings = settings
        self._client: Elasticsearch | None = None
        self._lock = threading.Lock()

    @property
    def initialized(self) -> bool:
        return self._client is not None

    def client(self) -> Elasticsearch:
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is None:
                options: dict[str, object] = {
                    "hosts": self._settings.elasticsearch_hosts,
                    "request_timeout": self._settings.elasticsearch_request_timeout_seconds,
                    "retry_on_timeout": self._settings.elasticsearch_retry_on_timeout,
                    "max_retries": self._settings.elasticsearch_max_retries,
                    "connections_per_node": self._settings.elasticsearch_connections_per_node,
                    "verify_certs": self._settings.elasticsearch_verify_certs,
                }
                if self._settings.elasticsearch_username:
                    options["basic_auth"] = (
                        self._settings.elasticsearch_username,
                        self._settings.elasticsearch_password,
                    )
                if self._settings.elasticsearch_ca_certs:
                    options["ca_certs"] = self._settings.elasticsearch_ca_certs
                self._client = Elasticsearch(**options)
            return self._client

    def ping(self) -> bool:
        """Probe with a short, single-attempt request.

        The shared client uses the (possibly long) retry policy for task
        traffic, so a probe must override it per request; otherwise an
        unreachable node keeps a health check busy for minutes.
        """

        client = self.client()
        options = getattr(client, "options", None)
        if callable(options):
            probe = options(
                request_timeout=self._settings.healthcheck_timeout_seconds,
                max_retries=0,
            )
            return bool(probe.ping())
        return bool(client.ping())

    def pool_snapshot(self) -> dict[str, object] | None:
        """Return transport node counts without issuing a request."""

        client = self._client
        if client is None:
            return None
        try:
            transport = getattr(client, "transport", None) or getattr(client, "_transport", None)
            node_pool = getattr(transport, "node_pool", None)
            if node_pool is None:
                return None
            return {"nodes": len(node_pool), "alive": len(node_pool.all())}
        except Exception as exc:
            logger.warning(
                "Failed to read Elasticsearch pool state: %s",
                type(exc).__name__,
                extra={"event": "elasticsearch_pool_snapshot_failed"},
            )
            return None

    def reset_after_fork(self) -> None:
        """Discard connections inherited from the parent process."""

        self.close()

    def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            client.close()
