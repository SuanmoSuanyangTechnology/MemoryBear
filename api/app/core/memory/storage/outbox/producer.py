from uuid import UUID

from .exceptions import OutboxEnqueueError, safe_error
from .repository import OutboxRepository, create_repository
from .types import OutboxEventInput


async def enqueue_events(events: list[OutboxEventInput], *,
                         repository: OutboxRepository | None = None) -> list[UUID]:
    """仅在确认 Neo4j 提交后调用；独立 PG 提交完成后再返回。

    入队失败后可再次入队，此时复用相同的输入对象/UUID。
    本函数既不写 Neo4j，也不调度 Celery 任务。
    """
    if not events:
        return []
    if any(not isinstance(event, OutboxEventInput) for event in events):
        raise TypeError("events must contain validated OutboxEventInput objects")
    try:
        repo = repository or create_repository()
        return await repo.enqueue_many(events)
    except Exception as exc:
        # 数据库驱动异常可能包含 SQL 参数，不要向外传播。
        raise OutboxEnqueueError([event.id for event in events], safe_error(exc)) from None
