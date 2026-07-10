"""数据库连接辅助模块 - 复用的 PostgreSQL 连接建立逻辑

被 ``DatabaseTool``（Agent 可调用的数据库工具）和工具管理页面的
"数据库连通性测试"接口共同复用，避免重复实现 psycopg2 连接建立代码。

本模块只负责"建立/测试连接"，不涉及任何 SQL 执行逻辑。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# 当前仅支持 postgresql（预留 driver 字段以便未来扩展其他数据库）
SUPPORTED_DRIVERS = ("postgresql",)

_PG_DRIVER = None  # psycopg2 模块（延迟加载，避免导入阶段硬依赖）


def load_pg_driver():
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


def is_pg_driver(driver: Optional[str]) -> bool:
    return (driver or "postgresql").lower() in SUPPORTED_DRIVERS


def open_pg_connection(conn_args: Dict[str, Any]):
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
    if not is_pg_driver(driver):
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

    psycopg2 = load_pg_driver()

    return psycopg2.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        dbname=database,
        connect_timeout=connect_timeout,
        application_name="memorybear-database-tool",
    )


def safe_close(conn: Any) -> None:
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
