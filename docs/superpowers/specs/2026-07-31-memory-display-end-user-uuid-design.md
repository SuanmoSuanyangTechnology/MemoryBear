# Memory Display End User UUID Design

## Goal

Use `uuid.UUID` consistently for `end_user_id` from the memory-display controller through the service and repository layers, and store `memory_display_records.end_user_id` as a PostgreSQL UUID foreign key to `end_users.id`.

## Scope

- Keep the HTTP query parameter as `str` so the existing application-specific missing/invalid parameter responses remain unchanged.
- Convert and retain `end_user_id` as `uuid.UUID` once in each memory-display controller endpoint.
- Pass UUID values through the engine-display and written-display query services and repositories.
- Keep string conversion only where a textual representation is required, such as deterministic card-ID input and logging.
- Change `MemoryDisplayRecord.end_user_id` from `String(255)` to `UUID(as_uuid=True)` and add its foreign key to `end_users.id`.
- Add a forward Alembic migration rather than editing an already committed historical migration.

## Data Flow

```text
HTTP query string
  -> controller strips and parses uuid.UUID once
  -> service accepts uuid.UUID
  -> repository accepts uuid.UUID
  -> SQLAlchemy compares UUID values with UUID columns
  -> PostgreSQL stores native uuid values
```

The asynchronous write pipeline may continue receiving the identifier as a string because it also sends the identifier to string-oriented storage and infrastructure. `MemoryDisplayRecordService.save_written` will validate/convert it before constructing PostgreSQL records.

## Database Migration

The migration will:

1. Drop the partial lookup index and unique constraint that include `end_user_id`.
2. Convert existing values with `USING end_user_id::uuid`.
3. Add a foreign key to `end_users.id`.
4. Recreate the unique constraint and partial lookup index.

The upgrade intentionally fails if an existing value is not a valid UUID or does not reference an existing end user. This prevents silently preserving corrupt ownership data. The downgrade removes the foreign key and converts UUID values back to `varchar(255)` before recreating the index and constraint.

## Error Handling

- Empty and malformed query parameters retain the current `BizCode` responses.
- The write-side best-effort behavior remains unchanged: invalid identifiers or database failures are logged without failing the primary memory-writing pipeline.
- Repository query methods no longer perform redundant UUID parsing because their typed contract requires `uuid.UUID`.

## Verification

- Controller tests cover empty, invalid, and valid UUID query parameters and confirm the service receives `uuid.UUID`.
- Service/repository tests cover UUID propagation and query construction for both written and engine display records.
- Migration upgrade/downgrade is checked, including preservation of constraints and the partial index.
- Existing memory-display service tests are run to detect response or card-ID regressions.
