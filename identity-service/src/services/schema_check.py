"""启动期只读表契约校验（受控复制保障）。

identity 的 5 张只读模型是 core 表的本地副本（表结构唯一真源在 core，现阶段
core 管理、未来随拆分迁入微服务）。core 改列名/删列时，副本在运行时才会报错；
本模块在启动时对 information_schema 做列存在性检查，缺列即 fail-fast，把
「运行时神秘报错」提前为「启动即明确失败」。
"""
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


async def verify_readonly_schema(session) -> list[str]:
    """对 ReadOnlyBase 全部只读表做列存在性检查，返回缺失列清单（空 = 通过）。"""
    from src.models.base import ReadOnlyBase

    missing: list[str] = []
    for table in ReadOnlyBase.metadata.sorted_tables:
        result = await session.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
            {"t": table.name},
        )
        actual = {row[0] for row in result}
        for col in table.columns.keys():
            if col not in actual:
                missing.append(f"{table.name}.{col}")
    return missing


async def assert_readonly_schema(session) -> None:
    """启动校验入口：缺列直接抛 RuntimeError（fail-fast）。"""
    missing = await verify_readonly_schema(session)
    if missing:
        raise RuntimeError(
            "只读表契约校验失败：core 表缺少本地模型声明的列，请同步 "
            "identity/models/*（列清单：" + ", ".join(sorted(missing)) + "）"
        )
