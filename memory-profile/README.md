# memory-profile

Independent, internal-only service hosting Memory domain modules. Routes are composed in `src/modules/__init__.py`; that module is the authoritative endpoint inventory, and the API-key reachable subset is mirrored by `src/external_api_contract.json`. This README deliberately does not enumerate endpoints or per-domain behavior, because more domains migrate in over time.

## Boundary and ownership

- This service reads shared PostgreSQL tables through read-only mappings, plus Neo4j, Redis, and Elasticsearch. It does not import `api` or `app` code.
- The generation endpoints (memory insight, user summary, cache generation) write two cached text groups on `end_users`. Reads and those writes share one PostgreSQL pool; the read repositories stay select-only through the boundary script's AST checks rather than a connection-level read-only setting.
- LLM calls go to the model service over its invoke face. This process never holds provider credentials: it names a `model_configs` row and its tenant, and the model service resolves channels and failover (`src/infrastructure/model_service.py`).
- PostgreSQL, Neo4j, Redis, Elasticsearch, and the model-service client each have a dedicated lifecycle manager under `src/infrastructure/`; `src/runtime.py` only composes, probes, and closes them. The model service is not part of readiness: only generation endpoints need it.
- `src/common/redis_cache.py` provides shared JSON helpers, zset JSON reads, exact/prefix/pattern invalidation, and the async `@redis_cache` cache-aside decorator. It reuses the existing `ProfileRuntime.redis` provider and does not add cache state to runtime.
- `src/modules/shared/` holds the cross-module services other modules call; repositories stay internal. `EndUserService` owns the end-user rules: is this end user addressable from the caller's workspace? It exposes `exists_in_workspace()` for a strict one-lookup membership answer (no merge branch, no payload columns), `resolve_in_workspace()` for the merge-aware identity used by profile, summary and graph endpoints, and `active_end_user_ids()` for the batch target set; both require the user and the workspace to be active and collapse malformed/foreign/missing ids to `False`/`None`. `ModelConfigService` owns model resolution for every workspace slot (`llm`, `embedding`, `rerank`, `vision`, `audio`, `video`): workspace → active `memory_config` → preset/slot column → `model_configs` tenant/active check, with typed calls (`chat_model_for_workspace`, `embed_texts_for_workspace`, `rerank_for_workspace`) and `WorkspaceModelMemo` making the LLM a once-per-request lookup shared by every user in a batch. `WorkspaceService` owns the one workspace row (`row()`): identity, active/storage flags and all six model columns, so validation and model resolution read it once per request. Profile, interest distribution, graph and cache generation all go through these services, so neither the workspace boundary nor the model choice can drift between endpoints. The boundary predicates and the narrow-read template live in `src/modules/shared/end_user_scope.py` (`active_user_in_workspace`, `active_users_in_workspace`, `scoped_end_user_select`, `active_workspace_exists`); the access repository, the profile reads and the generation writes all compose them instead of repeating the conditions.

Decorator usage:

```python
from src.common.redis_cache import invalidate_cache, redis_cache

@redis_cache(
    ttl=300,
    prefix="memory_summary",
    skip_args=["self"],
    id_arg="end_user_id",
    return_type=SummarySchema,
)
async def load_summary(self, end_user_id: UUID) -> SummarySchema:
    ...
```

The decorator normalizes positional/keyword arguments, uses one in-flight task per key, restores Pydantic/UUID results when `return_type` is set, and falls back to the wrapped function when Redis is unavailable or cached JSON is invalid. Invalidation matches the API helper:

```python
await invalidate_cache(key="cache:memory_summary:...")
await invalidate_cache(prefix=f"memory_summary:{end_user_id}")
await invalidate_cache(pattern="cache:memory_summary:*")
```

