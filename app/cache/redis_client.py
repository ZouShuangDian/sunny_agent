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

    # ── 限流计数器 ──
    @staticmethod
    def rate_limit(user_id: str, window: str) -> str:
        """滑动窗口限流计数器 (TTL 1min)"""
        return f"rl:{user_id}:{window}"

    # ── 告警事件 ──
    @staticmethod
    def alert_event(event_type: str) -> str:
        """告警事件 Sorted Set"""
        return f"alert:{event_type}"

    # ── Todo 列表 ──
    @staticmethod
    def todo(session_id: str) -> str:
        """会话级 Todo 任务列表 (TTL 7天)"""
        return f"todo:{session_id}"


    @staticmethod
    def sso_ticket_result(ticket: str) -> str:
        """SSO ticket login result cache key."""
        return f"sso:ticket:result:{ticket}"

    @staticmethod
    def sso_ticket_lock(ticket: str) -> str:
        """SSO ticket processing lock key."""
        return f"sso:ticket:lock:{ticket}"

    # ── Agent 执行中实时步骤 ──
    @staticmethod
    def live_steps(session_id: str) -> str:
        """Agent 执行中的实时步骤（Redis List，TTL 24h 兜底）"""
        return f"live_steps:{session_id}"

    # ── 用户存在性缓存 ──
    @staticmethod
    def user_active(user_id: str) -> str:
        """用户存在且激活的缓存标记（TTL 10min）"""
        return f"ua:{user_id}"

    # ── 异步任务进度事件 ──
    @staticmethod
    def task_events(task_id: str) -> str:
        """异步任务事件日志 List（断线续传缓冲，TTL=1h）"""
        return f"task_events:{task_id}"

    @staticmethod
    def task_channel(task_id: str) -> str:
        """异步任务进度 Pub/Sub channel（任务 SSE 端点订阅用）"""
        return f"task_ch:{task_id}"

    # ── 通知 Pub/Sub ──
    @staticmethod
    def notify_channel(usernumb: str) -> str:
        """用户级通知 Pub/Sub channel（SSE 订阅用）"""
        return f"notify:{usernumb}"

    # ── Langfuse 可观测性缓存 ──
    @staticmethod
    def langfuse_health() -> str:
        """Langfuse 健康状态缓存 (TTL 5min)"""
        return "sunny:langfuse:health"

    @staticmethod
    def langfuse_usage(date: str, user_id: str = "all") -> str:
        """Langfuse 用量缓存 (TTL 5min)"""
        return f"sunny:langfuse:usage:{date}:{user_id}"

    @staticmethod
    def langfuse_usage_summary(start: str, end: str, user_id: str = "all") -> str:
        """Langfuse 用量汇总缓存 (TTL 5min)"""
        return f"sunny:langfuse:usage:summary:{start}:{end}:{user_id}"

    @staticmethod
    def langfuse_usage_daily(start: str, end: str, user_id: str = "all") -> str:
        """Langfuse 每日用量缓存 (TTL 5min)"""
        return f"sunny:langfuse:usage:daily:{start}:{end}:{user_id}"

    @staticmethod
    def langfuse_usage_by_user(start: str, end: str) -> str:
        """Langfuse 按用户用量缓存 (TTL 5min)"""
        return f"sunny:langfuse:usage:by_user:{start}:{end}"

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
