"""
请求限流器：基于 Redis 的滑动窗口限流，按 app_id 隔离
"""

import logging
import time
from app.cache.redis_client import redis_client, RateLimitRedisKeys

logger = logging.getLogger(__name__)

# TTL constants (in seconds)
CONCURRENT_TTL = 300  # 5 minutes
RPM_TTL = 60          # 1 minute
RETRY_TTL = 60        # 1 minute


class RateLimiter:
    """
    基于 Redis 的滑动窗口限流

    配置参数:
        max_rpm: 每分钟最大请求数
        max_concurrent: 最大并发数
        delay_seconds: 延迟秒数
        max_delay_attempts: 最大延迟尝试次数
    """

    def __init__(
        self,
        max_rpm: int = 20,
        max_concurrent: int = 1,
        delay_seconds: int = 3,
        max_delay_attempts: int = 3
    ):
        self.max_rpm = max_rpm
        self.max_concurrent = max_concurrent
        self.delay_seconds = delay_seconds
        self.max_delay_attempts = max_delay_attempts
        self.redis_client = redis_client

    def _get_concurrent_key(self, app_id: str, user_id: str, chat_id: str) -> str:
        """生成并发计数 Key"""
        return RateLimitRedisKeys.concurrent(app_id, user_id, chat_id)

    def _get_rpm_key(self, app_id: str, user_id: str) -> str:
        """生成 RPM Key（使用时间戳窗口）"""
        timestamp = int(time.time() // 60)
        return RateLimitRedisKeys.rpm(app_id, user_id, timestamp)

    def _get_retry_key(self, app_id: str, user_id: str, message_id: str) -> str:
        """生成重试计数 Key"""
        return RateLimitRedisKeys.retry(app_id, user_id, message_id)

    async def check_rate_limit(
        self,
        app_id: str,
        user_id: str,
        message_id: str,
        chat_id: str,
        msg_type: str = "text"
    ) -> tuple[bool, str]:
        """
        Check rate limit for a message.
        
        Args:
            app_id: Application ID for isolation
            user_id: User ID (open_id)
            message_id: Message ID for tracking
            chat_id: Chat ID for isolation
            msg_type: Message type (text/image/file/audio/media)
        
        Returns:
            (allowed, reason): 
            - (True, "ok"): Allowed to proceed
            - (False, "concurrent_limit"): Concurrent limit exceeded
            - (False, "rpm_limit"): RPM limit exceeded
        
        Example:
            >>> limiter = RateLimiter()
            >>> allowed, reason = await limiter.check_rate_limit("app1", "user1", "msg1", "chat1")
            >>> if not allowed:
            ...     logger.warning(f"Rate limited: {reason}")
        """
        if msg_type in ["image", "file", "audio", "media"]:
            return (True, "ok")
        
        concurrent_key = self._get_concurrent_key(app_id, user_id, chat_id)
        rpm_key = self._get_rpm_key(app_id, user_id)

        try:
            concurrent_count = await self.redis_client.scard(concurrent_key)
            if concurrent_count >= self.max_concurrent:
                return (False, "concurrent_limit")

            rpm_count_str = await self.redis_client.get(rpm_key)
            rpm_count = int(rpm_count_str) if rpm_count_str else 0
            if rpm_count >= self.max_rpm:
                return (False, "rpm_limit")

            return (True, "ok")
        except Exception as e:
            logger.error(f"Rate limiter Redis error: {e}")
            raise

    async def start_processing(
        self,
        app_id: str,
        user_id: str,
        message_id: str,
        chat_id: str
    ) -> None:
        """开始处理，增加并发计数"""
        concurrent_key = self._get_concurrent_key(app_id, user_id, chat_id)
        try:
            await self.redis_client.sadd(concurrent_key, message_id)
            await self.redis_client.expire(concurrent_key, CONCURRENT_TTL)
        except Exception as e:
            logger.error(f"Rate limiter Redis error: {e}")
            raise

    async def end_processing(
        self,
        app_id: str,
        user_id: str,
        message_id: str,
        chat_id: str
    ) -> None:
        """处理完成，减少并发计数"""
        concurrent_key = self._get_concurrent_key(app_id, user_id, chat_id)
        try:
            await self.redis_client.srem(concurrent_key, message_id)
        except Exception as e:
            logger.error(f"Rate limiter Redis error: {e}")
            raise

    async def increment_rpm(self, app_id: str, user_id: str) -> int:
        """增加 RPM 计数"""
        rpm_key = self._get_rpm_key(app_id, user_id)
        try:
            count = await self.redis_client.incr(rpm_key)
            await self.redis_client.expire(rpm_key, RPM_TTL)
            return count
        except Exception as e:
            logger.error(f"Rate limiter Redis error: {e}")
            raise

    async def get_retry_count(
        self,
        app_id: str,
        user_id: str,
        message_id: str
    ) -> int:
        """获取重试次数"""
        retry_key = self._get_retry_key(app_id, user_id, message_id)
        try:
            count_str = await self.redis_client.get(retry_key)
            return int(count_str) if count_str else 0
        except Exception as e:
            logger.error(f"Rate limiter Redis error: {e}")
            raise

    async def increment_retry(
        self,
        app_id: str,
        user_id: str,
        message_id: str
    ) -> int:
        """增加重试次数"""
        retry_key = self._get_retry_key(app_id, user_id, message_id)
        try:
            count = await self.redis_client.incr(retry_key)
            await self.redis_client.expire(retry_key, RETRY_TTL)
            return count
        except Exception as e:
            logger.error(f"Rate limiter Redis error: {e}")
            raise


rate_limiter = RateLimiter()
