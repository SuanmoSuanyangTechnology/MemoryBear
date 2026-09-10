"""Bulk-migrate memory nodes from Neo4j into Elasticsearch.

Run from the API project directory::

    python scripts/migrate_neo4j_to_elasticsearch.py
    python scripts/migrate_neo4j_to_elasticsearch.py --labels Dialogue Statement
    python scripts/migrate_neo4j_to_elasticsearch.py --checkpoint .migration.json
    python scripts/migrate_neo4j_to_elasticsearch.py --clear-target --yes

Only production memory node labels registered in ``INDEX_DEFINITIONS`` are
migrated. Relationships are intentionally excluded because the memory storage
Elasticsearch provider has no relationship indices.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import random
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence, TypeVar

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from elasticsearch import (  # noqa: E402
    ApiError,
    AsyncElasticsearch,
    ConnectionError as ElasticsearchConnectionError,
    ConnectionTimeout as ElasticsearchConnectionTimeout,
)
from neo4j.exceptions import (  # noqa: E402
    ServiceUnavailable,
    SessionExpired,
    TransientError,
)

from app.core.memory.storage.enums import MemoryNodeType  # noqa: E402
from app.core.memory.storage.provider.elasticsearch.client import (  # noqa: E402
    ElasticClient,
)
from app.core.memory.storage.provider.elasticsearch.index import (  # noqa: E402
    INDEX_DEFINITIONS,
    get_index_name,
)
from app.core.memory.storage.provider.elasticsearch.serialization import (  # noqa: E402
    normalize_elasticsearch_document,
)
from app.core.memory.storage.provider.neo4j.client import Neo4jClient  # noqa: E402

logger = logging.getLogger("neo4j_to_elasticsearch")

DEFAULT_BATCH_SIZE = 1_000
DEFAULT_CONCURRENCY = 2
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BASE_DELAY = 1.0
DEFAULT_RETRY_MAX_DELAY = 30.0
MAX_BATCH_SIZE = 5_000
MAX_RETRIES = 20
CHECKPOINT_VERSION = 4

_RETRYABLE_ES_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
_T = TypeVar("_T")

_ID_PROPERTIES: dict[MemoryNodeType, str] = {
    MemoryNodeType.COMMUNITY: "community_id",
}
_BULK_FILTER_PATH = (
    "errors",
    "items.*._id",
    "items.*.status",
    "items.*.error",
)


class RetryableMigrationError(RuntimeError):
    """A transient acknowledgement failure safe to retry idempotently."""


def _is_retryable_exception(exc: Exception) -> bool:
    if isinstance(
        exc,
        (
            ElasticsearchConnectionError,
            ElasticsearchConnectionTimeout,
            ServiceUnavailable,
            SessionExpired,
            TransientError,
        ),
    ):
        return True
    return (
        isinstance(exc, ApiError)
        and exc.status_code in _RETRYABLE_ES_STATUS_CODES
    ) or isinstance(exc, RetryableMigrationError)


_PREFLIGHT_QUERY = """
MATCH (n:`{label}`)
{where_clause}
RETURN count(n) AS total,
       count(n.{id_property}) AS with_id,
       count(DISTINCT toString(n.{id_property})) AS distinct_ids,
       count(CASE
           WHEN n.{id_property} = toString(n.{id_property}) THEN 1
       END) AS string_ids
