"""
统一 API 响应格式

所有 JSON 端点统一返回：
{
    "success": true/false,
    "code": 0,
    "message": "ok",
    "data": { ... }
}

豁免端点（非 JSON）：
- POST /api/chat/stream  — SSE StreamingResponse
- GET  /api/files/download — FileResponse 二进制流
- GET  /metrics — Prometheus ASGI 挂载

业务错误码规范：code = http_status_code * 100，后两位预留子码扩展。
- 0     = 成功
- 40000 = 请求参数错误
- 40100 = 未认证
- 40300 = 无权限
- 40400 = 资源不存在
- 42200 = 请求校验失败
- 50000 = 服务器内部错误
- 50300 = 服务降级
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from fastapi.responses import JSONResponse
from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一信封响应模型"""

    success: bool = True
    code: int = 0
    message: str = "ok"
    data: T | None = None


def ok(
    data: Any = None,
    message: str = "ok",
    status_code: int = 200,
) -> JSONResponse:
    """
    成功响应工厂函数。

    参数：
    - data: 业务数据（Pydantic 模型会自动 model_dump）
    - message: 人类可读消息
    - status_code: HTTP 状态码（默认 200，POST 创建资源可传 201）
    """
    if isinstance(data, BaseModel):
        data = data.model_dump()
    body = {"success": True, "code": 0, "message": message, "data": data}
    return JSONResponse(content=body, status_code=status_code)


def fail(
    code: int,
    message: str,
    status_code: int = 500,
    data: Any = None,
) -> JSONResponse:
    """
    失败响应工厂函数。

    参数：
    - code: 业务错误码（非 0）
    - message: 人类可读错误描述
    - status_code: HTTP 状态码
    - data: 额外错误详情（可选）
    """
    body = {"success": False, "code": code, "message": message, "data": data}
    return JSONResponse(content=body, status_code=status_code)
