"""Read-only preflight, bounded backfill, and independent post-apply verification."""

from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import Table, func, select, text
from sqlalchemy.orm import Session

from ..infrastructure.database.manager import KnowledgeBase
from ..models.owned import Document, DocumentParserConfig, File, Knowledge, KnowledgeParserConfig
from .conversion import KnowledgeContext, to_runtime, validate_values
from .entities import Configuration, ConfigurationError, KnowledgeConfiguration
from .fields import Scope, fields_for
from .historical import from_historical
from .legacy_reference import runtime_differences

BATCH_SIZE = 500
SessionFactory = Callable[[], AbstractContextManager[Session]]


@dataclass
class MigrationReport:
    status: str = "ready"
    counts: dict[str, int] = field(default_factory=lambda: {"knowledges": 0, "documents": 0})
    defaulted: dict[str, int] = field(default_factory=dict)
    normalized: dict[str, int] = field(default_factory=dict)
    delimiters: dict[str, int] = field(default_factory=dict)
    parent_child_qa_zeroed: int = 0
    extras_keys: list[str] = field(default_factory=list)
    unconvertible: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    repairs: list[dict[str, str]] = field(default_factory=list)
    source_pending: list[dict[str, Any]] = field(default_factory=list)
    batches: dict[str, list[int]] = field(
        default_factory=lambda: {"knowledges": [], "documents": []}
    )
    validation: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _extras_keys(data: Mapping[str, Any], prefix: str = "") -> Iterator[str]:
    for key, value in data.items():
        path = prefix + key
        yield path
        if isinstance(value, dict):
            yield from _extras_keys(value, path + ".")


def inspect_record(
    row: Mapping[str, Any], scope: Scope, context: KnowledgeContext, report: MigrationReport
) -> Configuration | None:
    table_name = "knowledges" if scope == Scope.KNOWLEDGE else "documents"
    report.counts[table_name] += 1
    try:
        result = from_historical(row["parser_config"], scope, context=context)
        report.repairs.extend(
            {"table": table_name, "id": str(row["id"]), "field": name, "reason": reason}
            for name, reason in sorted(result.report.repairs.items())
        )
        if isinstance(result.config, KnowledgeConfiguration) and result.config.source_issues():
            report.source_pending.append(
                {
                    "table": table_name,
                    "id": str(row["id"]),
                    "fields": [issue.field for issue in result.config.source_issues()],
                }
            )
        for name in result.report.defaulted:
            key = table_name + "." + name
            report.defaulted[key] = report.defaulted.get(key, 0) + 1
        for name in result.report.normalized:
            key = table_name + "." + name
            report.normalized[key] = report.normalized.get(key, 0) + 1
        report.parent_child_qa_zeroed += int(result.report.parent_child_qa_zeroed)
        report.extras_keys = sorted(
            set(report.extras_keys)
            | {table_name + "." + name for name in _extras_keys(result.config.extras)}
        )
        for name in ("delimiters", "parent_chunk_delimiters"):
            values = getattr(result.config, name)
            for category, count in {
                "records": 1,
                "items": len(values),
                "empty": int(not values),
                "multichar": sum(len(item) > 1 for item in values),
                "duplicates": len(values) - len(set(values)),
            }.items():
                key = table_name + "." + name + "." + category
                report.delimiters[key] = report.delimiters.get(key, 0) + count
        differences = runtime_differences(
            result.comparison_data, result.config, to_runtime(result.config)
        )
        if differences:
            raise ConfigurationError("runtime_mismatch", ",".join(differences))
        return result.config
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        report.unconvertible.append(
            {
                "table": table_name,
                "id": str(row["id"]),
                "reason": exc.code
                if isinstance(exc, ConfigurationError)
                else "legacy_runtime_invalid",
                "field": exc.field if isinstance(exc, ConfigurationError) else "configuration",
            }
        )
        report.status = "blocked"
        return None


def _context(row: Mapping[str, Any], urls: tuple[str | None, ...] = ()) -> KnowledgeContext:
    return KnowledgeContext(
        knowledge_type=row["type"],
        image2text_id=row.get("image2text_id"),
        audio2text_id=row.get("audio2text_id"),
        video2text_id=row.get("video2text_id"),
        file_urls=urls,
    )


def _page(session: Session, table: Table, last_id: UUID | None) -> list[Mapping[str, Any]]:
    statement = select(table).order_by(table.c.id).limit(BATCH_SIZE)
    if last_id is not None:
        statement = statement.where(table.c.id > last_id)
    return [dict(row) for row in session.execute(statement).mappings()]