"""

_PAGE_QUERY = """
MATCH (n:`{label}`)
WHERE n.{id_property} IS NOT NULL
{cursor_clause}
{scope_clause}
WITH n, {cursor_expression} AS cursor
ORDER BY cursor
LIMIT $batch_size
RETURN properties(n) AS node, cursor
"""


@dataclass(frozen=True, slots=True)
class MigrationOptions:
    batch_size: int = DEFAULT_BATCH_SIZE
    concurrency: int = DEFAULT_CONCURRENCY
    end_user_id: str | None = None
    active_only: bool = False
    clear_target: bool = False
    dry_run: bool = False
    skip_invalid: bool = False
    refresh: bool = True
    max_retries: int = DEFAULT_MAX_RETRIES
    retry_base_delay: float = DEFAULT_RETRY_BASE_DELAY
    retry_max_delay: float = DEFAULT_RETRY_MAX_DELAY

    def __post_init__(self) -> None:
        if not 1 <= self.batch_size <= MAX_BATCH_SIZE:
            raise ValueError(
                f"batch_size must be between 1 and {MAX_BATCH_SIZE}"
            )
        if self.concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        if not 0 <= self.max_retries <= MAX_RETRIES:
            raise ValueError(
                f"max_retries must be between 0 and {MAX_RETRIES}"
            )
        if (
            not math.isfinite(self.retry_base_delay)
            or self.retry_base_delay < 0
        ):
            raise ValueError(
                "retry_base_delay must be a finite non-negative number"
            )
        if (
            not math.isfinite(self.retry_max_delay)
            or self.retry_max_delay < self.retry_base_delay
        ):
            raise ValueError(
                "retry_max_delay must be at least retry_base_delay"
            )


@dataclass(slots=True)
class LabelMigrationStats:
    label: str
    source_total: int = 0
    migrated: int = 0
    skipped: int = 0
    cleared: int = 0
    batches: int = 0
    elapsed_seconds: float = 0.0
    resumed: bool = False


class CheckpointStore:
    """Small atomic JSON checkpoint keyed by node label."""

    def __init__(
        self,
        path: Path | None,
        scope: Mapping[str, Any],
        *,
        reset: bool = False,
    ) -> None:
        self.path = path
        self.scope = dict(scope)
        self._lock = asyncio.Lock()
        self._state: dict[str, Any] = {
            "version": CHECKPOINT_VERSION,
            "scope": self.scope,
            "labels": {},
        }
        if path is None:
            return
        if reset and path.exists():
            path.unlink()
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if loaded.get("version") != CHECKPOINT_VERSION:
                raise ValueError(
                    "unsupported checkpoint version; rerun with "
                    "--reset-checkpoint"
                )
            if loaded.get("scope") != self.scope:
                raise ValueError(
                    "checkpoint scope does not match labels/end-user/active-only options"
                )
            if not isinstance(loaded.get("labels"), dict):
                raise ValueError("checkpoint labels payload is invalid")
            self._state = loaded

    @property
    def has_progress(self) -> bool:
        return bool(self._state["labels"])

    def label_state(self, label: MemoryNodeType) -> dict[str, Any]:
        value = self._state["labels"].get(label.value, {})
        return dict(value) if isinstance(value, Mapping) else {}

    async def save(
        self,
        label: MemoryNodeType,
        *,
        after_id: str | None,
        migrated: int,
        skipped: int,
        completed: bool,
    ) -> None:
        if self.path is None:
            return
        async with self._lock:
            self._state["labels"][label.value] = {
                "after_id": after_id,
                "migrated": migrated,
                "skipped": skipped,
                "completed": completed,
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)


class Neo4jToElasticsearchMigrator:
    def __init__(
        self,
        neo4j: Neo4jClient,
        elasticsearch: AsyncElasticsearch,
        options: MigrationOptions,
        checkpoint: CheckpointStore,
    ) -> None:
        self.neo4j = neo4j
        self.elasticsearch = elasticsearch
        self.options = options
        self.checkpoint = checkpoint
        self._semaphore = asyncio.Semaphore(options.concurrency)
        self._date_fields_by_label: dict[
            MemoryNodeType, frozenset[str]
        ] = {}
        for migration_label, definition in INDEX_DEFINITIONS.items():
            properties = definition.mappings.get("properties", {})
            self._date_fields_by_label[migration_label] = frozenset(
                field
                for field, mapping in properties.items()
                if isinstance(mapping, Mapping)
                and mapping.get("type") == "date"
            )

    async def _with_retry(
        self,
        label: MemoryNodeType,
        operation: str,
        call: Callable[[], Awaitable[_T]],
    ) -> _T:
        for retry_number in range(self.options.max_retries + 1):
            try:
                return await call()
            except Exception as exc:
                if (
                    not _is_retryable_exception(exc)
                    or retry_number >= self.options.max_retries
                ):
                    raise
                ceiling = min(
                    self.options.retry_max_delay,
                    self.options.retry_base_delay * (2 ** retry_number),
                )
                delay = random.uniform(ceiling / 2, ceiling) if ceiling else 0
                logger.warning(
                    "label=%s operation=%s transient_error=%s "
                    "retry=%d/%d delay=%.2fs error=%s",
                    label.value,
                    operation,
                    type(exc).__name__,
                    retry_number + 1,
                    self.options.max_retries,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
        raise AssertionError("retry loop exited unexpectedly")

    async def run(
        self,
        labels: Sequence[MemoryNodeType],
    ) -> list[LabelMigrationStats]:
        return list(
            await asyncio.gather(
                *(self._run_limited(label) for label in labels)
            )
        )

    async def _run_limited(
        self,
        label: MemoryNodeType,
    ) -> LabelMigrationStats:
        async with self._semaphore:
            return await self.migrate_label(label)

    async def migrate_label(
        self,
        label: MemoryNodeType,
    ) -> LabelMigrationStats:
        started = time.perf_counter()
        state = self.checkpoint.label_state(label)
        stats = LabelMigrationStats(
            label=label.value,
            migrated=int(state.get("migrated", 0) or 0),
            skipped=int(state.get("skipped", 0) or 0),
            resumed=bool(state),
        )
        if state.get("completed"):
            logger.info("label=%s already completed in checkpoint", label.value)
            return stats

        source_total, missing_ids, native_cursor = await self._preflight(label)
        stats.source_total = source_total
        logger.info(
            "label=%s cursor_mode=%s",
            label.value,
            "native-indexed" if native_cursor else "toString-fallback",
        )
        stats.skipped = max(stats.skipped, missing_ids)
        if missing_ids and not self.options.skip_invalid:
            id_property = _ID_PROPERTIES.get(label, "id")
            raise RuntimeError(
                f"{label.value} contains {missing_ids} nodes without "
                f"{id_property}; rerun with --skip-invalid to ignore them"
            )

        if self.options.clear_target:
            stats.cleared = await self._clear_target(label)

        after_id = state.get("after_id")
        if after_id is not None:
            after_id = str(after_id)

        rows = await self._fetch_page(label, after_id, native_cursor)
        while rows:
            documents = [self._normalize_row(label, row) for row in rows]
            next_after_id = str(rows[-1]["cursor"])
            # Keep only one page in flight: overlap Neo4j network latency with
            # the current Elasticsearch bulk request without changing commit
            # or checkpoint order.
            next_page_task = (
                asyncio.create_task(
                    self._fetch_page(label, next_after_id, native_cursor)
                )
                if not self.options.dry_run
                else None
            )
            try:
                if not self.options.dry_run:
                    await self._with_retry(
                        label,
                        "elasticsearch bulk",
                        lambda: self._bulk_index(label, documents),
                    )
                stats.migrated += len(documents)
                stats.batches += 1
                after_id = next_after_id
                await self.checkpoint.save(
                    label,
                    after_id=after_id,
                    migrated=stats.migrated,
                    skipped=stats.skipped,
                    completed=False,
                )
                logger.info(
                    "label=%s migrated=%d/%d batches=%d cursor=%s",
                    label.value,
                    stats.migrated,
                    max(0, source_total - missing_ids),
                    stats.batches,
                    after_id,
                )
                rows = (
                    await next_page_task
                    if next_page_task is not None
                    else await self._fetch_page(
                        label, after_id, native_cursor
                    )
                )
            except BaseException:
                if next_page_task is not None:
                    next_page_task.cancel()
                    await asyncio.gather(
                        next_page_task,
                        return_exceptions=True,
                    )
                raise

        expected = source_total - missing_ids
        if stats.migrated != expected:
            raise RuntimeError(
                f"{label.value} migration count mismatch: "
                f"expected={expected} migrated={stats.migrated}; "
                "source data may have changed during migration"
            )
        if self.options.refresh and not self.options.dry_run:
            await self._with_retry(
                label,
                "elasticsearch refresh",
                lambda: self.elasticsearch.indices.refresh(
                    index=get_index_name(label)
                ),
            )
        await self.checkpoint.save(
            label,
            after_id=after_id,
            migrated=stats.migrated,
            skipped=stats.skipped,
            completed=True,
        )
        stats.elapsed_seconds = time.perf_counter() - started
        logger.info(
            "label=%s complete migrated=%d skipped=%d cleared=%d elapsed=%.2fs",
            label.value,
            stats.migrated,
            stats.skipped,
            stats.cleared,
            stats.elapsed_seconds,
        )
        return stats

    async def _preflight(
        self,
        label: MemoryNodeType,
    ) -> tuple[int, int, bool]:
        conditions: list[str] = []
        parameters: dict[str, Any] = {}
        if self.options.end_user_id is not None:
            conditions.append("n.end_user_id = $end_user_id")
            parameters["end_user_id"] = self.options.end_user_id
        if self.options.active_only:
            conditions.append("n.delete_at IS NULL")
        where_clause = (
            "WHERE " + " AND ".join(conditions) if conditions else ""
        )
        id_property = _ID_PROPERTIES.get(label, "id")
        query = _PREFLIGHT_QUERY.format(
            label=label.value,
            id_property=id_property,
            where_clause=where_clause,
        )
        rows = await self._with_retry(
            label,
            "neo4j preflight",
            lambda: self.neo4j.execute_query(query, **parameters),
        )
        row = rows[0] if rows else {}
        total = int(row.get("total", 0) or 0)
        with_id = int(row.get("with_id", 0) or 0)
        distinct_ids = int(row.get("distinct_ids", 0) or 0)
        string_ids = int(row.get("string_ids", 0) or 0)
        if distinct_ids != with_id:
            raise RuntimeError(
                f"{label.value} contains duplicate business ids: "
                f"with_id={with_id} distinct_ids={distinct_ids}"
            )
        return total, total - with_id, string_ids == with_id

    async def _fetch_page(
        self,
        label: MemoryNodeType,
        after_id: str | None,
        native_cursor: bool,
    ) -> list[dict[str, Any]]:
        scope_conditions: list[str] = []
        parameters: dict[str, Any] = {
            "after_id": after_id,
            "batch_size": self.options.batch_size,
        }
        if self.options.end_user_id is not None:
            scope_conditions.append("  AND n.end_user_id = $end_user_id")
            parameters["end_user_id"] = self.options.end_user_id
        if self.options.active_only:
            scope_conditions.append("  AND n.delete_at IS NULL")
        id_property = _ID_PROPERTIES.get(label, "id")
        query = _PAGE_QUERY.format(
            label=label.value,
            id_property=id_property,
            cursor_expression=(
                f"n.{id_property}"
                if native_cursor
                else f"toString(n.{id_property})"
            ),
            cursor_clause=(
                (
                    f"  AND n.{id_property} > $after_id"
                    if native_cursor
                    else f"  AND toString(n.{id_property}) > $after_id"
                )
                if after_id is not None
                else ""
            ),
            scope_clause="\n".join(scope_conditions),
        )
        return await self._with_retry(
            label,
            "neo4j page",
            lambda: self.neo4j.execute_query(query, **parameters),
        )

    def _normalize_row(
        self,
        label: MemoryNodeType,
        row: Mapping[str, Any],
    ) -> dict[str, Any]:
        source = row.get("node")
        cursor = row.get("cursor")
        if not isinstance(source, Mapping):
            raise RuntimeError(f"{label.value} returned a non-object node")
        document = normalize_elasticsearch_document(
            source,
            date_fields=self._date_fields_by_label[label],
        )
        id_property = _ID_PROPERTIES.get(label, "id")
        node_id = document.get(id_property)
        if node_id is None or not str(node_id).strip():
            raise RuntimeError(
                f"{label.value} returned a node without {id_property}"
            )
        if cursor is None or str(node_id) != str(cursor):
            raise RuntimeError(
                f"{label.value} cursor does not match the document id"
            )
        canonical_id = str(node_id)
        document[id_property] = canonical_id
        document["id"] = canonical_id
        return document

    async def _bulk_index(
        self,
        label: MemoryNodeType,
        documents: Sequence[Mapping[str, Any]],
    ) -> None:
        operations: list[dict[str, Any]] = []
        index_name = get_index_name(label)
        for document in documents:
            operations.append(
                {
                    "index": {
                        "_index": index_name,
                        "_id": str(document["id"]),
                    }
                }
            )
            operations.append(dict(document))
        response = await self.elasticsearch.bulk(
            operations=operations,
            refresh=False,
            filter_path=_BULK_FILTER_PATH,
        )
        items = response.get("items", [])
        if response.get("errors") or len(items) != len(documents):
            failures: list[str] = []
            failure_statuses: list[int] = []
            for item in items:
                if not isinstance(item, Mapping) or not item:
                    continue
                result = next(iter(item.values()))
                if not isinstance(result, Mapping) or "error" not in result:
                    continue
                error = result.get("error")
                status = result.get("status")
                failure_statuses.append(
                    status
                    if isinstance(status, int)
                    and not isinstance(status, bool)
                    else -1
                )
                error_type = (
                    error.get("type")
                    if isinstance(error, Mapping)
                    else type(error).__name__
                )
                if len(failures) < 5:
                    failures.append(
                        f"id={result.get('_id')} type={error_type}"
                    )
            message = (
                f"Elasticsearch bulk failed for {label.value}: "
                + (", ".join(failures) or "invalid bulk acknowledgement")
            )
            if failure_statuses and all(
                status in _RETRYABLE_ES_STATUS_CODES
                for status in failure_statuses
            ):
                raise RetryableMigrationError(message)
            raise RuntimeError(message)

    async def _clear_target(self, label: MemoryNodeType) -> int:
        if self.options.dry_run:
            return 0
        query: dict[str, Any]
        if self.options.end_user_id is None:
            query = {"match_all": {}}
        else:
            query = {"term": {"end_user_id": self.options.end_user_id}}
        result = await self._with_retry(
            label,
            "elasticsearch delete_by_query",
            lambda: self.elasticsearch.delete_by_query(
                index=get_index_name(label),
                query=query,
                conflicts="proceed",
                refresh=True,
            ),
        )
        deleted = result.get("deleted", 0)
        if not isinstance(deleted, int) or isinstance(deleted, bool):
            raise RuntimeError(
                f"Elasticsearch clear returned invalid count for {label.value}"
            )
        return deleted


def _parse_labels(values: Sequence[str] | None) -> list[MemoryNodeType]:
    supported = {label.value.lower(): label for label in INDEX_DEFINITIONS}
    supported.update({label.name.lower(): label for label in INDEX_DEFINITIONS})
    if not values:
        return sorted(INDEX_DEFINITIONS, key=lambda item: item.value)

    labels: list[MemoryNodeType] = []
    for value in values:
        for token in value.split(","):
            normalized = token.strip().lower()
            if not normalized:
                continue
            try:
                label = supported[normalized]
            except KeyError:
                choices = ", ".join(
                    sorted(item.value for item in INDEX_DEFINITIONS)
                )
                raise ValueError(
                    f"unsupported label {token!r}; expected one of: {choices}"
                ) from None
            if label not in labels:
                labels.append(label)
    if not labels:
        raise ValueError("at least one label is required")
    return labels


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bulk-migrate memory nodes from Neo4j to Elasticsearch.",
    )
    parser.add_argument(
        "--labels",
        nargs="+",
        help="Node labels (space- or comma-separated); defaults to all.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Documents per bulk request (1-{MAX_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help="Maximum labels migrated concurrently.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help=f"Retries for transient failures (0-{MAX_RETRIES}).",
    )
    parser.add_argument(
        "--retry-base-delay",
        type=float,
        default=DEFAULT_RETRY_BASE_DELAY,
        help="Initial retry delay in seconds before jitter.",
    )
    parser.add_argument(
        "--retry-max-delay",
        type=float,
        default=DEFAULT_RETRY_MAX_DELAY,
        help="Maximum retry delay in seconds before jitter.",
    )
    parser.add_argument(
        "--end-user-id",
        help="Only migrate documents owned by this end user.",
    )
    parser.add_argument(
        "--active-only",
        action="store_true",
        help="Exclude Neo4j nodes whose delete_at is set.",
    )
    parser.add_argument(
        "--clear-target",
        action="store_true",
        help="Delete matching ES documents before migration (requires --yes).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm destructive --clear-target operation.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read and validate all source nodes without writing ES.",
    )
    parser.add_argument(
        "--skip-invalid",
        action="store_true",
        help="Skip Neo4j nodes without a business id instead of failing.",
    )
    parser.add_argument(
        "--no-refresh",
        action="store_true",
        help="Do not refresh ES aliases after each completed label.",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        help="JSON checkpoint path for resumable migration.",
    )
    parser.add_argument(
        "--reset-checkpoint",
        action="store_true",
        help="Delete the checkpoint before starting.",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    labels = _parse_labels(args.labels)
    options = MigrationOptions(
        batch_size=args.batch_size,
        concurrency=args.concurrency,
        end_user_id=args.end_user_id,
        active_only=args.active_only,
        clear_target=args.clear_target,
        dry_run=args.dry_run,
        skip_invalid=args.skip_invalid,
        refresh=not args.no_refresh,
        max_retries=args.max_retries,
        retry_base_delay=args.retry_base_delay,
        retry_max_delay=args.retry_max_delay,
    )
    scope = {
        "labels": [label.value for label in labels],
        "end_user_id": options.end_user_id,
        "active_only": options.active_only,
        "dry_run": options.dry_run,
    }
    checkpoint = CheckpointStore(
        args.checkpoint,
        scope,
        reset=args.reset_checkpoint,
    )
    if options.clear_target and checkpoint.has_progress:
        raise ValueError(
            "--clear-target cannot resume a populated checkpoint; "
            "use --reset-checkpoint"
        )

    neo4j = await Neo4jClient.create()
    elastic = await ElasticClient.create()
    try:
        migrator = Neo4jToElasticsearchMigrator(
            neo4j,
            elastic._require_client(),
            options,
            checkpoint,
        )
        stats = await migrator.run(labels)
    finally:
        await asyncio.gather(
            neo4j.close(),
            elastic.close(),
            return_exceptions=True,
        )

    print(json.dumps([asdict(item) for item in stats], ensure_ascii=False, indent=2))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.clear_target and not args.yes:
        parser.error("--clear-target is destructive and requires --yes")
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        logger.error("migration interrupted")
        return 130
    except Exception as exc:
        logger.error("migration failed: %s", exc, exc_info=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
