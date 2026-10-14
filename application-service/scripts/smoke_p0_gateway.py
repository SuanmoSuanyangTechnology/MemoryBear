#!/usr/bin/env python
"""Gateway-wiring smoke for the application service (zero-traffic mechanism check).

Runs six checks against a dev stack (gateway, identity, Redis and this service
up; ``kubectl port-forward`` is enough for the localhost URLs). No production
traffic is moved by this script - the Ingress path list decides if and when the
prefix is actually cut over; here only the mechanism is exercised end to end.

Checks:
  1. routes.json (repo file) claims /api/apps for service/aud application-service
  2. application-service liveness probe answers (public, anonymous)
  3. application-service internal face is fail-closed without identity headers
  4. the live gateway rejects an unauthenticated /api/apps request with its own
     401 'detail' envelope before route matching (fail-closed auth pre-check;
     route-registration evidence comes from check 6, not here)
  5. Redis acl:rules carries a gateway -> application-service allow rule for
     "GET /internal/v1/apps" - the gateway-mode precondition
  6. full chain with a real user token: the gateway-terminated request reaches
     this service and gets its own 404 envelope (no endpoint exists yet);
     a gateway-level 'detail' 404 instead identifies an unregistered prefix

Exit codes: 0 = all checks passed; 1 = a check failed; 2 = checks 1-5 passed
but check 6 was skipped because no --token/SMOKE_USER_TOKEN was supplied.

Usage:
  uv run --directory core/application-service python scripts/smoke_p0_gateway.py --token <user-jwt>
  # or with port-forwarded endpoints:
  ... --gateway-url http://127.0.0.1:8002 --service-url http://127.0.0.1:8080 \
      --redis-url redis://127.0.0.1:6379/0
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
from redis import Redis

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from auth_sdk.acl import parse_rules  # noqa: E402  (reuse the runtime matcher for check 5)

REPO_ROOT = Path(__file__).resolve().parents[3]  # scripts -> application-service -> core -> repo
ROUTES_PATH = REPO_ROOT / "core/gateway-service/routes.json"

SERVICE_NAME = "application-service"
GATEWAY_CALLER = "gateway"
ACL_KEY = "acl:rules"
SMOKE_ENDPOINT = "GET /internal/v1/apps"
SMOKE_PREFIX = "/api/apps"


class CheckFailed(Exception):
    """A smoke check failed; the message states the observed evidence."""


def check_routes_file() -> str:
    routes = json.loads(ROUTES_PATH.read_text(encoding="utf-8"))
    matches = [r for r in routes.get("routes", []) if r.get("path_prefix") == SMOKE_PREFIX]
    if not matches:
        raise CheckFailed(f"{SMOKE_PREFIX} is not registered in {ROUTES_PATH}")
    entry = matches[0]
    if entry.get("service") != SERVICE_NAME or entry.get("aud") != SERVICE_NAME:
        raise CheckFailed(
            f"{SMOKE_PREFIX} routes to service={entry.get('service')!r} "
            f"aud={entry.get('aud')!r}, expected {SERVICE_NAME!r} for both"
        )
    if not str(entry.get("base_url", "")).rstrip("/").endswith(SERVICE_NAME):
        raise CheckFailed(f"{SMOKE_PREFIX} base_url looks wrong: {entry.get('base_url')!r}")
    return (
        f"{SMOKE_PREFIX} -> {entry['service']} "
        f"(aud={entry['aud']}, base_url={entry['base_url']})"
    )


def check_service_liveness(client: httpx.Client, service_url: str) -> str:
    response = client.get(f"{service_url}/internal/v1/health/live")
    if response.status_code != 200:
        raise CheckFailed(f"liveness answered {response.status_code}: {response.text[:200]}")
    body = response.json()
    if body.get("status") != "alive" or body.get("service") != SERVICE_NAME:
        raise CheckFailed(f"unexpected liveness body: {body}")
    return "liveness ok (anonymous, status=alive)"


def check_service_fail_closed(client: httpx.Client, service_url: str) -> str:
    response = client.get(f"{service_url}/internal/v1/apps")
    content_type = response.headers.get("content-type", "")
    body = response.json() if content_type.startswith("application/json") else {}
    if response.status_code != 401 or body.get("code") != 401:
        raise CheckFailed(
            f"internal face must reject without identity headers "
            f"(got {response.status_code}: {response.text[:200]})"
        )
    return f"fail-closed without identity headers (error={body.get('error')!r})"


def check_gateway_auth_precheck(client: httpx.Client, gateway_url: str) -> str:
    """Anonymous traffic must die at the gateway's auth pre-check (fail-closed).

    The gateway authenticates before route matching, so this 401 is evidence of
    the auth chain only - not of /api/apps being registered (any prefix answers
    it identically). Route registration is evidenced by check 6; a 401 that
    carries a service error envelope means the anonymous request bypassed the
    gateway - the single-channel violation this check exists to catch.
    """
    response = client.get(f"{gateway_url}{SMOKE_PREFIX}")
    content_type = response.headers.get("content-type", "")
    body = response.json() if content_type.startswith("application/json") else {}
    if response.status_code != 401:
        raise CheckFailed(
            f"anonymous {SMOKE_PREFIX} must answer 401 at the gateway, "
            f"got {response.status_code}: {response.text[:200]}"
        )
    if body.get("code") is not None:
        raise CheckFailed(
            "the 401 carries a service error envelope - the anonymous request reached "
            f"a downstream service instead of dying at the gateway: {body}"
        )
    if "detail" not in body:
        raise CheckFailed(f"unexpected 401 body shape (no gateway 'detail' key): {body}")
    return f"rejected at the gateway before routing (detail={body['detail']!r})"


def check_acl_rule(redis_url: str, timeout: float) -> str:
    client = Redis.from_url(redis_url, decode_responses=True, socket_timeout=timeout)
    try:
        blob = client.get(ACL_KEY)
    except Exception as exc:  # redis.exceptions.RedisError and friends
        raise CheckFailed(f"redis unreachable at {redis_url}: {type(exc).__name__}: {exc}") from exc
    finally:
        client.close()
    matcher = parse_rules(blob)
    if matcher is None:
        detail = "key absent" if blob is None else "bad shape"
        raise CheckFailed(f"acl:rules missing or corrupt ({detail})")
    if not matcher.allowed(GATEWAY_CALLER, SERVICE_NAME, SMOKE_ENDPOINT):
        raise CheckFailed(
            f"no allow rule for ({GATEWAY_CALLER!r}, {SERVICE_NAME!r}, {SMOKE_ENDPOINT!r}); "
            "provision via identity: POST /internal/acl-rules with "
            '{"caller_service":"gateway","target_service":"application-service",'
            '"endpoint":"GET /internal/v1/apps","effect":"allow"}'
        )
    return f"allow rule present ({matcher.rule_count} rules loaded)"


def check_full_forward(client: httpx.Client, gateway_url: str, token: str) -> str:
    response = client.get(
        f"{gateway_url}{SMOKE_PREFIX}",
        headers={"Authorization": f"Bearer {token}"},
    )
    content_type = response.headers.get("content-type", "")
    body = response.json() if content_type.startswith("application/json") else {}
    if response.status_code == 502 or response.status_code == 504:
        raise CheckFailed(
            f"gateway could not reach {SERVICE_NAME} ({response.status_code}): "
            "check the Service DNS name / NetworkPolicy"
        )
    if response.status_code == 401:
        raise CheckFailed(
            "the service rejected the gateway-terminated request (401) - with a fresh "
            f"user token this points at the service-side auth chain: {response.text[:200]}"
        )
    # Reaching this service without any endpoint yet = the legacy 404 envelope
    # {"code": 404, ...}; the gateway's own unmatched-route 404 is {"detail": ...}.
    if response.status_code == 404 and body.get("code") == 404:
        return "request reached the application service (legacy 404 envelope; no endpoint in P0)"
    if response.status_code == 404:
        raise CheckFailed(
            f"gateway answered its own 404 (body={body!r}) - {SMOKE_PREFIX} is not in the "
            "live gateway route table (routes.json not deployed/loaded yet?)"
        )
    raise CheckFailed(
        f"unexpected response {response.status_code} (content-type={content_type!r}): "
        f"{response.text[:200]}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="P0 gateway-wiring smoke (zero traffic)")
    parser.add_argument("--gateway-url", default="http://127.0.0.1:8002")
    parser.add_argument("--service-url", default="http://127.0.0.1:8080")
    parser.add_argument("--redis-url", default=os.environ.get("SMOKE_REDIS_URL", "redis://127.0.0.1:6379/0"))
    parser.add_argument("--token", default=os.environ.get("SMOKE_USER_TOKEN"))
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    gateway_url = args.gateway_url.rstrip("/")
    service_url = args.service_url.rstrip("/")
    skipped_full_forward = args.token is None

    checks = [
        ("routes.json claims the smoke prefix", lambda: check_routes_file()),
        ("service liveness", lambda: check_service_liveness(client, service_url)),
        ("service fail-closed", lambda: check_service_fail_closed(client, service_url)),
        (
            "anonymous request rejected at the gateway",
            lambda: check_gateway_auth_precheck(client, gateway_url),
        ),
        ("acl:rules allow rule", lambda: check_acl_rule(args.redis_url, args.timeout)),
    ]
    if not skipped_full_forward:
        checks.append(
            (
                "full forward with user token",
                lambda: check_full_forward(client, gateway_url, args.token),
            )
        )

    failures = 0
    with httpx.Client(timeout=args.timeout) as client:
        for index, (name, run) in enumerate(checks, start=1):
            try:
                detail = run()
            except CheckFailed as exc:
                failures += 1
                print(f"[{index}/{len(checks)}] FAIL {name}: {exc}")
            except Exception as exc:  # transport errors, unexpected shapes
                failures += 1
                print(f"[{index}/{len(checks)}] FAIL {name}: {type(exc).__name__}: {exc}")
            else:
                print(f"[{index}/{len(checks)}] OK   {name}: {detail}")
        if skipped_full_forward:
            print(
                f"[{len(checks) + 1}] SKIP full forward with user token: "
                "no --token / SMOKE_USER_TOKEN supplied"
            )

    if failures:
        print(f"[smoke] FAILED: {failures} check(s) failed")
        return 1
    if skipped_full_forward:
        print("[smoke] mechanism checks passed; full forward not verified (exit 2)")
        return 2
    print("[smoke] all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
