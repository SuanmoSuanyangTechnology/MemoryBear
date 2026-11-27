"""Remote ontology binding facade for the split application service.

Ontology tables and their app bindings are owned by the memory domain; this
service must not read them directly. Community deployments have no
memory-enterprise at all; enterprise deployments reach its app-service
internal routes (``/internal/v1/apps/ontology-*``) with the scoped
application-service token. The facade is registered under the
``memory.ontology_binding`` plugin name, so callers keep using
``get_plugin(ONTOLOGY_BINDING_PLUGIN)``.

The enterprise wiring registers it through
``enterprise_ext.app.register_app_plugins``. Open-source builds leave the
registry empty: list endpoints omit the ontology sub-object and the ontology
filters degrade to a logged warning.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from typing import Any

from ..integrations.memory_enterprise import (
    MemoryEnterpriseClient,
    MemoryEnterpriseProtocolError,
    MemoryEnterpriseUnavailableError,
)

logger = logging.getLogger(__name__)

ONTOLOGY_BINDING_PLUGIN = "memory.ontology_binding"


class OntologyUpstreamError(RuntimeError):
    """The ontology service is unconfigured or rejected the request.

    ``code`` carries the business code when memory-enterprise answered with a
    business error envelope, so callers can pass it through to clients;
    transport and protocol failures leave it as None.
    """

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


def _dedupe(app_ids: Iterable[uuid.UUID]) -> list[uuid.UUID]:
    return list(dict.fromkeys(app_ids))


class RemoteOntologyBinder:
    """Memory-enterprise counterpart of the monolith ``SyncOntologyBinder``.

    ``uses_local_db`` tells callers this binder does not touch the local
    session; they must end their transaction before invoking it (no remote
    call inside a database transaction).
    """

    uses_local_db = False

    def __init__(self, client: MemoryEnterpriseClient) -> None:
        self._client = client

    def load_app_ontology_batch(
            self,
            db: Any,
            app_ids: Iterable[uuid.UUID],
            tenant_id: uuid.UUID | None = None,
    ) -> dict[uuid.UUID, dict] | None:
        """Batch-load the effective ontology sub-object per app.

        Returns None when the memory-enterprise channel is unconfigured
        (community edition); ``db`` is unused and kept for signature parity
        with the local binder.
        """
        ids = _dedupe(app_ids)
        if not ids:
            return {}
        if not self._client.configured:
            return None
        try:
            envelope = self._client.ontology_batch(app_ids=ids, tenant_id=tenant_id)
        except (MemoryEnterpriseUnavailableError, MemoryEnterpriseProtocolError) as exc:
            raise OntologyUpstreamError(str(exc)) from exc
        data = self._data(envelope, "ontology-batch")
        if not data.get("available"):
            return None
        payload = data.get("ontology")
        if not isinstance(payload, dict):
            raise OntologyUpstreamError("ontology-batch response is missing the ontology map")
        result: dict[uuid.UUID, dict] = {}
        for raw_app_id, info in payload.items():
            try:
                app_id = uuid.UUID(str(raw_app_id))
            except (TypeError, ValueError) as exc:
                raise OntologyUpstreamError(
                    f"invalid app id in ontology map: {raw_app_id}"
                ) from exc
            if not isinstance(info, dict):
                raise OntologyUpstreamError(
                    f"invalid ontology payload for app: {raw_app_id}"
                )
            result[app_id] = info
        return result

    def filter_app_ids(
            self,
            app_ids: Iterable[uuid.UUID],
            ontology_status: str | None,
            field_search: str | None,
            tenant_id: uuid.UUID | None = None,
    ) -> set[uuid.UUID] | None:
        """Return the candidate apps matching the ontology filter.

        Returns None when the memory-enterprise channel is unconfigured so
        callers can ignore the filter instead of returning an empty page.
        """
        ids = _dedupe(app_ids)
        if not ids:
            return set()
        if not self._client.configured:
            return None
        try:
            envelope = self._client.ontology_filter(
                app_ids=ids,
                tenant_id=tenant_id,
                ontology_status=ontology_status,
                field_search=field_search,
            )
        except (MemoryEnterpriseUnavailableError, MemoryEnterpriseProtocolError) as exc:
            raise OntologyUpstreamError(str(exc)) from exc
        data = self._data(envelope, "ontology-filter")
        if not data.get("available"):
            return None
        raw_ids = data.get("app_ids")
        if not isinstance(raw_ids, list):
            raise OntologyUpstreamError("ontology-filter response is missing app_ids")
        try:
            return {uuid.UUID(str(raw_id)) for raw_id in raw_ids}
        except (TypeError, ValueError) as exc:
            raise OntologyUpstreamError("ontology-filter returned an invalid app id") from exc

    def bind_app(
            self,
            app_id: uuid.UUID,
            tenant_id: uuid.UUID,
            ontology_id: uuid.UUID,
    ) -> None:
        """Write the first binding for a just-created app.

        Fail-loud: the caller already committed the local app row, so an
        unusable channel or a rejected binding must surface to the caller
        (which compensates) instead of being swallowed. The binder is only
        registered when the channel is configured, so the client raises
        ``MemoryEnterpriseUnavailableError`` for the unconfigured case too.
        """
        try:
            envelope = self._client.ontology_bind(
                app_id=app_id,
                tenant_id=tenant_id,
                ontology_id=ontology_id,
            )
        except (MemoryEnterpriseUnavailableError, MemoryEnterpriseProtocolError) as exc:
            raise OntologyUpstreamError(str(exc)) from exc
        data = self._data(envelope, "ontology-bind")
        if not data.get("available"):
            raise OntologyUpstreamError("ontology binding is unavailable")

    def unbind_app(self, app_id: uuid.UUID, tenant_id: uuid.UUID | None = None) -> None:
        """Soft-delete the app's active binding after the local delete commit.

        Unlike ``bind_app``, an unconfigured channel is not an error: the
        community edition has no memory domain, hence no binding to remove,
        and the delete may proceed. A configured-but-unreachable channel or a
        rejected unbind still fails loud so the caller can compensate. The
        call is idempotent.
        """
        if not self._client.configured:
            logger.warning(
                "memory enterprise is not configured; skipping unbind",
                extra={"app_id": str(app_id)},
            )
            return
        try:
            envelope = self._client.ontology_unbind(app_id=app_id, tenant_id=tenant_id)
        except (MemoryEnterpriseUnavailableError, MemoryEnterpriseProtocolError) as exc:
            raise OntologyUpstreamError(str(exc)) from exc
        data = self._data(envelope, "ontology-unbind")
        if not data.get("available"):
            logger.warning(
                "ontology module unavailable; skipping unbind",
                extra={"app_id": str(app_id)},
            )

    @staticmethod
    def _data(envelope: dict[str, Any], operation: str) -> dict[str, Any]:
        if envelope.get("code") != 0:
            message = envelope.get("msg")
            raise OntologyUpstreamError(
                message if isinstance(message, str) and message
                else f"{operation} was rejected by memory enterprise",
                code=envelope.get("code"),
            )
        data = envelope.get("data")
        if not isinstance(data, dict):
            raise OntologyUpstreamError(f"{operation} returned no data envelope")
        return data


__all__ = [
    "ONTOLOGY_BINDING_PLUGIN",
    "OntologyUpstreamError",
    "RemoteOntologyBinder",
]
