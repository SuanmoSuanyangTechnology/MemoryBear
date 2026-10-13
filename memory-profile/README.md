# memory-profile

Independent, internal-only profile analytics service. It currently exposes six read-only analytics use cases. The API cuts over the five read operations whose contracts are equivalent: `memory_insight`, `user_summary`, `node_statistics`, `graph_data`, and `community_graph`; `workspace_statistics` remains locally served until snapshot refresh is migrated.

## Boundary and ownership

- This service reads the shared PostgreSQL `end_users` and narrow analytics tables, Neo4j graph, Redis caches, and Elasticsearch-backed memory indexes. It does not import `api` or `app` code.
- PostgreSQL, Neo4j, Redis, and Elasticsearch each have a dedicated lifecycle manager under `src/infrastructure/`; `src/runtime.py` only composes, probes, and closes them.
- `src/common/redis_cache.py` provides shared JSON helpers, zset JSON reads, exact/prefix/pattern invalidation, and the async `@redis_cache` cache-aside decorator. It reuses the existing `ProfileRuntime.redis` provider and does not add cache state to runtime.

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
- `auth-sdk` is consumed from the repository-local `../packages/auth-sdk` path without a version constraint, so profile builds always use the current monorepo SDK source.
- The API service remains the owner of shared PostgreSQL tables. `memory-profile` owns and migrates only models registered under `src/models/owned/`; shared mappings under `src/models/references/` are never migration targets.
- Workspace statistics are snapshot-only. The API worker remains responsible for producing `cache:memory:workspace_statistics:v1:{workspace_id}`; this service does not synchronously recompute a whole workspace.
- Direct mode trusts internal identity headers and therefore requires network-level isolation. Every non-public Memory request requires a complete `X-Memory-*` principal, and user-scoped operations verify the active workspace owner.

## Authentication

`MEMORY_AUTH_MODE=direct` is the community default. It accepts only a complete, UUID-valid `X-Memory-*` identity injected by the API or Gateway; deploy this mode behind a NetworkPolicy/security group that blocks all other callers. Health endpoints are public.

`MEMORY_AUTH_MODE=gateway` loads `enterprise_ext.memory.MemoryGatewayAuth`. Missing enterprise code or missing JWKS configuration fails startup instead of downgrading to direct. Bearer JWT claims are authoritative for actor, tenant, workspace, and credential type; API-key tokens must include the `memory` scope. The enterprise extension retains a transitional channel for the existing API direct proxy only when no Bearer is supplied and all trusted headers are present; an invalid Bearer never falls back.

Authentication failures use the normal `{code,msg,data,error,time}` envelope and include `X-Trace-Id`. `MEMORY_AUTH_KILL_SWITCH_FILE` only disables gateway verification during a controlled rollback; identity headers remain mandatory because analytics cannot execute without workspace context.

## Internal HTTP contract

Base prefix: `/internal/v1`.

Health (no identity header):

- `GET /health/live`
- `GET /health/ready`

Analytics under `/memory/analytics` (all require `X-Memory-Workspace-ID: <uuid>`):

- `GET /memory_insight?end_user_id=<uuid>`
- `GET /user_summary?end_user_id=<uuid>`
- `GET /node_statistics?end_user_id=<uuid>`
- `GET /workspace_statistics`
- `GET /graph_data?end_user_id=<uuid>`
- `GET /community_graph?end_user_id=<uuid>`

Required direct-mode headers are `X-Memory-Actor-ID`, `X-Memory-Tenant-ID`, `X-Memory-Workspace-ID`, and `X-Memory-Source`, where all identity IDs must be UUIDs and source must be `manager` or `api_key`. `X-Trace-Id` is optional. In gateway mode these values come from verified token claims; conflicting request headers are ignored.

The legacy analytics envelope is exactly:

```json
{"code":0,"msg":"OK","data":{},"error":"","time":1710000000000}
```

The field names intentionally remain `msg`, `error`, and millisecond `time`, matching `api/app/core/response_utils.py`. Health responses use their dedicated probe shape.

`graph_data` accepts comma-separated `node_types`, `limit` up to 1000, `depth` up to 3, `center_node_id`, and `per_type_limits` such as `Statement:50,Chunk:20`. Only the fixed supported node labels are accepted, and every Neo4j query includes `end_user_id` isolation.

