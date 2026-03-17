"""
可观测性管理 API：Langfuse 配置管理与用量统计
"""

import json
from typing import Optional

import jwt
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import fail, ok
from app.cache.redis_client import RedisKeys, get_redis
from app.db.engine import get_db
from app.observability.langfuse_client import configure_langfuse
from app.config import get_settings
from app.security.auth import AuthenticatedUser, get_current_user, is_super_admin

from app.services.langfuse_manager import LangfuseManager
from app.services.observability import ObservabilityService

settings = get_settings()

router = APIRouter(prefix="/api/v1/observability", tags=["observability"])


def get_langfuse_manager() -> LangfuseManager:
    return LangfuseManager()


async def get_observability_service(
    redis: aioredis.Redis = Depends(get_redis),
    db: AsyncSession = Depends(get_db),
) -> ObservabilityService:
    manager = LangfuseManager()
    config = await manager.get_decrypted_config(db)
    return ObservabilityService(config=config, redis=redis)


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class SaveConfigRequest(BaseModel):
    enabled: bool = False
    langfuse_host: Optional[str] = None
    langfuse_public_key: Optional[str] = None
    langfuse_secret_key: Optional[str] = None
    sample_rate: Optional[float] = 1.0
    flush_interval: Optional[int] = 5
    pii_patterns: Optional[str] = ""


class ValidateConnectionRequest(BaseModel):
    langfuse_host: str
    langfuse_public_key: str
    langfuse_secret_key: str


# ---------------------------------------------------------------------------
# Config Endpoints
# ---------------------------------------------------------------------------


