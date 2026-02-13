"""
请求限流器（Phase 1 占位）
Phase 2 填充：基于 Redis 的滑动窗口限流，按角色分级
"""


class RateLimiter:
    """
    基于 Redis 的滑动窗口限流

    TODO Phase 2:
    - 按角色分级限流：viewer 30rpm / operator 60rpm / manager 100rpm / admin 200rpm
    - 按天限流：rpd 配额
    - Redis INCR + EXPIRE 实现滑动窗口
    """

    async def check(self, user_id: str, role: str) -> bool:
        """Phase 1: 直接放行，不做限流"""
        return True


# 单例
rate_limiter = RateLimiter()


async def check_rate_limit() -> bool:
    """FastAPI 依赖注入：限流检查（Phase 1 直接放行）"""
    return True
