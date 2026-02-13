"""
M13-3 Redis 滑动窗口告警

基于 Redis Sorted Set 实现事件计数，当窗口内事件数超过阈值时触发告警。

告警规则：
- llm_error: LLM 连续失败 3 次 / 5 分钟
- high_error_rate: 错误率突增（10 次 / 5 分钟）
"""

import time
from dataclasses import dataclass

import redis.asyncio as aioredis
import structlog

from app.cache.redis_client import RedisKeys

log = structlog.get_logger()


@dataclass
class AlertRule:
    """告警规则"""

    event_type: str  # 事件类型
    threshold: int  # 阈值（窗口内最大事件数）
    window_seconds: int  # 时间窗口（秒）
    description: str  # 规则描述


# ── 预定义告警规则 ──

DEFAULT_RULES: list[AlertRule] = [
    AlertRule(
        event_type="llm_error",
        threshold=3,
        window_seconds=300,
        description="LLM 连续失败 3 次 / 5 分钟",
    ),
    AlertRule(
        event_type="high_error_rate",
        threshold=10,
        window_seconds=300,
        description="错误率突增：10 次 / 5 分钟",
    ),
    AlertRule(
        event_type="tool_error",
        threshold=5,
        window_seconds=300,
        description="工具调用失败 5 次 / 5 分钟",
    ),
]


class AlertManager:
    """Redis 滑动窗口告警管理器"""

    def __init__(self, redis: aioredis.Redis, rules: list[AlertRule] | None = None):
        self.redis = redis
        self.rules = {r.event_type: r for r in (rules or DEFAULT_RULES)}

    async def record_event(self, event_type: str) -> bool:
        """
        记录一次告警事件，并检查是否触发告警。
        返回 True 表示触发了告警。
        """
        key = RedisKeys.alert_event(event_type)
        now = time.time()

        # ZADD 记录事件时间戳（score 和 member 都用时间戳）
        await self.redis.zadd(key, {str(now): now})

        # 设置 key 过期（防止无限增长）
        rule = self.rules.get(event_type)
        if rule:
            await self.redis.expire(key, rule.window_seconds * 2)

        # 检查是否超过阈值
        return await self.check_threshold(event_type)

    async def check_threshold(self, event_type: str) -> bool:
        """检查窗口内事件数是否超过阈值"""
        rule = self.rules.get(event_type)
        if not rule:
            return False

        key = RedisKeys.alert_event(event_type)
        now = time.time()
        window_start = now - rule.window_seconds

        # 清理过期事件
        await self.redis.zremrangebyscore(key, 0, window_start)

        # 统计窗口内事件数
        count = await self.redis.zcard(key)

        if count >= rule.threshold:
            await self._send_alert(rule, count)
            return True

        return False

    async def _send_alert(self, rule: AlertRule, count: int) -> None:
        """
        发送告警。
        Phase 1: 写日志 + log.critical。
        Phase 2+: 接入企业微信/钉钉通知。
        """
        log.critical(
            "告警触发",
            event_type=rule.event_type,
            threshold=rule.threshold,
            window_seconds=rule.window_seconds,
            current_count=count,
            description=rule.description,
        )
