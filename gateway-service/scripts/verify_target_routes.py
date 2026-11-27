"""Reconcile the gateway route table with each target service's OpenAPI.

Fatal findings:
- dead entry: a route entry matches no target path (typo / retired prefix),
  checked only for services whose OpenAPI was supplied;
- uncovered path: a target business path matches no entry (would 404 externally).

Usage:
  uv run --project core/gateway-service python \
    core/gateway-service/scripts/verify_target_routes.py \
    --routes core/gateway-service/routes.json \
    --openapi file-service=k8s/openapi/file-service.json \
    --openapi application-service=http://127.0.0.1:8080/openapi.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run by path, not -m

from src.forward import rewrite_internal_path  # noqa: E402

# Surfaces the gateway never routes externally. Health/metrics/docs belong to
# the infrastructure network; /internal/v1/files is the file service's
# service-to-service API (called by application-service and the memory domain,
# not the public edge); /internal/v1/app-categories is consumed internally by
# the monolith only; /internal/v1/operator is the platform-admin BFF face,
# authenticated by its own service credential. The v2 upload paths are the
# memory domain's internal knowledge import; /internal/v1/invoke and
# /internal/v1/platform are model-service runtime/platform-admin faces;
# /internal/v1/{admin,catalog} are market-service admin/catalog faces — all
# reached by sibling services, never by the edge.
_EXEMPT_PREFIXES = (
    "/internal/v1/health", "/internal/v1/metrics", "/healthz", "/metrics",
    "/openapi.json", "/docs", "/redoc",
    "/internal/v1/files", "/internal/v1/app-categories", "/internal/v1/operator",
    "/internal/v2/chunks", "/internal/v2/files",
    "/internal/v1/invoke", "/internal/v1/platform",
    "/internal/v1/admin", "/internal/v1/catalog",
    # identity-service sibling faces: JWKS publication and the /internal/*
    # snapshot/verify/ACL API are consumed by the gateway and peer services,
    # never by the public edge.
    "/jwks", "/internal/user-snapshot", "/internal/api-key-verify",
    "/internal/acl-rules",
    # Sibling-only faces added for the P3-3 full sweep: identity renders
    # workspace model options/selection by calling model-service; ops-service
    # calls identity's SSO/tenant-admin/tenant-user/email-exists internals and
    # serves the home-page version read that application-service proxies.
    "/internal/v1/workspace-model-options",
    "/internal/v1/workspace-model-selection",
    "/internal/v1/home-page",
    "/internal/sso", "/internal/tenant-admins", "/internal/tenant-users",
    "/internal/users",
)


def _target_prefix(route: dict) -> str:
    if route.get("path_mode", "internal") == "preserve":
        return route["path_prefix"].rstrip("/")
    return rewrite_internal_path(route["path_prefix"]).rstrip("/")


def _matches(path: str, prefix: str) -> bool:
    # The runtime resolver uses plain startswith, so a broad family prefix such
    # as /sys/alert legitimately covers /sys/alert-events. Accept a hyphen
    # boundary for that case but not a bare character extension, so typos like
    # /internal/v1/storagex still surface as dead entries.
    return (path == prefix or path.startswith(prefix + "/")
            or path.startswith(prefix + "-"))


def _is_exempt(path: str) -> bool:
    return any(_matches(path, p) for p in _EXEMPT_PREFIXES)


def evaluate(routes: list[dict], openapi_by_service: dict[str, list[str]]) -> list[str]:
    problems: list[str] = []
    for route in routes:
        service = route["service"]
        if service not in openapi_by_service:
            # Dead-entry checking needs the target's own path list; a service
            # omitted from --openapi is out of scope, not dead by default.
            continue
        prefix = _target_prefix(route)
        paths = openapi_by_service[service]
        if not any(_matches(p, prefix) for p in paths):
            problems.append(
                f"dead entry: {route['path_prefix']} -> {prefix} has no path in {service}")
    for service, paths in openapi_by_service.items():
        service_routes = [r for r in routes if r["service"] == service]
        for path in paths:
            if _is_exempt(path):
                continue
            if not any(_matches(path, _target_prefix(r)) for r in service_routes):
                problems.append(f"uncovered path: {service} {path}")
    return problems


def _load_openapi(source: str) -> list[str]:
    if source.startswith("http://") or source.startswith("https://"):
        with urlopen(source, timeout=5) as resp:  # noqa: S310 - operator-supplied URL
            payload = json.loads(resp.read())
    else:
        payload = json.loads(Path(source).read_text(encoding="utf-8"))
    return sorted(payload.get("paths", {}))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--routes", required=True)
    parser.add_argument("--openapi", action="append", default=[],
                        metavar="SERVICE=SOURCE")
    args = parser.parse_args(argv)
    raw = json.loads(Path(args.routes).read_text(encoding="utf-8"))
    openapi_by_service = {}
    for item in args.openapi:
        service, source = item.split("=", 1)
        openapi_by_service[service] = _load_openapi(source)
    problems = evaluate(raw.get("routes", []), openapi_by_service)
    for problem in problems:
        print(problem)
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
