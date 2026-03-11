"""
LiveStepsWriter：Agent 执行中实时步骤写入 Redis

每个 step 产生时 RPUSH 到 Redis List，静默降级不影响 Agent 执行。
正常流程由 agent_scope 在收尾阶段主动 DEL，24h TTL 仅作兜底。
"""

import json

import structlog
from redis.asyncio import Redis

from app.cache.redis_client import RedisKeys

log = structlog.get_logger()


class LiveStepsWriter:
    def __init__(self, redis: Redis, session_id: str):
        self._redis = redis
        self._key = RedisKeys.live_steps(session_id)
        self._session_id = session_id
        self._ttl_set = False

    async def push(self, steps: list[dict]) -> None:
        """将增量步骤 RPUSH 到 Redis List，首次写入时设置 24h TTL。"""
        if not steps:
            return
        try:
            pipe = self._redis.pipeline()
            for step in steps:
                pipe.rpush(self._key, json.dumps(step, ensure_ascii=False))
            need_ttl = not self._ttl_set
            if need_ttl:
                pipe.expire(self._key, 86400)
            await pipe.execute()
            if need_ttl:
                self._ttl_set = True
        except Exception as e:
            log.warning("live_steps 写入失败", session_id=self._session_id, error=str(e))
