"""跨服务传输对象（DTO）定义。

这些是服务间传递的数据结构（内部 token claims、Redis 快照、审计事件），
不是 ORM model，故命名为 schema.py（对齐 core 的 schemas/ 惯例，内容用
轻量 dataclass 而非 Pydantic——无外部输入校验需求，构造点均为内部可信来源）。
"""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class UserContext:
    """一次请求的完整身份上下文：网关中间件解析后签发进内部 token claims。"""
    user_id: str
    tenant_id: str
    workspace_id: str
    roles: tuple[str, ...] = ()


@dataclass
class ApiKeyContext:
    """API Key 请求的上下文：服务对服务调用（老单体 service_router 语义）。"""
    api_key_id: str
    workspace_id: str
    tenant_id: str
    scopes: list[str] = field(default_factory=list)
    # 限流参数随快照携带（默认值保旧快照兼容：字段缺失不炸）
    rate_limit: int | None = None
    daily_request_limit: int | None = None
    rate_limit_disabled: bool = False


@dataclass
class UserSnapshot:
    """用户身份快照（Redis 值，JSON 序列化后存 user:{id}）。

    快照是网关解析多租户上下文的权威来源（用户 JWT 只携带 sub，不含
    tenant_id/workspace_id）；token_invalidated_before 用于快照失效控制
    （该时间前签发的用户 JWT 一律拒绝）。
    """
    user_id: str
    tenant_id: str
    workspace_id: str
    roles: tuple[str, ...]
    disabled: bool
    tenant_active: bool
    token_invalidated_before: datetime | None = None


@dataclass
class AuditEvent:
    """审计事件：网关/身份服务入队 Redis（audit:stream），消费者批量落库 audit_logs。"""
    event_type: str
    actor_id: str | None
    tenant_id: str | None
    target: str | None
    result: str
    detail: dict | None = None
    ts: datetime | None = None
    event_id: str | None = None  # 幂等键：消费者 ON CONFLICT 去重（未提供时入队时生成）
