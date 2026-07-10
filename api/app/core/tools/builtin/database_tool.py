"""数据库查询内置工具 - PostgreSQL 连接与任意 SQL 执行

工具定位说明
------------
本工具**不持有任何预配置的静态连接信息**。工具管理页面对这个工具只有一个
启用开关（``is_enabled``），没有任何需要管理员预填的参数（跟百度搜索、
OpenClaw 等"需要先配置密钥才能用"的内置工具不同）。

- 调用参数（Agent/工作流每次调用时提供）：数据库服务器连接信息
  ``host`` / ``port`` / ``user`` / ``password`` / ``driver`` /
  ``connect_timeout`` + 要操作的 ``database`` + 要执行的 ``sql``。也就是说
  连哪台服务器、连哪个库，完全由调用方每次决定，不受管理员预先配置的范围
  限制。
- 工具实例不持有、不缓存跨调用的数据库连接：每次 ``execute()`` 调用都会用
  当次传入的连接信息临时建立一次连接，用完立即关闭。
- 当前仅支持 PostgreSQL（依赖 psycopg2-binary）。
- 工具管理页面"测试数据库连接"面板直接调用 ``POST /tools/execution/execute``
  （即本类的 ``execute()``），传入完整的 driver/host/port/user/password/
  database/sql，模拟一次真实调用，不做简化，不落库、不保存这些参数。

⚠️ 风险提示
------------
本工具允许执行**任意 SQL 语句**（包括 SELECT / INSERT / UPDATE / DELETE /
DROP / TRUNCATE / ALTER 等写操作与 DDL），不做关键字黑名单、不限制语句条数、
不限制表/schema。仅保留最基础的防护：
  - 执行异常时自动 ``ROLLBACK``，避免连接残留在失败事务中；
  - 每次调用执行完毕后连接立即关闭；
  - 执行时会记录审计日志（host/database/user/SQL 文本，不含密码），便于事后追溯。
由于连接信息本身也是调用参数（而非管理员预先审核过的固定配置），一次误操作
（或被提示词注入诱导的操作）可能导致连接到任意数据库并造成不可逆的数据丢失。
请只在能接受该风险的场景下启用此工具，并在编排 Agent/工作流时谨慎处理数据库
密码的传递与留存。
"""
from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, time as time_cls
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.core.logging_config import get_business_logger
from app.core.tools.base import ParameterType, ToolParameter, ToolResult
from app.core.tools.builtin.base import BuiltinTool

logger = get_business_logger()

# 连通性测试 / 未指定 database 时使用的默认数据库名（PostgreSQL 自带）
_DEFAULT_TEST_DATABASE = "postgres"

# ---------------------------------------------------------------------------
# PostgreSQL 连接辅助（原 db_connection.py，内联至此以避免跨模块依赖）
# ---------------------------------------------------------------------------
# 当前仅支持 postgresql（预留 driver 字段以便未来扩展其他数据库）
_SUPPORTED_DRIVERS = ("postgresql",)

_PG_DRIVER = None  # psycopg2 模块（延迟加载，避免导入阶段硬依赖）


def _load_pg_driver():
    """延迟加载 psycopg2，避免模块导入阶段硬依赖该驱动"""
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
    """根据连接参数同步建立一个 PostgreSQL 连接（阻塞调用，请在线程池中执行）

    Args:
        conn_args: 包含 driver/host/port/user/password/database/connect_timeout 的字典

    Returns:
        psycopg2 connection 对象

    Raises:
        ValueError: 驱动不支持或缺少必需连接参数
        RuntimeError: 缺少 psycopg2 驱动
        psycopg2.OperationalError 等: 连接失败
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
    """把数据库返回的 Python 类型转为 JSON 友好类型"""
    if v is None:
        return None
    if isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, Decimal):
        # 保留精度；如果是整数值的 Decimal，转 int 避免序列化问题
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
# 主工具类
# ---------------------------------------------------------------------------
class DatabaseTool(BuiltinTool):
    """数据库工具：连接 PostgreSQL 并执行任意 SQL

    没有任何预配置的静态连接信息，工具管理页面只有一个启用开关。数据库服务器
    连接信息（host/port/user/password 等）跟 ``database``/``sql`` 一样，都是
    Agent/工作流每次调用时传入的调用参数。
    """

    # ---- 元信息 ----
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

    # ---- 配置项：本工具没有任何需要预先配置的静态参数，只有启用开关 ----
    def get_required_config_parameters(self) -> List[str]:
        return []

    # ---- 参数定义（均为调用参数，由 Agent/工作流在每次调用时填入）----
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
            # ---- 要执行的 SQL：不做关键字/语句限制 ----
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

    # ---- 驱动识别 ----
    @staticmethod
    def _is_pg(driver: Optional[str]) -> bool:
        return _is_pg_driver(driver)

    @staticmethod
    def _build_conn_args(kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """从本次调用参数中提取连接信息，不再读取任何静态配置"""
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
    # 连接管理：每次调用临时建立连接，用完立即释放，不做跨调用缓存/复用
    # ------------------------------------------------------------------
    async def _open_connection(self, conn_args: Dict[str, Any]):
        return await asyncio.to_thread(_open_pg_connection, conn_args)

    @staticmethod
    def _safe_close(conn: Any) -> None:
        _safe_close_conn(conn)

    # 注：本工具没有单独的"连接测试"，工具管理页面的"测试数据库连接"面板
    # 直接调用真正的 execute()（走 /tools/execution/execute），带上完整的
    # driver/host/port/user/password/database/sql 参数，测的就是这个工具
    # 被 Agent/工作流调用时会发生的真实结果，不再另外维护一套简化测试逻辑。

    # ---- 执行入口 ----
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
            # 出错时尝试回滚，避免连接残留在失败事务中
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
            # 无论成功失败，本次调用建立的连接都不做保留
            self._safe_close(conn)

    # ------------------ 唯一操作：执行任意 SQL ------------------
    async def _op_execute_sql(
        self, conn, kwargs: Dict[str, Any], conn_args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """执行任意 SQL 语句（不限制关键字/操作类型，仅保留最基础的防护）"""
        sql = (kwargs.get("sql") or "").strip()
        if not sql:
            raise ValueError("必须提供 sql 参数")

        # 审计日志：记录目标库、执行者、SQL 文本（不含密码），便于事后追溯
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
                # 有结果集（例如 SELECT / RETURNING）：只返回行数据本身
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
                # 无结果集（INSERT/UPDATE/DELETE/DDL 等写操作）：只返回影响行数
                result = {
                    "affected_rows": cur.rowcount if cur.rowcount is not None else 0,
                }

            conn.commit()
            return result
