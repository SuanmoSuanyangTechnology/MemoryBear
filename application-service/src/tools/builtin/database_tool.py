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

from typing import List

from ..base import ParameterType, ToolParameter
from .base import BuiltinTool


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