Exact invalidation uses `DEL`; prefix/pattern invalidation uses cursor-based `SCAN` and batched `UNLINK`.
- `auth-sdk` is consumed from the repository-local `../packages/auth-sdk` path without a version constraint, so service builds always use the current monorepo SDK source.
- The API service remains the owner of shared PostgreSQL tables. `memory-profile` owns and migrates only models registered under `src/models/owned/`; shared mappings under `src/models/references/` are never migration targets.
- Direct mode trusts internal identity headers and therefore requires network-level isolation. Every non-public Memory request requires a complete generic identity principal (`x-user-id` or `x-api-key-id`, plus `x-tenant-id` / `x-workspace-id`), and user-scoped operations verify the active workspace owner.

## Localization

Response text — module messages, enum display names, and error envelopes —
is translated by `src/i18n` from the JSON catalogs under
`src/locales/<locale>/`. `LanguageMiddleware` resolves the request language
once, in this order: `X-Language-Type`, `Accept-Language`, then
`I18N_DEFAULT_LANGUAGE` (default `zh`; the chosen locale is echoed as
`Content-Language`).

`zh` output stays byte-identical to the legacy API contract. Error text is
raised as translation keys (`LocalizedProfileError`) and rendered once by the
global exception handler; empty-state payloads carry a `message_key` that the
handler renders, so those messages stay language-neutral in the cache. Any
module that caches translated display text must include the language in its
cache key. To add a language, add a directory under
`src/locales` with the same namespaces and list it in
`I18N_SUPPORTED_LANGUAGES`.

## Authentication

`MEMORY_AUTH_MODE=direct` is the community default. It accepts only a complete, UUID-valid generic identity injected by the API or Gateway; deploy this mode behind a NetworkPolicy/security group that blocks all other callers. Health endpoints are public.

`MEMORY_AUTH_MODE=gateway` loads `enterprise_ext.memory.MemoryGatewayAuth`. Missing enterprise code or missing JWKS configuration fails startup instead of downgrading to direct. Bearer JWT claims are authoritative for actor, tenant, workspace, and credential type; API-key tokens must include the `memory` scope. The enterprise extension retains a transitional channel for the existing API direct proxy only when no Bearer is supplied and all trusted headers are present; an invalid Bearer never falls back.

Authentication failures use the normal `{code,msg,data,error,time}` envelope and include `X-Trace-Id`. `MEMORY_AUTH_KILL_SWITCH_FILE` only disables gateway verification during a controlled rollback; identity headers remain mandatory because modules cannot execute without workspace context.

Domain modules never read `request.state`. Handlers and routes take the caller from `src.middleware.identity.require_memory_context`, which returns `src.middleware.auth.MemoryPrincipal`; `request.state.memory_context` stays an internal detail of the middleware layer, so a new Memory module adds routes without importing another domain's dependencies.

## External API contract

Routes reachable through the API-key face (`ex_api`) carry `@external_api` from
`src/common/external_api.py`. The decorator is metadata only — it sets one
attribute and returns the function unchanged, so it adds no request-time cost.
Health routes are unauthenticated but are not part of the API-key contract, so
they are not marked.

Their HTTP contract is snapshotted in `src/external_api_contract.json` and
compared once while the application is built. The snapshot is read from
FastAPI's resolved route analysis, not re-derived from the signature, so it
records:

- where each parameter lives — query, path, header, cookie, form, file, or body;
  entries are keyed by `location:name`, so the same name exposed as both a query
  parameter and a header is kept separately. A genuine same-slot collision
  (route and dependency both exposing `query:limit`) keeps every entry as
  `query:limit@endpoint` and `query:limit@<dependency>` — each additional source
  gets its own qualified key — instead of dropping one;
- the default FastAPI actually uses, including a signature default overriding
  `Annotated[..., Query(...)]`; `default_factory` is captured by a code-structure
  digest that ignores filenames and line numbers (never called — non-deterministic
  factories would churn the snapshot), so host, CI, and image agree; set and
  frozenset defaults are sorted before serialisation, so hash randomisation
  cannot reorder them;
- validation constraints from both `Query`/`Body` and `Annotated[..., Field(...)]`;
- parameters contributed by `Depends(...)` dependencies, which FastAPI exposes
  to callers and documents in OpenAPI;
