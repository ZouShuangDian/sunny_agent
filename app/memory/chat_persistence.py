"""
聊天记录持久化服务（PG 冷存储）

职责：
- 写入：每条 user/assistant 消息异步写入 PG（write-behind，不阻塞主流程）
- 读取：Redis miss 时从 PG 回源加载会话历史

设计要点：
- tool_calls（W7）：从 msg.tool_calls 直接读取，挂载在 Message 模型上
- reasoning_trace（W6）：独立参数传入，不经过 Message 模型
- 写入失败静默降级，不影响用户对话
"""

import asyncio
from datetime import datetime, timezone

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import func

from app.config import get_settings
from app.db.models.chat import ChatMessage, ChatSession
from app.memory.schemas import ConversationHistory, Message, ToolCall

log = structlog.get_logger()
settings = get_settings()


class ChatPersistence:
    """聊天记录持久化服务（PG 冷存储）"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    # ── 写入 ──

    async def ensure_session(
        self,
        session_id: str,
        user_id: str,
        first_message: str | None = None,
    ) -> None:
        """确保 PG 中存在会话记录（幂等）"""
        async with self._session_factory() as db:
            existing = await db.execute(
                select(ChatSession).where(ChatSession.session_id == session_id)
            )
            if existing.scalar_one_or_none():
                return
            title = (
                first_message[:50] + "..."
                if first_message and len(first_message) > 50
                else first_message
            )
            db.add(ChatSession(
                session_id=session_id,
                user_id=user_id,
                title=title,
            ))
            await db.commit()

    def ensure_session_background(
        self,
        session_id: str,
        user_id: str,
        first_message: str | None = None,
    ) -> None:
        """异步创建 PG 会话（发后即忘）"""
        asyncio.create_task(
            self._safe_ensure_session(session_id, user_id, first_message)
        )

    async def _safe_ensure_session(
        self,
        session_id: str,
        user_id: str,
        first_message: str | None = None,
    ) -> None:
        try:
            await self.ensure_session(session_id, user_id, first_message)
        except Exception as e:
            log.warning("会话创建持久化失败", session_id=session_id, error=str(e))

    async def save_message(
        self,
        session_id: str,
        msg: Message,
        reasoning_trace: dict | list | None = None,
    ) -> None:
        """
        保存单条消息到 PG。

        Args:
            session_id: 会话 ID
            msg: Message 对象（含 tool_calls，W7 从 Message 直接读取）
            reasoning_trace: L3 推理轨迹（W6，从 ExecutionResult 提取，不走 Message）
        """
        async with self._session_factory() as db:
            db.add(ChatMessage(
                session_id=session_id,
                message_id=msg.message_id,
                role=msg.role,
                content=msg.content,
                intent_primary=msg.intent_primary,
                route=msg.route,
                model=msg.model,
                # W7：tool_calls 从 Message 模型直接读取（L1 + L3 通用）
                tool_calls=(
                    [tc.model_dump() for tc in msg.tool_calls]
                    if msg.tool_calls
                    else None
                ),
                # W6：reasoning_trace 独立参数（仅 L3 assistant）
                reasoning_trace=reasoning_trace,
                created_at=datetime.fromtimestamp(msg.timestamp, tz=timezone.utc),
            ))
            await db.execute(
                update(ChatSession)
                .where(ChatSession.session_id == session_id)
                .values(last_active_at=func.now())
            )
            await db.commit()

    def save_message_background(
        self,
        session_id: str,
        msg: Message,
        reasoning_trace: dict | list | None = None,
    ) -> None:
        """发后即忘（与 audit_logger.log_background 同模式）"""
        asyncio.create_task(self._safe_save(session_id, msg, reasoning_trace))

    async def _safe_save(
        self,
        session_id: str,
        msg: Message,
        reasoning_trace: dict | list | None = None,
    ) -> None:
        """PG 写入失败静默降级，不影响主流程"""
        try:
            await self.save_message(session_id, msg, reasoning_trace)
        except Exception as e:
            log.warning("聊天记录持久化失败", session_id=session_id, error=str(e))

    # ── 读取（Redis miss 时回源） ──

    async def load_history(self, session_id: str) -> ConversationHistory | None:
        """从 PG 加载会话历史"""
        async with self._session_factory() as db:
            result = await db.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at.asc())
            )
            rows = result.scalars().all()
            if not rows:
                return None

            history = ConversationHistory(max_turns=settings.WORKING_MEMORY_MAX_TURNS)
            for row in rows:
                history.append(Message(
                    role=row.role,
                    content=row.content,
                    timestamp=row.created_at.timestamp(),
                    message_id=row.message_id,
                    intent_primary=row.intent_primary,
                    route=row.route,
                    model=row.model,
                    # W7：从 PG JSONB 还原 ToolCall 对象（回源时 LLM 需要 FC 上下文）
                    tool_calls=(
                        [ToolCall(**tc) for tc in row.tool_calls]
                        if row.tool_calls
                        else None
                    ),
                ))
            return history
