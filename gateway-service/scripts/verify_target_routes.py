"""Reconcile the gateway route table with each target service's OpenAPI.

Fatal findings:
- dead entry: a route entry matches no target path (typo / retired prefix),
  checked only for services whose OpenAPI was supplied;
- uncovered path: a target business path matches no entry (would 404 externally).
- missing operation: a declared KB upload does not reach a registered POST.

Usage:
  uv run python scripts/verify_target_routes.py \
    --routes routes.json \
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

from error_core.knowledge_upload import VERSIONED_UPLOAD_PATHS  # noqa: E402

from src.forward import StaticTargetResolver, TargetRoute, rewrite_internal_path  # noqa: E402

# Surfaces the gateway never routes externally. Health/metrics/docs belong to
# the infrastructure network; /internal/v1/files is the file service's
# service-to-service API (called by application-service and the memory domain,
# not the public edge); /internal/v1/app-categories is consumed internally by
# the monolith only; /internal/v1/operator is the platform-admin BFF face,
# authenticated by its own service credential. /internal/v1/invoke and
# /internal/v1/platform are model-service runtime/platform-admin faces;
# /internal/v1/{admin,catalog} are market-service admin/catalog faces — all
# reached by sibling services, never by the edge.
_EXEMPT_PREFIXES = (
    "/internal/v1/health", "/internal/v1/metrics", "/healthz", "/metrics",
    "/openapi.json", "/docs", "/redoc",
    "/internal/v1/files", "/internal/v1/app-categories", "/internal/v1/operator",
    "/internal/v1/invoke", "/internal/v1/platform",
    "/internal/v1/admin", "/internal/v1/catalog",
)


def _target_prefix(route: dict) -> str:
    if route.get("path_mode", "internal") == "preserve":
        return route["path_prefix"].rstrip("/")
    return rewrite_internal_path(route["path_prefix"]).rstrip("/")


def _matches(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _is_exempt(path: str) -> bool:
    return any(_matches(path, p) for p in _EXEMPT_PREFIXES)


def evaluate(routes: list[dict], openapi_by_service: dict[str, dict]) -> list[str]:
    problems: list[str] = []
    upload_paths = _check_kb_uploads(routes, openapi_by_service, problems)
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
            if _is_exempt(path) or (service == "kb" and path in upload_paths):
                continue
            if not any(_matches(path, _target_prefix(r)) for r in service_routes):
                problems.append(f"uncovered path: {service} {path}")
    return problems


def _check_kb_uploads(
    routes: list[dict], openapi_by_service: dict[str, dict], problems: list[str]
) -> set[str]:
    if "kb" not in openapi_by_service:
        return set()
    resolver = StaticTargetResolver([TargetRoute.model_validate(route) for route in routes])
    kb_paths = openapi_by_service["kb"]
    for external_path, expected in VERSIONED_UPLOAD_PATHS.items():
        route = resolver.resolve(external_path)
        if route is None or route.service != "kb":
            problems.append(f"missing operation: POST {external_path} has no KB route")
            continue
        target = route.target_path(external_path, "POST")
        if target != expected or "post" not in kb_paths.get(target, {}):
            problems.append(
                f"missing operation: POST {external_path} -> {target}; expected KB POST {expected}"
            )
    return set(VERSIONED_UPLOAD_PATHS.values())


def _load_openapi(source: str) -> dict:
    if source.startswith("http://") or source.startswith("https://"):
        with urlopen(source, timeout=5) as resp:  # noqa: S310 - operator-supplied URL
            payload = json.loads(resp.read())
    else:
        payload = json.loads(Path(source).read_text(encoding="utf-8"))
    return payload.get("paths", {})


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