- request-body models field by field (nested models, PEP 695 aliases, unions of
  models, and dataclasses included), plus wire-relevant `model_config`
  (`extra`, `strict`, `populate_by_name`, `from_attributes`) and field
  `validation_alias` / `serialization_alias`;
- the response model and its fields, the status code, and declared responses.

Middleware-enforced identity headers (`x-tenant-id`, `x-workspace-id`,
`x-source`, `x-user-id` / `x-api-key-id`) are global, not per-route, and are
documented under Authentication rather than repeated in every route entry.

Drift logs `external API contract changed: ...` at WARNING level per change. A
missing, unreadable, or structurally invalid snapshot — including one whose
`schema` is absent or differs from the code's `SNAPSHOT_SCHEMA` — fails startup
instead of silently disabling the guard. After intentionally accepting a
contract change, regenerate the snapshot and commit the diff:

```bash
uv run python scripts/update_external_api_contract.py
uv run python scripts/check_external_api_contract.py   # manual check; exit 1 on drift
```

`tests/test_external_api_contract.py` covers the same comparison locally; the
`memory-profile` workflow runs `scripts/check_external_api_contract.py`, so CI
catches a stale snapshot before deployment.

## Internal HTTP contract

Every route answers on one internal prefix, `/internal/v1` — the same single face
`mem-knowledge` and `model-service` expose. Callers map their own path onto it:
the API's proxy rewrites its own `/api` (user JWT) or `/v1` (API key) path, and
the Gateway's `/api|/v1 → /internal/v1` rewrite lands on the same routes. The face
does not survive in the path: the request carries it as `x-source` (which face it
arrived on — `ex_api` or `in_api`).

Health probes are the only identity-free paths and the container HEALTHCHECK
target; they live under `/internal/v1/health/`.

Domain routes are mounted under `/internal/v1/<domain>/...`; workspace-scoped
operations require `x-workspace-id: <uuid>`. The composed router in
`src/modules/__init__.py` is the authoritative inventory of routes; API-key
reachable routes and their parameter contract are mirrored in
`src/external_api_contract.json`. This README does not duplicate that list.