def _contexts(
    session: Session, rows: list[Mapping[str, Any]], scope: Scope
) -> dict[UUID, KnowledgeContext]:
    owners = rows
    if scope == Scope.DOCUMENT:
        ids = {row["kb_id"] for row in rows}
        owners = [
            dict(row)
            for row in session.execute(
                select(Knowledge.__table__).where(Knowledge.id.in_(ids))
            ).mappings()
        ]
    third_party_ids = [row["id"] for row in owners if row["type"] == "Third-party"]
    urls: dict[UUID, list[str | None]] = {}
    if third_party_ids and scope == Scope.KNOWLEDGE:
        for owner_id, url in session.execute(
            select(File.kb_id, File.file_url).where(
                File.kb_id.in_(third_party_ids),
                File.file_role == "source",
            )
        ):
            urls.setdefault(owner_id, []).append(url)
    return {row["id"]: _context(row, tuple(urls.get(row["id"], []))) for row in owners}


def _convert_page(
    session: Session, rows: list[Mapping[str, Any]], scope: Scope, report: MigrationReport
) -> list[tuple[Mapping[str, Any], Configuration]]:
    contexts = _contexts(session, rows, scope)
    converted = []
    for row in rows:
        owner_id = row["id"] if scope == Scope.KNOWLEDGE else row["kb_id"]
        if owner_id not in contexts:
            _skip_orphan_document(row, report)
            continue
        config = inspect_record(row, scope, contexts[owner_id], report)
        if config is not None:
            converted.append((row, config))
    return converted


def _skip_orphan_document(row: Mapping[str, Any], report: MigrationReport) -> None:
    report.counts["documents"] += 1
    report.skipped.append({
        "table": "documents", "id": str(row["id"]),
        "kb_id": str(row["kb_id"]), "reason": "missing_knowledge",
    })


def _scan(session: Session, report: MigrationReport) -> None:
    for scope, model in ((Scope.KNOWLEDGE, Knowledge), (Scope.DOCUMENT, Document)):
        last_id = None
        while rows := _page(session, KnowledgeBase.metadata.tables[model.__tablename__], last_id):
            _convert_page(session, rows, scope, report)
            last_id = rows[-1]["id"]


def _write_page(
    session: Session, rows: list[tuple[Mapping[str, Any], Configuration]], scope: Scope
) -> None:
    if not rows:
        return
    table = (KnowledgeParserConfig if scope == Scope.KNOWLEDGE else DocumentParserConfig).__table__
    key = "knowledge_id" if scope == Scope.KNOWLEDGE else "document_id"
    existing = set(
        session.scalars(
            select(table.c[key]).where(table.c[key].in_([row["id"] for row, _ in rows]))
        )
    )
    for row, config in rows:
        values = {
            spec.column: getattr(config, spec.column)
            for spec in fields_for(scope)
            if not spec.owner
        }
        values["extras"] = config.extras
        if row["id"] in existing:
            session.execute(table.update().where(table.c[key] == row["id"]).values(**values))
        else:
            session.execute(table.insert().values(**{key: row["id"]}, **values))
        if isinstance(config, KnowledgeConfiguration):
            # Migration preserves original snapshots; daily writes use the repository instead.
            session.execute(
                KnowledgeBase.metadata.tables["knowledges"]
                .update()
                .where(Knowledge.id == row["id"])
                .values(
                    source=config.source,
                    index_strategy=config.index_strategy,
                )
            )


