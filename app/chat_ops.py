"""
Agent 执行生命周期管理

agent_scope context manager 统一管理 running → 执行 → active 生命周期，
替代手动在各调用方分散写 set running + finalize_execution。
"""

import asyncio

import structlog
from contextlib import asynccontextmanager

from sqlalchemy import update, func

from app.db.engine import async_session
from app.db.models.chat import ChatSession
from app.cache.redis_client import redis_client, RedisKeys
from app.memory.chat_persistence import ChatPersistence
from app.memory.schemas import Message, L3Step

log = structlog.get_logger()


@asynccontextmanager
async def agent_scope(
    session_id: str,
    chat_persistence: ChatPersistence,
):
    """
    Agent 执行生命周期管理。

    用法：
        async with agent_scope(session_id, chat_persistence) as scope:
            result = await react_engine.execute(...)
            scope.set_result(message_id, msg, reasoning_trace, l3_steps)
        # 退出时自动：save_message → save_l3_steps → DEL Redis → status=active
    """
    # ── 进入：status → running ──
    async with async_session() as db:
        await db.execute(
            update(ChatSession)
            .where(ChatSession.session_id == session_id)
            .values(status="running")
        )
        await db.commit()

    scope = _AgentScope(session_id, chat_persistence)
    try:
        yield scope
    finally:
        # ── 退出：顺序收尾 ──
        await scope._finalize()


class _AgentScope:
    def __init__(self, session_id: str, chat_persistence: ChatPersistence):
        self._session_id = session_id
        self._persistence = chat_persistence
        self._result_set = False
        self._message_id: str | None = None
        self._msg: Message | None = None
        self._reasoning_trace = None
        self._l3_steps: list | None = None

    def set_result(
        self,
        message_id: str,
        msg: Message,
        reasoning_trace: dict | list | None = None,
        l3_steps: list | None = None,
    ):
        self._result_set = True
        self._message_id = message_id
        self._msg = msg
        self._reasoning_trace = reasoning_trace
        self._l3_steps = l3_steps

    async def _finalize(self):
        """
        顺序 await：①PG 写消息 → ②PG 写 steps → ③DEL Redis → ④status=active
        用 shield 保护，防止外部 CancelledError 中断收尾流程。
        """
        try:
            await asyncio.shield(self._do_finalize())
        except asyncio.CancelledError:
            pass  # shield 内部任务继续执行

    async def _do_finalize(self):
        try:
            if self._result_set:
                # ① 写 assistant 消息
                await self._persistence.save_message(
                    self._session_id, self._msg, self._reasoning_trace
                )
                # ② 写 l3_steps（exec_result.l3_steps 是 list[dict]，需转为 Pydantic）
                if self._l3_steps:
                    l3_step_objs = [L3Step(**s) for s in self._l3_steps]
                    await self._persistence.save_l3_steps(
                        self._session_id, self._message_id, l3_step_objs
                    )

            # ③ 清理 Redis live steps（无论是否有 result，都要清理）
            await redis_client.delete(RedisKeys.live_steps(self._session_id))

            # ④ 更新 status=active + last_active_at
            # 注：save_message 内部也会更新 last_active_at，此处为 intentional 双写：
            # 即使 save_message 未执行（result_set=False），也要刷新活跃时间
            async with async_session() as db:
                await db.execute(
                    update(ChatSession)
                    .where(
                        ChatSession.session_id == self._session_id,
                        ChatSession.status != "archived",
                    )
                    .values(status="active", last_active_at=func.now())
                )
                await db.commit()

        except Exception as e:
            log.warning("执行收尾失败", session_id=self._session_id, error=str(e))


async def set_session_running(session_id: str) -> None:
    """流式场景：在请求层设 running（不通过 agent_scope）"""
    async with async_session() as db:
        await db.execute(
            update(ChatSession)
            .where(ChatSession.session_id == session_id)
            .values(status="running")
        )
        await db.commit()


async def cleanup_session(session_id: str) -> None:
    """
    中断场景：清理 Redis live_steps + status → active。

    中断时 live_steps 不持久化到 PG 是有意设计：
    中断产生的步骤是不完整的，持久化反而产生脏数据。
    """
    try:
        await redis_client.delete(RedisKeys.live_steps(session_id))
        async with async_session() as db:
            await db.execute(
                update(ChatSession)
                .where(
                    ChatSession.session_id == session_id,
                    ChatSession.status != "archived",
                )
                .values(status="active", last_active_at=func.now())
            )
            await db.commit()
    except Exception as e:
        log.warning("中断清理失败", session_id=session_id, error=str(e))


async def finalize_execution(
    session_id: str,
    chat_persistence: ChatPersistence,
    message_id: str,
    msg: Message,
    reasoning_trace: dict | list | None = None,
    l3_steps: list | None = None,
) -> None:
    """
    流式场景：生成器结束后用 create_task 调用此函数做顺序收尾。

    注：直接调用 _do_finalize() 而非 _finalize()（跳过 asyncio.shield），
    因为本函数由 create_task 创建的独立 Task 调用，不会被生成器 cancel 波及，
    无需 shield 保护。
    """
    scope = _AgentScope(session_id, chat_persistence)
    scope.set_result(message_id, msg, reasoning_trace, l3_steps)
    await scope._do_finalize()
