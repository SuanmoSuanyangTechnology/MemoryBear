"""Database query builtin tool - PostgreSQL connection and arbitrary SQL execution

Purpose
-------
This tool holds **no preconfigured static connection info**. The tool management page
exposes only an enable switch (``is_enabled``) for it and no parameters for the admin
to prefill (unlike builtin tools such as Baidu Search or OpenClaw that require a key
to be configured before use).

- Invocation parameters (provided by the Agent/workflow on each call): database
  server connection info ``host`` / ``port`` / ``user`` / ``password`` / ``driver`` /
  ``connect_timeout`` plus the target ``database`` and the ``sql`` to run. That is,
  which server and which database to connect to is decided by the caller on every
  call, not bounded by any admin-configured scope.
- The tool instance holds and caches no cross-call database connection: every
  ``execute()`` call opens a connection temporarily with the info passed in that
  call and closes it immediately afterwards.
- Currently only PostgreSQL is supported (via psycopg2-binary).
- The "test database connection" panel on the tool management page calls
  ``POST /tools/execution/execute`` (i.e. this class's ``execute()``) directly with
  the full driver/host/port/user/password/database/sql, simulating a real call with
  no simplification and without persisting or storing these parameters.

⚠️ Risk notice
--------------
This tool allows executing **arbitrary SQL statements** (including SELECT / INSERT /
UPDATE / DELETE / DROP / TRUNCATE / ALTER and other writes and DDL) with no keyword
blacklist, no statement count limit and no table/schema restriction. Only the most
basic safeguards are kept:
  - automatic ``ROLLBACK`` on execution errors, so a connection does not stay in a
    failed transaction;
  - the connection is closed immediately after each call;
  - audit log entries are recorded on execution (host/database/user/SQL text,
    excluding the password) for later tracing.
Because the connection info is itself an invocation parameter (rather than a fixed
configuration pre-reviewed by an admin), a mistaken operation (or an operation
induced by prompt injection) may connect to an arbitrary database and cause
irreversible data loss. Enable this tool only where that risk is acceptable, and
handle the passing and retention of database passwords with care when composing
Agents/workflows.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, time as time_cls
from decimal import Decimal
from typing import Any, Dict, List, Optional

from ..base import ParameterType, ToolParameter, ToolResult
from .base import BuiltinTool


logger = logging.getLogger(__name__)

# Default database name (shipped with PostgreSQL) used for connectivity tests /
# when no database is specified
_DEFAULT_TEST_DATABASE = "postgres"

# ---------------------------------------------------------------------------
# PostgreSQL connection helpers (previously db_connection.py, inlined here to
# avoid a cross-module dependency)
# ---------------------------------------------------------------------------
# Currently only postgresql is supported (the driver field is reserved for
# future database backends)
_SUPPORTED_DRIVERS = ("postgresql",)

_PG_DRIVER = None  # psycopg2 module (lazy-loaded to avoid a hard import-time dependency)


def _load_pg_driver():
    """Lazy-load psycopg2 to avoid a hard import-time dependency on the driver."""
    global _PG_DRIVER
    if _PG_DRIVER is None:
        try:
            import psycopg2  # type: ignore
            import psycopg2.extras  # type: ignore  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "缺少 PostgreSQL 驱动 psycopg2，请先 pip install psycopg2-binary"
            ) from e
        _PG_DRIVER = psycopg2
    return _PG_DRIVER


def _is_pg_driver(driver: Optional[str]) -> bool:
    return (driver or "postgresql").lower() in _SUPPORTED_DRIVERS


def _open_pg_connection(conn_args: Dict[str, Any]):
    """Open a PostgreSQL connection synchronously from the connection args
    (a blocking call — run it in a thread pool)

    Args:
        conn_args: dict with driver/host/port/user/password/database/connect_timeout

    Returns:
        psycopg2 connection object

    Raises:
        ValueError: unsupported driver or missing required connection parameters
        RuntimeError: psycopg2 driver missing
        psycopg2.OperationalError etc.: connection failure
    """
    driver = (conn_args.get("driver") or "postgresql").lower()
    if not _is_pg_driver(driver):
        raise ValueError(f"不支持的数据库驱动 '{driver}'，当前仅支持 PostgreSQL")

    host = conn_args.get("host")
    user = conn_args.get("user")
    password = conn_args.get("password")
    database = conn_args.get("database")
    missing = [
        k for k, v in {
            "host": host,
            "user": user,
            "password": password,
            "database": database,
        }.items() if not v
    ]
    if missing:
        raise ValueError(f"缺少必需的数据库连接参数: {', '.join(missing)}")

    port = int(conn_args.get("port") or 5432)
    connect_timeout = int(conn_args.get("connect_timeout") or 10)

    psycopg2 = _load_pg_driver()

    return psycopg2.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        dbname=database,
        connect_timeout=connect_timeout,
        application_name="memorybear-database-tool",
    )


def _safe_close_conn(conn: Any) -> None:
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def _normalize_json_value(v: Any) -> Any:
    """Convert a database-returned Python value to a JSON-friendly type."""
    if v is None:
        return None
    if isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, Decimal):
        # Preserve precision; integral Decimals become int to avoid serialization issues
        try:
            if v == v.to_integral_value():
                return int(v)
        except Exception:
            pass
        return float(v)
    if isinstance(v, (datetime, date, time_cls)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        try:
            return v.decode("utf-8", errors="replace")
        except Exception:
            return v.hex()
    if isinstance(v, (list, tuple)):
        return [_normalize_json_value(x) for x in v]
    if isinstance(v, dict):
        return {k: _normalize_json_value(x) for k, x in v.items()}
    return str(v)


# ---------------------------------------------------------------------------
# Main tool class
# ---------------------------------------------------------------------------
class DatabaseTool(BuiltinTool):
    """Database tool: connect to PostgreSQL and execute arbitrary SQL

    No preconfigured static connection info; the tool management page has only an
    enable switch. The database server connection info (host/port/user/password
    etc.) is, like ``database``/``sql``, an invocation parameter passed in by the
    Agent/workflow on each call.
    """

    # ---- Metadata ----
    @property
    def name(self) -> str:
        return "database_tool"

    @property
    def description(self) -> str:
        return (
            "数据库工具：连接 PostgreSQL 数据库服务器，执行任意 SQL 语句"
            "（包括 SELECT/INSERT/UPDATE/DELETE/DDL 等，不做限制，请谨慎使用）。"
            "调用时需指定连接信息（host/port/user/password）、要操作的数据库名"
            "（database）和 SQL 语句。"
        )

    # ---- Config: no static parameters to preconfigure, only the enable switch ----
    def get_required_config_parameters(self) -> List[str]:
        return []

    # ---- Parameter definitions (invocation arguments filled in by Agent/workflow per call) ----
    @property
    def parameters(self) -> List[ToolParameter]:
        return [
            ToolParameter(
                name="driver",
                type=ParameterType.STRING,
                description="数据库驱动类型，当前仅支持 postgresql",
                required=False,
                default="postgresql",
                enum=["postgresql"],
            ),
            ToolParameter(
                name="host",
                type=ParameterType.STRING,
                description="数据库主机地址，如 127.0.0.1 或 db.example.com",
                required=True,
            ),
            ToolParameter(
                name="port",
                type=ParameterType.INTEGER,
                description="数据库端口（PostgreSQL 默认 5432）",
                required=True,
                default=5432,
            ),
            ToolParameter(
                name="user",
                type=ParameterType.STRING,
                description="数据库用户名",
                required=True,
            ),
            ToolParameter(
                name="password",
                type=ParameterType.STRING,
                description="数据库密码",
                required=True,
            ),
            ToolParameter(
                name="connect_timeout",
                type=ParameterType.INTEGER,
                description="连接超时秒数",
                required=False,
                default=10,
            ),
            ToolParameter(
                name="database",
                type=ParameterType.STRING,
                description="要操作的数据库名 / schema 名",
                required=True,
            ),
            # ---- SQL to run: no keyword/statement restrictions ----
            ToolParameter(
                name="sql",
                type=ParameterType.STRING,
                description=(
                    "要执行的完整 SQL 语句，支持任意 SQL（SELECT/INSERT/UPDATE/"
                    "DELETE/DDL 等），不做关键字过滤。"
                ),
                required=True,
            ),
        ]

    # ---- Driver detection ----
    @staticmethod
    def _is_pg(driver: Optional[str]) -> bool:
        return _is_pg_driver(driver)

    @staticmethod
    def _build_conn_args(kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Extract connection info from this call's parameters; no static config is read."""
        return {
            "driver": kwargs.get("driver") or "postgresql",
            "host": kwargs.get("host") or "",
            "port": int(kwargs.get("port") or 5432),
            "user": kwargs.get("user") or "",
            "password": kwargs.get("password") or "",
            "database": kwargs.get("database") or _DEFAULT_TEST_DATABASE,
            "connect_timeout": int(kwargs.get("connect_timeout") or 10),
        }

    # ------------------------------------------------------------------
    # Connection management: a temporary connection per call, released
    # immediately after use, with no cross-call caching/reuse
    # ------------------------------------------------------------------
    async def _open_connection(self, conn_args: Dict[str, Any]):
        return await asyncio.to_thread(_open_pg_connection, conn_args)

    @staticmethod
    def _safe_close(conn: Any) -> None:
        _safe_close_conn(conn)

    # Note: this tool has no separate "connection test" — the tool management
    # page's "test database connection" panel calls the real execute() (via
    # /tools/execution/execute) with the full driver/host/port/user/password/
    # database/sql parameters, testing the exact result an Agent/workflow call
    # would get instead of maintaining a separate simplified test path.

    # ---- Execution entry ----
    async def execute(self, **kwargs) -> ToolResult:
        start_time = time.time()
        conn = None
        try:
            conn_args = self._build_conn_args(kwargs)
            conn = await self._open_connection(conn_args)
            data = await self._op_execute_sql(conn, kwargs, conn_args)

            return ToolResult.success_result(
                data=data,
                execution_time=time.time() - start_time,
            )
        except Exception as e:
            logger.exception("数据库工具执行失败: %s", e)
            # Try to roll back on error so the connection does not stay in a failed transaction
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return ToolResult.error_result(
                error=str(e),
                error_code="DATABASE_TOOL_ERROR",
                execution_time=time.time() - start_time,
            )
        finally:
            # Whether it succeeded or failed, the connection opened for this
            # call is never kept
            self._safe_close(conn)

    # ------------------ Sole operation: execute arbitrary SQL ------------------
    async def _op_execute_sql(
        self, conn, kwargs: Dict[str, Any], conn_args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute an arbitrary SQL statement (no keyword/operation restrictions,
        only the most basic safeguards)."""
        sql = (kwargs.get("sql") or "").strip()
        if not sql:
            raise ValueError("必须提供 sql 参数")

        # Audit log: target database, caller, SQL text (excluding the password), for tracing
        logger.warning(
            "database_tool execute_sql | host=%s port=%s database=%s user=%s | sql=%s",
            conn_args.get("host"),
            conn_args.get("port"),
            conn_args.get("database"),
            conn_args.get("user"),
            sql,
        )

        return await asyncio.to_thread(self._sync_execute_sql, conn, sql, conn_args)

    def _sync_execute_sql(
        self,
        conn,
        sql: str,
        conn_args: Dict[str, Any],
    ) -> Dict[str, Any]:
        import psycopg2.extras  # type: ignore

        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)

            if cur.description:
                # Has a result set (e.g. SELECT / RETURNING): return the row data only
                columns = [d[0] for d in cur.description]
                rows = cur.fetchall()
                result: Dict[str, Any] = {
                    "row_count": len(rows),
                    "rows": [
                        {c: _normalize_json_value(r.get(c)) for c in columns}
                        for r in rows
                    ],
                }
            else:
                # No result set (INSERT/UPDATE/DELETE/DDL writes): return the affected row count only
                result = {
                    "affected_rows": cur.rowcount if cur.rowcount is not None else 0,
                }

            conn.commit()
            return result
