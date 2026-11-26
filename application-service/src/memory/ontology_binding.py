"""Remote ontology binding facade backed only by memory-enterprise."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from ..infrastructure.upstream import MemoryEnterpriseUpstream, UpstreamUnavailableError

ONTOLOGY_BINDING_PLUGIN = "memory.ontology_binding"

_BATCH_PATH = "/internal/v1/apps/ontology-batch"
_FILTER_PATH = "/internal/v1/apps/ontology-filter"
_BIND_PATH = "/internal/v1/apps/ontology-bind"
_UNBIND_PATH = "/internal/v1/apps/ontology-unbind"
_MAX_APP_IDS = 1000


class OntologyUpstreamError(RuntimeError):
    """memory-enterprise rejected the request or returned an invalid response."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


def _dedupe(app_ids: Iterable[uuid.UUID]) -> list[uuid.UUID]:
    return list(dict.fromkeys(app_ids))


class RemoteOntologyBinder:
    """HTTP counterpart of the ontology binding facade in memory-enterprise.

    ``uses_local_db`` tells callers to finish their local transaction before the
    remote call. Unbind always needs the tenant because memory-enterprise checks
    application ownership at its boundary.
    """

    uses_local_db = False
    requires_unbind_tenant = True

    def __init__(self, upstream: MemoryEnterpriseUpstream) -> None:
        self._upstream = upstream

    @staticmethod
    def _chunks(ids: list[uuid.UUID]) -> Iterable[list[uuid.UUID]]:
        for offset in range(0, len(ids), _MAX_APP_IDS):
            yield ids[offset:offset + _MAX_APP_IDS]

    def load_app_ontology_batch(
        self,
        db: Any,
        app_ids: Iterable[uuid.UUID],
    ) -> dict[uuid.UUID, dict]:
        """Batch-load the effective ontology sub-object per app."""

        del db
        ids = _dedupe(app_ids)
        if not ids:
            return {}
        result: dict[uuid.UUID, dict] = {}
        for chunk in self._chunks(ids):
            data = self._call(_BATCH_PATH, {"app_ids": [str(app_id) for app_id in chunk]})
            self._require_available(data)
            payload = data.get("ontology")
            if not isinstance(payload, dict):
                raise OntologyUpstreamError(
                    "ontology-batch response is missing the ontology map"
                )
            for raw_app_id, info in payload.items():
                try:
                    app_id = uuid.UUID(str(raw_app_id))
                except (TypeError, ValueError) as exc:
                    raise OntologyUpstreamError(
                        f"invalid app id in ontology map: {raw_app_id}"
                    ) from exc
                if app_id not in chunk:
                    raise OntologyUpstreamError(
                        f"ontology-batch returned an app outside the candidates: {raw_app_id}"
                    )
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
    ) -> set[uuid.UUID]:
        """Return the candidate apps matching the ontology filter."""

        ids = _dedupe(app_ids)
        if not ids:
            return set()
        matched: set[uuid.UUID] = set()
        for chunk in self._chunks(ids):
            data = self._call(
                _FILTER_PATH,
                {
                    "app_ids": [str(app_id) for app_id in chunk],
                    "ontology_status": ontology_status,
                    "field_search": field_search,
                },
            )
            self._require_available(data)
            raw_ids = data.get("app_ids")
            if not isinstance(raw_ids, list):
                raise OntologyUpstreamError(
                    "ontology-filter response is missing app_ids"
                )
            try:
                chunk_matches = {uuid.UUID(str(raw_id)) for raw_id in raw_ids}
            except (TypeError, ValueError) as exc:
                raise OntologyUpstreamError(
                    "ontology-filter returned an invalid app id"
                ) from exc
            if not chunk_matches.issubset(set(chunk)):
                raise OntologyUpstreamError(
                    "ontology-filter returned ids outside the candidates"
                )
            matched.update(chunk_matches)
        return matched

    def bind_app(
        self,
        app_id: uuid.UUID,
        tenant_id: uuid.UUID,
        ontology_id: uuid.UUID,
    ) -> None:
        """Write the first binding for a just-created app."""

        data = self._call(
            _BIND_PATH,
            {
                "app_id": str(app_id),
                "tenant_id": str(tenant_id),
                "ontology_id": str(ontology_id),
            },
        )
        self._require_available(data)
        if data.get("bound") is not True:
            raise OntologyUpstreamError("ontology binding was not confirmed")

    def unbind_app(self, app_id: uuid.UUID, tenant_id: uuid.UUID | None = None) -> None:
        """Soft-delete the app's active binding after the local delete commit."""

        if tenant_id is None:
            raise OntologyUpstreamError("tenant id is required for ontology unbind")
        data = self._call(
            _UNBIND_PATH,
            {"app_id": str(app_id), "tenant_id": str(tenant_id)},
        )
        self._require_available(data)
        if data.get("unbound") is not True:
            raise OntologyUpstreamError("ontology unbind was not confirmed")

    @staticmethod
    def _require_available(data: dict[str, Any]) -> None:
        if data.get("available") is not True:
            raise OntologyUpstreamError(
                "memory-enterprise returned an unavailable ontology contract"
            )

    def _call(self, path: str, json_body: dict[str, Any]) -> dict[str, Any]:
        if not self._upstream.configured:
            raise OntologyUpstreamError("memory-enterprise upstream is not configured")
        attempts = 2 if path in {_BIND_PATH, _UNBIND_PATH} else 1
        for attempt in range(attempts):
            try:
                response = self._upstream.forward("POST", path, json_body=json_body)
            except UpstreamUnavailableError as exc:
                if attempt + 1 == attempts:
                    raise OntologyUpstreamError(str(exc)) from exc
                continue
            if response.status_code >= 500 and attempt + 1 < attempts:
                continue
            break
        body = response.body if isinstance(response.body, dict) else {}
        if response.status_code != 200 or body.get("code") != 0:
            message = (
                body.get("msg")
                or body.get("error")
                or "memory-enterprise rejected the ontology request"
            )
            raw_code = body.get("code")
            raise OntologyUpstreamError(
                str(message), code=raw_code if isinstance(raw_code, int) else None
            )
        data = body.get("data")
        if not isinstance(data, dict):
            raise OntologyUpstreamError(
                "memory-enterprise returned no ontology data envelope"
            )
        return data


__all__ = [
    "ONTOLOGY_BINDING_PLUGIN",
    "OntologyUpstreamError",
    "RemoteOntologyBinder",
]
