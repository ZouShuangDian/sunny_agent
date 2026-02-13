"""
请求日志中间件：记录每个 HTTP 请求的开始/结束 + trace_id 注入
"""

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.observability.context import trace_id_var, user_id_var

log = structlog.get_logger()


class RequestLoggerMiddleware(BaseHTTPMiddleware):
    """HTTP 请求日志 + trace_id 上下文注入"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # 1. 注入 trace_id 上下文
        trace_id = request.headers.get("X-Trace-ID", str(uuid.uuid4()))
        trace_id_var.set(trace_id)

        # 绑定到 structlog 上下文，后续所有日志自动带 trace_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(trace_id=trace_id)

        start = time.monotonic()

        log.info(
            "请求开始",
            method=request.method,
            path=request.url.path,
            client_ip=request.client.host if request.client else "unknown",
        )

        response = await call_next(request)

        duration_ms = int((time.monotonic() - start) * 1000)

        log.info(
            "请求结束",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )

        # 2. 响应头注入 trace_id 和耗时
        response.headers["X-Trace-ID"] = trace_id
        response.headers["X-Duration-Ms"] = str(duration_ms)

        return response
