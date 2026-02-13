"""
Redis 客户端：连接池 + Key 统一管理 + FastAPI 依赖注入
"""

import redis.asyncio as aioredis

from app.config import get_settings


class RedisKeys:
    """
    Redis Key 统一管理，避免散弹式硬编码
    命名规范：{业务域}:{资源类型}:{标识}
    """

    # ── JWT 黑名单 ──
    @staticmethod
    def token_blacklist(jti: str) -> str:
        """JWT 注销黑名单 (TTL = token 剩余有效期)"""
        return f"bl:token:{jti}"

    # ── 工作记忆（Redis Hash） ──
    @staticmethod
    def working_memory(session_id: str) -> str:
        """会话级工作记忆 Hash Key (TTL 30min)"""
        return f"wm:{session_id}"

    # ── 码表缓存 ──
    @staticmethod
    def codebook(entity_type: str, alias: str) -> str:
        """码表查询缓存 (TTL 1h)"""
        return f"cb:{entity_type}:{alias}"

    # ── 限流计数器 ──
    @staticmethod
    def rate_limit(user_id: str, window: str) -> str:
        """滑动窗口限流计数器 (TTL 1min)"""
        return f"rl:{user_id}:{window}"

    # ── Prompt 模板缓存 ──
    @staticmethod
    def template(intent_pattern: str) -> str:
        """Prompt 模板缓存 (TTL 由 TEMPLATE_CACHE_TTL 控制)"""
        return f"tpl:{intent_pattern}"

    # ── 告警事件 ──
    @staticmethod
    def alert_event(event_type: str) -> str:
        """告警事件 Sorted Set"""
        return f"alert:{event_type}"

settings = get_settings()

redis_pool = aioredis.ConnectionPool.from_url(
    settings.REDIS_URL,
    max_connections=settings.REDIS_MAX_CONNECTIONS,
    decode_responses=True,
    socket_timeout=5,
    socket_connect_timeout=5,
    retry_on_timeout=True,
)

redis_client = aioredis.Redis(connection_pool=redis_pool)


async def get_redis() -> aioredis.Redis:
    """FastAPI 依赖注入：获取 Redis 客户端"""
    return redis_client
