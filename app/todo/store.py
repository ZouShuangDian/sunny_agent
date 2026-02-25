"""
Todo Redis 存储层

每个会话独立存储，Key = todo:{session_id}，TTL = 7 天。
session_id 为空字符串时（SubAgent 隔离场景）直接跳过，不读写 Redis。

容错策略：
- get() 失败时：记录错误日志并返回空列表（保证 ReAct 循环不因 Todo 崩溃）
- set() 失败时：记录错误日志并静默忽略（写入失败不阻断工具执行）
"""

import json

import structlog

from app.cache.redis_client import RedisKeys, redis_client

log = structlog.get_logger()

TODO_TTL = 86400 * 7  # 7 天


class TodoStore:
    """会话级 Todo 列表的 Redis CRUD"""

    @staticmethod
    async def get(session_id: str) -> list[dict]:
        """读取当前会话的 Todo 列表，不存在或 Redis 不可用时返回空列表"""
        if not session_id:
            return []
        try:
            raw = await redis_client.get(RedisKeys.todo(session_id))
            if not raw:
                return []
            return json.loads(raw)
        except Exception as e:
            # Redis 瞬时不可用时降级：返回空列表，ReAct 循环正常继续
            log.error("TodoStore.get 失败，降级返回空列表", session_id=session_id, error=str(e))
            return []

    @staticmethod
    async def set(session_id: str, todos: list[dict]) -> None:
        """覆盖写入当前会话的 Todo 列表，Redis 不可用时静默忽略"""
        if not session_id:
            return
        try:
            await redis_client.set(
                RedisKeys.todo(session_id),
                json.dumps(todos, ensure_ascii=False),
                ex=TODO_TTL,
            )
        except Exception as e:
            # 写入失败不阻断工具执行，日志留存供排查
            log.error("TodoStore.set 失败，Todo 状态未持久化", session_id=session_id, error=str(e))
