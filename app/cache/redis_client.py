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

    # ── 通知 Pub/Sub ──
    @staticmethod
    def notify_channel(usernumb: str) -> str:
        """用户级通知 Pub/Sub channel（SSE 订阅用）"""
        return f"notify:{usernumb}"


class FeishuRedisKeys:
    """
    飞书集成模块 Redis Key 统一管理
    命名规范：feishu:{功能类型}:{标识}
    """

    # ── ARQ 队列 ──
    ARQ_QUEUE = "arq:feishu:queue"

    # ── Webhook 消息队列 ──
    EXTERNAL_WEBHOOK_QUEUE = "feishu:webhook:queue"
    PROCESSING_QUEUE = "feishu:processing:queue"

    # ── Token 缓存 ──
    @staticmethod
    def token(app_id: str) -> str:
        """飞书应用 Token 缓存 (TTL 7000s)"""
        return f"feishu:token:{app_id}"

    # ── 用户缓存 ──
    @staticmethod
    def user(app_id: str, open_id: str) -> str:
        """飞书用户身份解析缓存 (TTL 1h)"""
        return f"feishu:user:{app_id}:{open_id}"

    # ── 幂等校验 ──
    @staticmethod
    def processed(event_id: str, message_id: str) -> str:
        """消息幂等校验标记 (TTL 24h)"""
        return f"feishu:processed:{event_id}:{message_id}"

    # ── 消息防抖（Debounce） ──
    @staticmethod
    def debounce_buffer(open_id: str, chat_id: str) -> str:
        """防抖消息缓冲队列"""
        return f"feishu:buffer:{open_id}:{chat_id}"

    @staticmethod
    def debounce_state(open_id: str, chat_id: str) -> str:
        """防抖状态标记"""
        return f"feishu:state:{open_id}:{chat_id}"

    @staticmethod
    def debounce_timer(open_id: str, chat_id: str) -> str:
        """防抖定时器标记"""
        return f"feishu:timer:{open_id}:{chat_id}"

    @staticmethod
    def debounce_no_text(open_id: str, chat_id: str) -> str:
        """无文本防抖标记"""
        return f"feishu:no_text:{open_id}:{chat_id}"

    @staticmethod
    def debounce_lock(open_id: str, chat_id: str) -> str:
        """防抖分布式锁"""
        return f"feishu:lock:{open_id}:{chat_id}"


# 保持向后兼容：导出常用 key 生成函数
token_blacklist = RedisKeys.token_blacklist
working_memory = RedisKeys.working_memory
rate_limit = RedisKeys.rate_limit
feishu_token = FeishuRedisKeys.token
feishu_user = FeishuRedisKeys.user
feishu_processed = FeishuRedisKeys.processed

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
