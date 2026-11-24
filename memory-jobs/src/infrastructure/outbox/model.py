"""Reference definition of the API-owned storage-outbox table.

The producer stays in the API: every Neo4j memory-node mutation appends a row to
``memory_storage_outbox_events`` in the same business database. This service
only takes over the consumer side, so it needs a query surface, never DDL.

The table is therefore a module-level Core ``Table`` on a private ``MetaData``:
no declarative ``Base``, no ORM class, no ``create_all``. Column names, types,
nullability and database-side defaults mirror ``api/app/models/outbox_model.py``
so that a statement written here compiles to the SQL the API already runs.

Schema knowledge this adapter has to agree on but must not own:

* ``uq_memory_outbox_processing_node`` is a *partial* unique index on
  ``(label, node_id) WHERE status = 'processing'``. It is what turns two
  concurrent claims of the same node into ``IntegrityError``; the repository
  keys its per-row skip on that index name (see
  :data:`PROCESSING_NODE_UNIQUE_INDEX`) instead of re-checking the conflict in
  application code.
* The CHECK constraints are enforced by the migrations: the operation set, the
  status set, ``attempt_count BETWEEN 0 AND 3``, the claim-state invariant
  (lease columns all set exactly while processing) and the terminal-time
  invariant (``processed_at`` xor ``failed_at``). The writer below satisfies
  them; restating them here would only invite drift.
* ``created_at``/``updated_at`` are filled by the producer with the API's
  Python-side ``utcnow_naive`` default, so the database columns carry no server
  default. This service never inserts a row, hence no Python default either.
"""

from __future__ import annotations

import sqlalchemy as sa

#: Physical table name; the API model owns it and migrations create it.
OUTBOX_TABLE_NAME = "memory_storage_outbox_events"

#: Name of the partial unique index on ``(label, node_id)`` where the row is
#: ``processing``. A claim that loses this race rolls back its savepoint and
#: skips the row; any other integrity failure must still surface.
PROCESSING_NODE_UNIQUE_INDEX = "uq_memory_outbox_processing_node"

#: Row states; values fixed by ``ck_memory_outbox_status``.
PENDING = "pending"
PROCESSING = "processing"
PROCESSED = "processed"
FAILED = "failed"

#: Operations; values fixed by ``ck_memory_outbox_operation``.
UPSERT = "upsert"
DELETE = "delete"
DRAFT_DELETE = "draft_delete"

#: The value sets the API's CHECK constraints accept (migrations own them).
STATUSES = frozenset({PENDING, PROCESSING, PROCESSED, FAILED})
OPERATIONS = frozenset({UPSERT, DELETE, DRAFT_DELETE})

#: The outbox rows, as the API producer writes them.
OUTBOX_EVENTS = sa.Table(
    OUTBOX_TABLE_NAME,
    sa.MetaData(),
    sa.Column("id", sa.UUID, primary_key=True),
    sa.Column("sequence", sa.BigInteger, sa.Identity(), nullable=False),
    sa.Column("label", sa.String(64), nullable=False),
    sa.Column("node_id", sa.Text, nullable=False),
    sa.Column("operation", sa.String(20), nullable=False, server_default=UPSERT),
    sa.Column("status", sa.String(20), nullable=False, server_default=PENDING),
    sa.Column(
        "attempt_count", sa.SmallInteger, nullable=False, server_default=sa.text("0")
    ),
    sa.Column("locked_by", sa.String(128)),
    sa.Column("claim_token", sa.UUID),
    sa.Column("locked_at", sa.DateTime),
    sa.Column("heartbeat_at", sa.DateTime),
    sa.Column("last_error", sa.Text),
    sa.Column("created_at", sa.DateTime, nullable=False),
    sa.Column("updated_at", sa.DateTime, nullable=False),
    sa.Column("processed_at", sa.DateTime),
    sa.Column("failed_at", sa.DateTime),
)

__all__ = [
    "DELETE",
    "DRAFT_DELETE",
    "FAILED",
    "OPERATIONS",
    "OUTBOX_EVENTS",
    "OUTBOX_TABLE_NAME",
    "PENDING",
    "PROCESSED",
    "PROCESSING",
    "PROCESSING_NODE_UNIQUE_INDEX",
    "STATUSES",
    "UPSERT",
]