@router.get("/config")
async def get_config(
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取当前 Langfuse 配置"""
    if not is_super_admin(user):
        return fail(code=40300, message="权限不足", status_code=403)

    manager = get_langfuse_manager()
    config = await manager.get_config(db)
    return ok(data=config)


@router.put("/config")
async def save_config(
    body: SaveConfigRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """保存 Langfuse 配置"""
    if not is_super_admin(user):
        return fail(code=40300, message="权限不足", status_code=403)

    manager = get_langfuse_manager()
    await manager.save_config(body.model_dump(), db)

    # 重载 Langfuse 全局客户端
    new_config = await manager.get_decrypted_config(db)
    configure_langfuse(new_config)

    # 同步注册/注销 litellm 回调
    import litellm
    if new_config:
        litellm.success_callback = ["langfuse_otel"]
        litellm.failure_callback = ["langfuse_otel"]
    else:
        litellm.success_callback = []
        litellm.failure_callback = []

    # 清除旧的健康状态缓存
    await redis.delete(RedisKeys.langfuse_health())

    # 返回最新状态
    svc = ObservabilityService(config=new_config, redis=redis)
    status = await svc.get_status()
    return ok(data={"saved": True, "status": status})


@router.post("/config/validate")
async def validate_connection(
    body: ValidateConnectionRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """验证 Langfuse 连接（填入 host/keys 后点击验证）"""
    if not is_super_admin(user):
        return fail(code=40300, message="权限不足", status_code=403)

    manager = get_langfuse_manager()
    result = await manager.validate_connection(
        host=body.langfuse_host,
        public_key=body.langfuse_public_key,
        secret_key=body.langfuse_secret_key,
    )
    return ok(data=result)


# ---------------------------------------------------------------------------
# Status & Usage Endpoints
# ---------------------------------------------------------------------------


@router.get("/status")
async def get_langfuse_status(
    user: AuthenticatedUser = Depends(get_current_user),
    svc: ObservabilityService = Depends(get_observability_service),
):
    """获取 Langfuse 健康状态"""
    if not is_super_admin(user):
        return fail(code=40300, message="权限不足", status_code=403)
    result = await svc.get_status()
    return ok(data=result)


@router.get("/console-url")
async def get_console_url(
    user: AuthenticatedUser = Depends(get_current_user),
    svc: ObservabilityService = Depends(get_observability_service),
):
    """获取 Langfuse 控制台 URL（仅管理员）"""
    if not is_super_admin(user):
        return fail(code=40300, message="权限不足", status_code=403)

    result = await svc.get_console_url()
    return ok(data=result)


@router.get("/console-redirect")
async def console_redirect(
    token: str = Query(..., description="JWT access token"),
    redis: aioredis.Redis = Depends(get_redis),
    svc: ObservabilityService = Depends(get_observability_service),
):
    """代理登录 Langfuse 并重定向到控制台（通过 query token 鉴权）"""
    # 手动验证 token（window.open 无法携带 Authorization 头）
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.InvalidTokenError:
        return RedirectResponse(url="/")

    jti = payload.get("jti")
    if jti and await redis.exists(RedisKeys.token_blacklist(jti)):
        return RedirectResponse(url="/")

    user = AuthenticatedUser(
        id=payload["sub"],
        usernumb=payload.get("usernumb", ""),
        role=payload.get("role", "viewer"),
        permissions=payload.get("permissions", []),
    )

    if not is_super_admin(user):
        return RedirectResponse(url="/")

    settings_obj = get_settings()
    result = await svc.proxy_login(
        admin_email=settings_obj.LANGFUSE_ADMIN_EMAIL,
        admin_password=settings_obj.LANGFUSE_ADMIN_PASSWORD,
    )

    if result.get("error") or not result.get("cookies"):
        return RedirectResponse(url=result.get("url") or "/")

    response = RedirectResponse(url=result["url"])
    for cookie in result["cookies"]:
        response.headers.append("set-cookie", cookie)
    return response


@router.get("/usage/summary")
async def get_usage_summary(
    start: str = Query(..., description="开始日期 YYYY-MM-DD"),
    end: str = Query(..., description="结束日期 YYYY-MM-DD"),
    user: AuthenticatedUser = Depends(get_current_user),
    svc: ObservabilityService = Depends(get_observability_service),
):
    """获取 token 用量汇总"""
    if not is_super_admin(user):
        return fail(code=40300, message="权限不足", status_code=403)
    result = await svc.get_usage_summary(start, end)
    return ok(data=result)


@router.get("/usage/daily")
async def get_usage_daily(
    start: str = Query(..., description="开始日期 YYYY-MM-DD"),
    end: str = Query(..., description="结束日期 YYYY-MM-DD"),
    user: AuthenticatedUser = Depends(get_current_user),
    svc: ObservabilityService = Depends(get_observability_service),
):
    """获取每日 token 用量"""
    if not is_super_admin(user):
        return fail(code=40300, message="权限不足", status_code=403)
    result = await svc.get_usage_daily(start, end)
    return ok(data=result)


@router.get("/usage/by-user")
async def get_usage_by_user(
    start: str = Query(..., description="开始日期 YYYY-MM-DD"),
    end: str = Query(..., description="结束日期 YYYY-MM-DD"),
    user: AuthenticatedUser = Depends(get_current_user),
    svc: ObservabilityService = Depends(get_observability_service),
):
    """获取按用户汇总的 token 用量（仅管理员）"""
    if not is_super_admin(user):
        return fail(code=40300, message="权限不足", status_code=403)

    result = await svc.get_usage_by_user(start, end)
    return ok(data=result)


@router.post("/usage/refresh")
async def refresh_usage(
    user: AuthenticatedUser = Depends(get_current_user),
    svc: ObservabilityService = Depends(get_observability_service),
):
    """清除用量缓存并重新拉取（仅管理员）"""
    if not is_super_admin(user):
        return fail(code=40300, message="权限不足", status_code=403)

    result = await svc.refresh_usage()
    return ok(data=result)


# ---------------------------------------------------------------------------
# Trace Export Endpoint
# ---------------------------------------------------------------------------


@router.get("/traces/export")
async def export_traces(
    startDate: str = Query(..., description="开始日期 YYYY-MM-DD"),
    endDate: str = Query(..., description="结束日期 YYYY-MM-DD"),
    format: str = Query(..., description="导出格式: json 或 csv"),
    userId: Optional[str] = Query(None, description="用户ID过滤，admin可传'all'"),
    user: AuthenticatedUser = Depends(get_current_user),
    svc: ObservabilityService = Depends(get_observability_service),
):
    """导出 Trace 数据（JSON/CSV 文件下载）"""
    if not is_super_admin(user):
        return fail(code=40300, message="权限不足", status_code=403)
    effective_user_id = None if (userId is None or userId == "all") else userId

    result = await svc.export_traces(
        start=startDate,
        end=endDate,
        fmt=format,
        user_id=effective_user_id,
    )

    if isinstance(result, dict) and result.get("error"):
        return fail(code=40000, message=result["message"], status_code=400)

    filename_base = f"traces_{startDate}_{endDate}"

    if format == "csv":
        return StreamingResponse(
            iter([result]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{filename_base}.csv"',
            },
        )

    content = json.dumps(result, ensure_ascii=False, indent=2)
    return StreamingResponse(
        iter([content]),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename_base}.json"',
        },
    )