Required direct-mode headers are `x-tenant-id`, `x-workspace-id`, `x-source`, and the acting id — whose header the face decides: `ex_api` carries the key that authenticated as `x-api-key-id`, and every other face carries the user as `x-user-id`. Both terminators follow that rule (the Gateway when it terminates an API key, the API's own proxy on its `/v1` route), so an `ex_api` request resolves to one principal whichever hop terminated it, and the acting id is never a key id on one path and a creator's user id on another. The request log records which of the two it got, as `actor_kind` (`apikey` or `actor`), so a key id is never read as an actor. `X-Trace-Id` is optional; all identity IDs must be UUIDs. In gateway mode the actor, tenant, workspace, and authenticated face come from verified token claims (the claim keeps its own `api_key` / absent spelling and is mapped onto `ex_api` / `in_api`); conflicting request headers are ignored.

The legacy response envelope is exactly:

```json
{"code":0,"msg":"OK","data":{},"error":"","time":1710000000000}
```

The field names intentionally remain `msg`, `error`, and millisecond `time`, matching `api/app/core/response_utils.py`. Health responses use their dedicated probe shape.

## Generation endpoints

Generation is a later, explicitly scoped extension over the read service:

- which LLM: end user → workspace → active `memory_config` → effective LLM column
  (default-config workspaces follow the singleton `workspace_default_model_presets`)
  → `model_configs` tenant/active check (`src/modules/shared/model_config_service.py`).
  Only the LLM is validated; generation needs no embedding/rerank model, unlike
  the API's full memory-config load;
- prompts: byte-identical copies of the API templates under
  `src/prompts/templates/`, loaded through `src/prompts/`;
- writes: only `end_users.memory_insight*` and `end_users.user_summary*`, with the
  API's optimistic concurrency conditions for insight; for the migrated endpoints
  those writes happen in memory-jobs, so this service no longer writes
  `end_users` at all;
- `interest_distribution` is generated by `memory_jobs.interest_distribution.generate`
  and awaited the same way; the cache stays here. It uses the same Redis key
  (`cache:memory:interest_distribution:by_user:<id>:<lang>`) and payload shape as
  the API, so either implementation can serve a cache entry written by the other.
  The worker only reports `cacheable`; this service owns the key, the TTL and the
  "cache the empty result too" rule;
- `generate_cache` with an `end_user_id` does **not** run the pipeline here: the
  service publishes `memory_jobs.profile_cache.generate` through
  `task-bridge-sdk` (a bare Celery producer — no task registration, no worker)
  and waits on the result backend, then returns the API's response shape. The
  wait is bounded by a fixed 960s budget (above the worker's 900s hard limit).
  Task name and queue are bridge constants, and each flow declares its own wait
  budget next to the submit call — none of them is a setting. Broker and
  result backend default to Redis DB 3/4, matching the API and memory-jobs. The task result is the API's
  `data` shape verbatim (`end_user_id` / `insight_success` / `summary_success` /
  `errors`; the batch uses `total_users` / `successful` / `failed` / `errors`),
  and this service returns it unchanged. `interest_distribution` follows
  the same pattern with a fixed 120s budget.
- `generate_cache` without an `end_user_id` dispatches
  `memory_jobs.profile_cache.generate_workspace`: the worker runs the API's
  serial per-user loop and returns `{total_users, successful, failed, errors}`.
  The wait is bounded by a fixed 3600s budget, matching the API proxy's
  `MEMORY_PROFILE_GENERATE_CACHE_TIMEOUT_SECONDS` for its own outer request.
- Workspace-level facts — the workspace row and the LLM configuration — are
  resolved once per request by `WorkspaceModelMemo` and shared by both parts of
  a user and by every user in a batch. Resolution stays lazy (only when there is
  something to generate), and a failed resolution is not retried per user.
- Deployments that enable `generate_cache` or `interest_distribution` need a
  reachable broker/result backend and a running `memory_heavy_tasks` worker
  (memory-jobs). Without a worker these endpoints keep the legacy envelope:
  HTTP 200 with `code=10003` (`SERVICE_UNAVAILABLE`) and a localized `msg` —
  the API's `fail()` never changed the HTTP status either.
- Deployments that enable `generate_cache` must grant `UPDATE` on `end_users`
  to the **memory-jobs** PostgreSQL role (all generation now runs there). This
  service's role can stay read-only; without the grant only the generation
  endpoints fail.

## Source layout

```text
src/entry.py
  -> src/app.py
  -> src/modules/__init__.py
  -> src/modules/<domain>/routes.py
  -> src/modules/<domain>/handler.py
  -> src/modules/<domain>/service.py
  -> src/modules/<domain>/repository/
  -> src/modules/shared/               # 跨模块服务：end_user_service / model_config_service / workspace_service
  -> src/modules/shared/repository/    # 这些服务的只读数据访问
  -> src/modules/<domain>/generation/   # 仅 analytics：LLM 生成流水线
  -> src/prompts/                       # 全部 prompt 模板与命名渲染器
  -> src/infrastructure/               # 客户端与 producer；task_bridge.py 只投递任务，不注册任务
```

`src/modules/__init__.py` composes every domain router into the single router `src/app.py` mounts, so a new module is registered there and nowhere else. Routes only bind HTTP parameters. Handlers translate transport contracts and response envelopes. Services own use-case orchestration. Repositories are named by domain capability and depend on shared infrastructure managers. Prompt text lives only in `src/prompts/templates/`; pipelines render it through the named functions exported by `src/prompts/`, never by composing strings inline.

API-owned PostgreSQL tables are mapped under `src/models/references/` with a separate `ReadOnlyBase.metadata`, no foreign keys, and no relationships. Profile-owned tables belong under `src/models/owned/`, inherit `ProfileBase`, and are the only tables exposed to Alembic. PostgreSQL read repositories use SQLAlchemy ORM/select expressions only; native `text()` SQL and ORM write methods are rejected by the boundary script. The runtime pool explicitly uses rollback-on-return and invalidates any asyncpg connection still in a transaction at checkin or checkout; Alembic uses a separate short-lived migration engine without pooling.

## Database migrations

Alembic manages only tables registered in `ProfileBase.metadata` and records its independent chain in `alembic_version_profile`. The `include_name` and `include_object` filters prevent shared/API-owned tables from being reflected or emitted as drops during autogenerate. Add each new owned model to `src/models/owned/__init__.py` before generating its revision; never import `ReadOnlyBase` or `src.models.references` from the owned package or migration environment.

Alembic uses `PROFILE_MIGRATION_DATABASE_URL` when supplied, otherwise it derives the same asyncpg URL as the service. Production deployments should inject a dedicated DDL role only into a one-shot migration job. Do not run migrations automatically in every web replica.

```bash
# From memory-profile/
uv run alembic -c alembic.ini current
uv run alembic -c alembic.ini revision --autogenerate -m "add profile table"
uv run alembic -c alembic.ini upgrade head
uv run alembic -c alembic.ini check

# Using the same image as a one-shot deployment job
alembic -c /app/alembic.ini upgrade head
```

The scaffold intentionally contains no empty initial revision. Create the first revision together with the first owned table, review the generated file, and reject it if it references any shared table.

## Global pagination contract

All future paginated modules must use `src/common/pagination.py` and a concrete generic response model rather than defining local page schemas:

```python
PageResponse = ApiResponse[PageData[ItemSchema]]
return PageResponse(
    data=PageData[ItemSchema].create(
        page=page,
        pagesize=pagesize,
        total=total,
        items=items,
    )
)
```

The payload matches the API service exactly:

```json
{"page":{"page":1,"pagesize":20,"total":100,"hasnext":true},"items":[]}
```

`page` starts at 1, `pagesize` and `total` are non-negative, and `hasnext` is constrained to `(page * pagesize) < total`. Empty pages may use `pagesize=0,total=0` for compatibility with existing API behavior.

## Configuration

Configuration is frozen after bootstrap. Paths are derived from the source location, never the current working directory. Precedence is repository `.env`, then `memory-profile/.env`, then process environment. Existing `DATABASE_*`/`POSTGRES_*`, `NEO4J_*`, `REDIS_*`, and `ELASTICSEARCH_*` variables are reused; the model-service invoke face uses `MODEL_SERVICE_*`; service-specific settings use `PROFILE_*`. See `.env.example`.

This service must use the same Redis logical DB as the API (the shared
deployment uses `REDIS_DB=3`) so keys written by API workers stay visible.
`REDIS_URL`, when set, takes precedence over
`REDIS_HOST`/`REDIS_PORT`/`REDIS_DB`; keep its path in sync with the API's DB
as well.

## API cutover

The monolith keeps distinct public JWT (`/api`) and API-key (`/v1`) adapters. Their authentication, workspace selection, end-user validation/merge behavior, and request-body semantics are intentionally not collapsed. Contract-equivalent endpoints opt into forwarding with the API-side `@memory_profile_proxy(...)` decorator; undecorated endpoints always execute their original local implementation. It defaults to the legacy implementation. After deploying this service, enable proxying in the API process:

```dotenv
ENABLE_MEMORY_PROFILE=true
MEMORY_PROFILE_BASE_URL=http://memory-profile:8082
```

With the flag enabled, every endpoint carrying `@memory_profile_proxy(...)` uses the remote service; endpoints without it keep their local implementation. The API marks API-key calls `x-source: ex_api` and everything else `in_api`, the same labelling the Gateway applies on the face it terminated credentials on, and it identifies the API-key face by the key itself (`x-api-key-id`) exactly as the Gateway does. The mode is derived from the caller's own path, so it cannot drift from the forwarded path. Transport/protocol failures fail closed as `SERVICE_UNAVAILABLE`. Set the flag back to `false` for a reversible rollback.

## Logging

Logging is configured once by `src/common/observability/logging.py`; `logging.basicConfig()` is not used. Configuration:

```dotenv
PROFILE_LOG_LEVEL=INFO
PROFILE_LOG_FORMAT=text       # text | json
PROFILE_ACCESS_LOG_ENABLED=true
```

Every record receives the current `trace_id` and the emitting process hostname as `instance` (text: `instance=<hostname>`, JSON: `"instance": "<hostname>"`), so log lines from multiple replicas stay distinguishable. JSON logs use stable fields including `timestamp`, `level`, `service`, `instance`, `logger`, `event`, `message`, and `trace_id`; request completion records additionally include method, route template, status, duration, response bytes, completion/outcome, authenticated actor/tenant/workspace/source, and business error codes. The request logger never reads request or response bodies and never logs query strings or headers. Values passed to the logging system, including cache identifiers and exception text, are emitted without redaction.

Middleware execution order is `RequestLogging -> TraceId -> Language -> Metrics -> MemoryAuth -> routing`. Uvicorn access logging is disabled to avoid duplicate request lines; the service middleware owns access completion logs.

Uvicorn runs with `--http zttp --http2`. Port `8082` accepts HTTP/1.1 and cleartext HTTP/2 prior knowledge (h2c); HTTP/1.1 Upgrade-based h2c and HTTP/2 WebSockets are not supported. Production TLS and ALPN negotiation should be terminated by the Gateway or reverse proxy.

## Runtime monitoring

`GET /internal/v1/health/metrics` is an unauthenticated, read-only diagnostic
probe with the same network-isolation assumption as `/health/live` and
`/health/ready`. It returns a dedicated JSON shape (not the standard response
envelope)
and sets `Cache-Control: no-store`. It never pings a dependency; `/health/ready`
remains the connectivity contract.

- `process`: RSS/VMS, peak and baseline RSS, growth since process start, CPU
  time/percent, thread count, and open file descriptors (POSIX) or handles
  (Windows).
- `python`: GC counts and per-generation statistics, `gc.garbage`, Python
  thread count, pending asyncio tasks, and tracemalloc state.
- `http`: per-process request totals, in-flight count, status-class buckets, and
  per-route-template request/error counts with average and maximum latency.
- `dependencies`: in-memory PostgreSQL, Neo4j, Redis, and Elasticsearch pool
  counters (checked-out connections, in-use connections, node liveness).
- `warnings`: stable codes such as `postgres_pool_saturated`,
  `neo4j_pool_saturated`, `redis_pool_saturated`,
  `elasticsearch_no_alive_nodes`, `rss_threshold_exceeded`, and
  `process_metrics_unavailable`. `status` becomes `degraded` when any are
  present; the endpoint still answers `200`.

A `top=0` scrape costs tens of microseconds on the event loop and never touches
the network. `top` (0-50, default 0) adds the largest Python allocation sites.
Taking a snapshot walks every traced block and blocks the event loop (hundreds
of milliseconds on a normal heap), so keep it at 0 during load unless you are
actively investigating; results are memoized for 10 seconds. The always-on
request middleware costs about two microseconds per HTTP request. Allocation
tracing must be enabled at startup:

```dotenv
PROFILE_MEMORY_TRACEMALLOC_ENABLED=true    # restart required; adds overhead
PROFILE_MEMORY_TRACEMALLOC_FRAMES=10
PROFILE_MEMORY_RSS_WARN_BYTES=0            # 0 disables the RSS warning
```

Because the endpoint is public like the other health probes, expose
`/internal/*` only to the cluster network. It returns process, pool, and route
counters, but never configuration, connection strings, SQL, or business data.

## Run and validate

```bash
cd memory-profile
uv sync --frozen
uv run uvicorn src.entry:app --host 0.0.0.0 --port 8082 --http zttp --http2 --no-access-log
uv run ruff check .
uv run python -m compileall -q src scripts migrations
uv run python scripts/check_import_boundaries.py
```

Build from the repository root so Docker can use the service-specific ignore file:

```bash
docker build -f memory-profile/Dockerfile -t memory-profile:local .
```

The image runs as UID/GID 10001 and exposes port 8082.