## Source layout

```text
src/entry.py
  -> src/app.py
  -> src/modules/<domain>/routes.py
  -> src/modules/<domain>/handler.py
  -> src/modules/<domain>/service.py
  -> src/modules/<domain>/repository/
  -> src/infrastructure/
```

Routes only bind HTTP parameters. Handlers translate transport contracts and response envelopes. Services own use-case orchestration. Repositories are named by domain capability (`end_user_profile`, `memory_statistics`, `memory_graph`, `workspace_statistics`) and depend on shared infrastructure managers.

API-owned PostgreSQL tables are mapped under `src/models/references/` with a separate `ReadOnlyBase.metadata`, no foreign keys, and no relationships. Profile-owned tables belong under `src/models/owned/`, inherit `ProfileBase`, and are the only tables exposed to Alembic. PostgreSQL reference repositories use SQLAlchemy ORM/select expressions only; native `text()` SQL and ORM write methods are rejected by the boundary script. Reference connections also set `default_transaction_read_only=on`, explicitly use rollback-on-return, and invalidate any asyncpg connection still in a transaction at checkin or checkout; Alembic uses a separate short-lived migration engine without pooling or the runtime read-only setting.

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

Configuration is frozen after bootstrap. Paths are derived from the source location, never the current working directory. Precedence is repository `.env`, then `memory-profile/.env`, then process environment. Existing `DATABASE_*`/`POSTGRES_*`, `NEO4J_*`, `REDIS_*`, and `ELASTICSEARCH_*` variables are reused; service-specific settings use `PROFILE_*`. See `.env.example`.

## API cutover

The monolith keeps distinct public JWT (`/api`) and API-key (`/v1`) adapters. Their authentication, workspace selection, end-user validation/merge behavior, and request-body semantics are intentionally not collapsed. Contract-equivalent endpoints opt into forwarding with the API-side `@memory_profile_proxy(...)` decorator; undecorated endpoints always execute their original local implementation. It defaults to the legacy implementation. After deploying this service, enable proxying in the API process:

```dotenv
ENABLE_MEMORY_PROFILE=true
MEMORY_PROFILE_BASE_URL=http://memory-profile:8082
```

With the flag enabled, only the five contract-equivalent read operations listed above use the remote service. `generate_cache` and `interest_distribution` are not exposed by `memory-profile` and remain on their original implementations; `workspace_statistics` remains local until the profile service owns equivalent snapshot-refresh infrastructure. API-key calls are marked with `X-Memory-Source: api_key`; JWT manager calls use `manager`. Transport/protocol failures fail closed as `SERVICE_UNAVAILABLE`. Set the flag back to `false` for a reversible rollback.

## Logging

Logging is configured once by `src/common/observability/logging.py`; `logging.basicConfig()` is not used. Configuration:

```dotenv
PROFILE_LOG_LEVEL=INFO
PROFILE_LOG_FORMAT=text       # text | json
PROFILE_ACCESS_LOG_ENABLED=true
```

Every record receives the current `trace_id` and the emitting process hostname as `instance` (text: `instance=<hostname>`, JSON: `"instance": "<hostname>"`), so log lines from multiple replicas stay distinguishable. JSON logs use stable fields including `timestamp`, `level`, `service`, `instance`, `logger`, `event`, `message`, and `trace_id`; request completion records additionally include method, route template, status, duration, response bytes, completion/outcome, authenticated actor/tenant/workspace/source, and business error codes. The request logger never reads request or response bodies and never logs query strings or headers. Values passed to the logging system, including cache identifiers and exception text, are emitted without redaction.

Middleware execution order is `RequestLogging -> TraceId -> MemoryAuth -> routing`. Uvicorn access logging is disabled to avoid duplicate request lines; the service middleware owns access completion logs.

Uvicorn runs with `--http zttp --http2`. Port `8082` accepts HTTP/1.1 and cleartext HTTP/2 prior knowledge (h2c); HTTP/1.1 Upgrade-based h2c and HTTP/2 WebSockets are not supported. Production TLS and ALPN negotiation should be terminated by the Gateway or reverse proxy.

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