def validate_migration(session: Session) -> dict[str, Any]:
    counts: dict[str, int] = {}
    skipped_documents = 0
    for scope, owner, config_model in (
        (Scope.KNOWLEDGE, Knowledge, KnowledgeParserConfig),
        (Scope.DOCUMENT, Document, DocumentParserConfig),
    ):
        table = config_model.__table__
        key = "knowledge_id" if scope == Scope.KNOWLEDGE else "document_id"
        owner_count_query = select(func.count()).select_from(owner)
        config_count_query = select(func.count()).select_from(table)
        if scope == Scope.DOCUMENT:
            has_knowledge = select(Knowledge.id).where(Knowledge.id == Document.kb_id).exists()
            owner_count_query = owner_count_query.where(has_knowledge)
            config_count_query = config_count_query.where(
                table.c.document_id.in_(select(Document.id).where(has_knowledge))
            )
        owner_count = session.scalar(owner_count_query)
        config_count = session.scalar(config_count_query)
        if owner_count != config_count:
            raise ConfigurationError("row_count_mismatch", table.name)
        counts[table.name] = config_count or 0
        last_id = None
        while rows := _page(session, KnowledgeBase.metadata.tables[owner.__tablename__], last_id):
            report = MigrationReport()
            expected_rows = _convert_page(session, rows, scope, report)
            skipped_documents += len(report.skipped)
            if report.unconvertible:
                raise ConfigurationError("post_apply_runtime_mismatch")
            saved = {
                row[key]: row
                for row in session.execute(
                    select(table).where(table.c[key].in_([row["id"] for row in rows]))
                ).mappings()
            }
            for original, expected in expected_rows:
                row = saved.get(original["id"])
                if row is None:
                    raise ConfigurationError("configuration_missing", table.name)
                values = {
                    spec.column: row[spec.column] for spec in fields_for(scope) if not spec.owner
                }
                values["extras"] = row["extras"]
                if scope == Scope.KNOWLEDGE:
                    values.update(
                        knowledge_type=original["type"],
                        source=original["source"],
                        index_strategy=original["index_strategy"],
                    )
                    values.update(
                        {
                            spec.column: original[spec.column]
                            for spec in fields_for(scope)
                            if spec.owner
                        }
                    )
                actual = validate_values(values, scope)
                if actual != expected:
                    raise ConfigurationError("stored_configuration_mismatch", table.name)
                # _convert_page independently checked the repaired input key by key;
                # full entity equality also covers stored defaults, extras and source fields.
            last_id = rows[-1]["id"]
    return {
        "passed": True,
        "counts": counts,
        "skipped_documents": skipped_documents,
        "checks": [
            "owner_counts_including_folders",
            "typed_entities",
            "source_type",
            "source_readiness",
            "chunk_method",
            "index_strategy",
            "multimodal",
            "document_model_inheritance",
            "runtime_values",
            "registered_repairs",
            "canonical_delimiter_strings",
        ],
    }


def migrate(session_factory: SessionFactory, *, apply: bool = False) -> MigrationReport:
    report = MigrationReport()
    with session_factory() as session, session.no_autoflush:
        if session.get_bind().dialect.name == "postgresql":
            session.execute(text("SET TRANSACTION READ ONLY"))
        _scan(session, report)
    if not apply or report.unconvertible:
        return report
    try:
        for scope, model in ((Scope.KNOWLEDGE, Knowledge), (Scope.DOCUMENT, Document)):
            last_id = None
            while True:
                with session_factory() as session, session.begin():
                    rows = _page(
                        session, KnowledgeBase.metadata.tables[model.__tablename__], last_id
                    )
                    if not rows:
                        break
                    check = MigrationReport()
                    converted = _convert_page(session, rows, scope, check)
                    if check.unconvertible:
                        report.unconvertible.extend(check.unconvertible)
                        raise ConfigurationError("batch_conversion_failed")
                    _write_page(session, converted, scope)
                report.batches[model.__tablename__].append(len(converted))
                last_id = rows[-1]["id"]
        with session_factory() as session:
            report.validation = validate_migration(session)
        report.status = "applied"
    except Exception as exc:
        # Earlier completed batches remain committed. The failing batch is fully rolled back.
        report.status = "failed"
        report.validation = {
            "passed": False,
            "reason": (
                exc.code if isinstance(exc, ConfigurationError) else "database_operation_failed"
            ),
        }
    return report


def inspect_fixture(data: Mapping[str, Any]) -> MigrationReport:
    """Inspect synthetic/explicitly supplied JSON; never initialize runtime settings or a DB."""
    report = MigrationReport()
    owners = {row["id"]: row for row in data.get("knowledges", [])}
    for table, scope in (("knowledges", Scope.KNOWLEDGE), ("documents", Scope.DOCUMENT)):
        for row in data.get(table, []):
            owner = owners.get(row["id"] if scope == Scope.KNOWLEDGE else row["kb_id"])
            if owner is None:
                _skip_orphan_document(row, report)
                continue
            normalized = dict(owner)
            for key in ("image2text_id", "audio2text_id", "video2text_id"):
                if normalized.get(key) is not None:
                    normalized[key] = UUID(normalized[key])
            inspect_record(
                row, scope, _context(normalized, tuple(owner.get("file_urls", []))), report
            )
    return report
