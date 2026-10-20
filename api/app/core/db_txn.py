"""共享 session 的连接提前归还工具。"""
import inspect
import logging

logger = logging.getLogger(__name__)


async def end_read_transaction(db, where: str = "") -> None:
    """结束 db 上已开启的事务，把底层连接还给连接池。

    用在"读完库、接下来要长时间等 LLM / 子 Agent"之前。项目的 AsyncSessionLocal 是
    expire_on_commit=False，commit 后已加载的 ORM 对象仍可正常读属性（与 close() 不同，
    不会脱离 session），后续再用 db 时会自动开新事务。

    失败只记 warning，不影响主流程。db 为 None / 无事务时什么也不做。
    """
    if db is None:
        return
    try:
        if not db.in_transaction():
            return
        result = db.commit()
        if inspect.isawaitable(result):
            await result
    except Exception as exc:  # noqa: BLE001 - 旁路优化，不能影响业务
        logger.warning("end_read_transaction failed where=%s error=%s", where, exc)
