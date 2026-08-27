"""安全诊断信息：绝不持久化异常正文、SQL 查询或节点内容。"""

from uuid import UUID


class OutboxEnqueueError(RuntimeError):
    primary_committed = True

    def __init__(self, event_ids: list[UUID], reason: str):
        self.event_ids = tuple(event_ids)
        self.reason = reason
        super().__init__(f"Primary committed; Outbox enqueue failed ({reason})")


class OutboxConflictError(RuntimeError):
    """已存在的事件 ID 具有不同的业务字段。"""


class ClaimLostError(RuntimeError):
    pass


def safe_error(exc: Exception, max_length: int = 4096) -> str:
    # 即使是 SDK 的异常消息也可能包含凭据或完整文档。
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        name += f" (HTTP {status})"
    return name[:max_length]
