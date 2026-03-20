"""
M11-1 工作记忆：基于 Redis Hash 的会话级临时状态存储

每个会话一个 Hash Key（wm:{session_id}），TTL 自动过期。
所有 field 使用 Pydantic model_dump_json / model_validate_json 序列化。
"""

import time

import redis.asyncio as aioredis

from app.cache.redis_client import RedisKeys
from app.config import get_settings
from app.memory.schemas import (
    ConversationHistory,
    LastIntent,
    Message,
    SessionMeta,
)

settings = get_settings()


class WorkingMemory:
    """会话级工作记忆，基于 Redis Hash"""

    # Redis Hash 中的 field 名称常量
    FIELD_HISTORY = "history"
    FIELD_LAST_INTENT = "last_intent"
    FIELD_META = "meta"

    def __init__(self, redis: aioredis.Redis):
        self.redis = redis
        self.default_ttl = settings.WORKING_MEMORY_TTL

    # ── 基础操作 ──

    def _key(self, session_id: str) -> str:
        return RedisKeys.working_memory(session_id)

    async def _hset_and_expire(
        self, session_id: str, field: str, value: str, ttl: int | None = None
    ) -> None:
        """写入单个 field 并刷新 TTL"""
        key = self._key(session_id)
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.hset(key, field, value)
            pipe.expire(key, ttl or self.default_ttl)
            await pipe.execute()

    async def exists(self, session_id: str) -> bool:
        """检查会话是否存在"""
        return bool(await self.redis.exists(self._key(session_id)))

    async def touch(self, session_id: str, ttl: int | None = None) -> None:
        """续期（用户活跃时刷新 TTL）"""
        await self.redis.expire(self._key(session_id), ttl or self.default_ttl)

    async def clear(self, session_id: str) -> None:
        """清空整个会话"""
        await self.redis.delete(self._key(session_id))

    # ── 会话初始化 ──

    async def init_session(
        self, session_id: str, user_id: str, usernumb: str
    ) -> SessionMeta:
        """初始化新会话，写入元数据"""
        now = time.time()
        meta = SessionMeta(
            session_id=session_id,
            user_id=user_id,
            usernumb=usernumb,
            turn_count=0,
            created_at=now,
            last_active_at=now,
        )
        history = ConversationHistory(max_turns=settings.WORKING_MEMORY_MAX_TURNS)

        key = self._key(session_id)
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.hset(key, self.FIELD_META, meta.model_dump_json())
            pipe.hset(key, self.FIELD_HISTORY, history.model_dump_json())
            pipe.expire(key, self.default_ttl)
            await pipe.execute()

        return meta

    # ── 对话历史 ──

    async def get_history(self, session_id: str) -> ConversationHistory:
        """读取对话历史"""
        raw = await self.redis.hget(self._key(session_id), self.FIELD_HISTORY)
        if raw:
            return ConversationHistory.model_validate_json(raw)
        return ConversationHistory(max_turns=settings.WORKING_MEMORY_MAX_TURNS)

    async def append_message(self, session_id: str, msg: Message) -> None:
        """追加一条消息到对话历史"""
        history = await self.get_history(session_id)
        history.append(msg)
        await self._hset_and_expire(
            session_id, self.FIELD_HISTORY, history.model_dump_json()
        )

    # ── 上一轮意图 ──

    async def get_last_intent(self, session_id: str) -> LastIntent | None:
        """读取上一轮意图"""
        raw = await self.redis.hget(self._key(session_id), self.FIELD_LAST_INTENT)
        if raw:
            return LastIntent.model_validate_json(raw)
        return None

    async def save_last_intent(
        self, session_id: str, intent: LastIntent
    ) -> None:
        """保存本轮意图快照"""
        await self._hset_and_expire(
            session_id, self.FIELD_LAST_INTENT, intent.model_dump_json()
        )

    # ── 会话元数据 ──

    async def get_meta(self, session_id: str) -> SessionMeta | None:
        """读取会话元数据"""
        raw = await self.redis.hget(self._key(session_id), self.FIELD_META)
        if raw:
            return SessionMeta.model_validate_json(raw)
        return None

    async def increment_turn(self, session_id: str) -> int:
        """轮次 +1，更新最后活跃时间，返回新轮次号"""
        meta = await self.get_meta(session_id)
        if not meta:
            return 0
        meta.turn_count += 1
        meta.last_active_at = time.time()
        await self._hset_and_expire(
            session_id, self.FIELD_META, meta.model_dump_json()
        )
        return meta.turn_count
