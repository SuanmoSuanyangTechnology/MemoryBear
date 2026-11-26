"""migrate-parser-config CLI; importing it has no external side effects."""

import argparse
import json
from pathlib import Path

from sqlalchemy.exc import OperationalError

from .migration import inspect_fixture, migrate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preflight or backfill parser configuration")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--fixture", type=Path, help="Offline JSON input; requires --dry-run")
    args = parser.parse_args(argv)
    if args.fixture and args.apply:
        parser.error("--fixture supports --dry-run only")
    try:
        if args.fixture:
            report = inspect_fixture(json.loads(args.fixture.read_text(encoding="utf-8")))
        else:
            from ..application.bootstrap import get_settings
            from ..infrastructure.database.manager import DatabaseManager

            database = DatabaseManager(get_settings())
            try:
                report = migrate(database.sync_session, apply=args.apply)
            finally:
                database.close_sync()
    except Exception as error:
        # Database/validation exceptions may contain bound credentials and connection URLs.
        failure = {"status": "failed", "reason": "migration_input_or_database_error"}
        if isinstance(error, OperationalError):
            from psycopg.errors import ConnectionTimeout

            if isinstance(error.orig, ConnectionTimeout):
                failure["diagnostic"] = "database_connection_timeout"
        print(json.dumps(failure))
        return 1
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.status in {"ready", "applied"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
