from contextvars import ContextVar, Token

_trace_id_var: ContextVar[str] = ContextVar("request_trace_id", default="")


def get_trace_id() -> str:
    """获取当前请求上下文的 trace_id，无请求上下文时返回空字符串。"""
    return _trace_id_var.get()


def set_trace_id(trace_id: str) -> Token:
    """设置当前请求上下文的 trace_id，返回用于 reset 的 token。"""
    return _trace_id_var.set(trace_id)


def reset_trace_id(token: Token) -> None:
    """还原 trace_id 到进入上下文前的值。"""
    _trace_id_var.reset(token)
