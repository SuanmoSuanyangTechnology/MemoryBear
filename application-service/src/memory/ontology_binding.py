"""Remote ontology binding facade for the split application service.

Ontology tables are owned by the memory domain; this service must not read
them directly. While the memory module is loaded, the monolith registers an
in-process ``memory.ontology_binding`` plugin and exposes it over the internal
callback endpoints (``/api/internal/app-service/apps/ontology-*``); this
module is the remote counterpart registered under the same plugin name, so
callers keep using ``get_plugin(ONTOLOGY_BINDING_PLUGIN)``.

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

from ..infrastructure.upstream import MonolithUpstream, UpstreamUnavailableError

logger = logging.getLogger(__name__)

ONTOLOGY_BINDING_PLUGIN = "memory.ontology_binding"

_BATCH_PATH = "/api/internal/app-service/apps/ontology-batch"
_FILTER_PATH = "/api/internal/app-service/apps/ontology-filter"


class OntologyUpstreamError(RuntimeError):
    """The ontology callback channel is unconfigured or returned a bad envelope."""


def _dedupe(app_ids: Iterable[uuid.UUID]) -> list[uuid.UUID]:
    return list(dict.fromkeys(app_ids))


class RemoteOntologyBinder:
    """HTTP counterpart of the monolith ``SyncOntologyBinder``.

    ``uses_local_db`` tells callers this binder does not touch the local
    session; they must end their transaction before invoking it (no remote
    call inside a database transaction).
    """

    uses_local_db = False

    def __init__(self, upstream: MonolithUpstream) -> None:
        self._upstream = upstream

    def load_app_ontology_batch(
            self,
            db: Any,
            app_ids: Iterable[uuid.UUID],
    ) -> dict[uuid.UUID, dict] | None:
        """Batch-load the effective ontology sub-object per app.

        Returns None when the monolith reports the memory module is not
        loaded (community edition / License without the memory feature);
        ``db`` is unused and kept for signature parity with the local binder.
        """
        ids = _dedupe(app_ids)
        if not ids:
            return {}
        data = self._call(_BATCH_PATH, {"app_ids": [str(app_id) for app_id in ids]})
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
    ) -> set[uuid.UUID] | None:
        """Return the candidate apps matching the ontology filter.

        Returns None when the monolith reports the memory module is not
        loaded so callers can ignore the filter instead of returning an
        empty page.
        """
        ids = _dedupe(app_ids)
        if not ids:
            return set()
        data = self._call(_FILTER_PATH, {
            "app_ids": [str(app_id) for app_id in ids],
            "ontology_status": ontology_status,
            "field_search": field_search,
        })
        if not data.get("available"):
            return None
        raw_ids = data.get("app_ids")
        if not isinstance(raw_ids, list):
            raise OntologyUpstreamError("ontology-filter response is missing app_ids")
        try:
            return {uuid.UUID(str(raw_id)) for raw_id in raw_ids}
        except (TypeError, ValueError) as exc:
            raise OntologyUpstreamError("ontology-filter returned an invalid app id") from exc

    def _call(self, path: str, json_body: dict[str, Any]) -> dict[str, Any]:
        if not self._upstream.configured:
            raise OntologyUpstreamError("application service upstream is not configured")
        try:
            response = self._upstream.forward("POST", path, json_body=json_body)
        except UpstreamUnavailableError as exc:
            raise OntologyUpstreamError(str(exc)) from exc
        body = response.body if isinstance(response.body, dict) else {}
        if response.status_code != 200 or body.get("code") != 0:
            message = (
                body.get("msg") or body.get("error")
                or "ontology callback rejected the request"
            )
            raise OntologyUpstreamError(str(message))
        data = body.get("data")
        if not isinstance(data, dict):
            raise OntologyUpstreamError("ontology callback returned no data envelope")
        return data


__all__ = [
    "ONTOLOGY_BINDING_PLUGIN",
    "OntologyUpstreamError",
    "RemoteOntologyBinder",
]
