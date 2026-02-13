"""
M13-2 请求级指标采集中间件

采集每个 HTTP 请求的方法、路径、状态码、耗时。
"""

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.observability.metrics import REQUEST_DURATION, REQUEST_TOTAL


class MetricsMiddleware(BaseHTTPMiddleware):
    """HTTP 请求指标采集"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # 跳过 /metrics 自身和健康检查
        if request.url.path in ("/metrics", "/health", "/health/ready"):
            return await call_next(request)

        start = time.time()
        response = await call_next(request)
        duration_ms = (time.time() - start) * 1000

        endpoint = request.url.path
        method = request.method
        status = str(response.status_code)

        REQUEST_TOTAL.labels(method=method, endpoint=endpoint, status_code=status).inc()
        REQUEST_DURATION.labels(method=method, endpoint=endpoint).observe(duration_ms)

        return response
