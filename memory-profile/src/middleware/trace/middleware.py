"""请求级 trace_id 与**服务内耗时**统计。

耗时口径：从请求进入本服务（最外层中间件）到响应生成完毕，**不含**客户端网络与
网关转发。要端到端（含网关与网络）需在网关/客户端侧另测——网关当前只有
counter/gauge，没有延迟直方图。

为什么必须注册在**最外层**（最后 ``add_middleware``）：

1. **耗时才完整**：鉴权中间件在 direct 模式下要走一次到 identity 的 HTTP 往返
   （API key 校验），enterprise 模式下还有 ACL 判定；若计时中间件在它内层，这段
   时间就被漏掉了。
2. **trace_id 才不断链**：ContextVar 只向下传播，内层设置的 trace_id 外层看不到
   ——注册在内层时，鉴权/语言中间件的日志（如 gateway auth denied）trace_id 为空，
   无法与同一请求的其他日志串起来。

日志形如（``LOG_FORMAT`` 里已含 trace_id，据此可 grep 出同一请求的全部日志）::

    2026-09-11 06:20:31 - [3f2a…] - src.middleware.trace.middleware - INFO - GET /api/memory/analytics/graph_data -> 200 103.42 ms
"""

import logging
import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from src.middleware.trace.utils import reset_trace_id, set_trace_id

# 这里**不能**用 src.infrastructure.logger.config.get_logger：logger/config.py 需要
# 从 src.middleware.trace.utils 取 trace_id 注入日志，而 src/middleware/trace/__init__.py
# 又导入本模块 —— 于是本模块反向导入 logger 就成环（ImportError: partially initialized）。
# 直接用 stdlib logger 等价：日志格式（含 trace_id）由 logger/config.py 的 handler +
# TraceIdFilter 统一施加，与本模块怎么取 logger 无关。
logger = logging.getLogger(__name__)

TRACE_ID_HEADER = "X-Trace-Id"
RESPONSE_TIME_HEADER = "X-Response-Time-Ms"


class TraceIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = uuid.uuid4().hex
        started = time.perf_counter()

        token = set_trace_id(trace_id)
        request.state.trace_id = trace_id

        status_code: int | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
        finally:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            reset_trace_id(token)

            # 异常路径：call_next 抛出时 response 未绑定，此时只记耗时（状态记为 ERR），
            # 异常继续向上抛给全局错误处理，不在此吞掉
            if status_code is not None:
                response.headers[TRACE_ID_HEADER] = trace_id
                response.headers[RESPONSE_TIME_HEADER] = f"{elapsed_ms:.2f}"

            # trace_id 必须**显式**通过 extra 传入：日志的 trace_id 由 logger/config.py
            # 的 TraceIdFilter 在"生成日志记录那一刻"读 ContextVar 填充，而此处已先
            # reset_trace_id()，不显式传就会成空串（TraceIdFilter 对已有该属性的 record
            # 不再覆盖，故 extra 生效）。
            logger.info(
                "%s %s -> %s %s ms",
                request.method, request.url.path,
                status_code if status_code is not None else "ERR",
                elapsed_ms,
                extra={"trace_id": trace_id},
            )

        return response