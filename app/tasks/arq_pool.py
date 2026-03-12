"""
FastAPI 侧 arq 入队连接池

Agent 进程不是 arq Worker，没有 ctx["redis"]，
需要独立的 arq 连接池来入队异步任务。
"""

import asyncio

from arq.connections import ArqRedis, RedisSettings, create_pool

from app.config import get_settings

_pool: ArqRedis | None = None
_lock = asyncio.Lock()


async def get_arq_pool() -> ArqRedis:
    """惰性创建 arq 连接池（双检锁防止并发竞态）"""
    global _pool
    if _pool is not None:
        return _pool
    async with _lock:
        if _pool is not None:
            return _pool
        settings = get_settings()
        _pool = await create_pool(
            RedisSettings.from_dsn(settings.REDIS_URL),
            default_queue_name=settings.ARQ_QUEUE_NAME,
        )
        return _pool


async def close_arq_pool() -> None:
    """关闭连接池（lifespan shutdown 时调用）"""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
