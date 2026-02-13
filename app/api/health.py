"""
健康检查接口：探活 + 依赖服务状态
"""

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis_client import get_redis
from app.db.engine import get_db

router = APIRouter(tags=["健康检查"])
log = structlog.get_logger()


@router.get("/health")
async def health_check(
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """健康检查：校验 PG + Redis 连接"""
    status = {"status": "ok", "postgres": "ok", "redis": "ok"}

    # 检查 PG
    try:
        await db.execute(text("SELECT 1"))
    except Exception as e:
        status["postgres"] = f"error: {e}"
        status["status"] = "degraded"
        log.error("PG 健康检查失败", error=str(e))

    # 检查 Redis
    try:
        await redis.ping()
    except Exception as e:
        status["redis"] = f"error: {e}"
        status["status"] = "degraded"
        log.error("Redis 健康检查失败", error=str(e))

    return status
