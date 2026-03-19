import uuid
import hashlib
from datetime import datetime, timedelta, timezone
from uuid import UUID

import structlog
from sqlalchemy import select, update, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models.chat import ChatMessage, ChatSession
from app.db.models.feishu import FeishuChatSessionMapping

log = structlog.get_logger()
settings = get_settings()


async def get_or_rotate_feishu_session(
    db: AsyncSession,
    *,
    open_id: str,
    chat_id: str,
    chat_type: str,
    user_id: UUID | None = None,
    source: str = "feishu",
) -> str:
    await _acquire_session_mapping_lock(db, chat_id=chat_id, open_id=open_id)
    active_mapping = await _get_active_mapping(db, chat_id=chat_id, open_id=open_id)
    if active_mapping:
        chat_session = await _get_chat_session(db, active_mapping.session_id)
        if chat_session and not _is_session_idle(chat_session.last_active_at):
            await _touch_mapping(db, active_mapping)
            return active_mapping.session_id

        if chat_session:
            await _archive_mapping_and_session(db, active_mapping, chat_session)
            new_session_id = await _create_chat_session(
                db,
                user_id=user_id,
                title=chat_session.title,
                source=source,
            )
            await _inherit_latest_summary(db, chat_session.session_id, new_session_id)
            await _create_mapping(
                db,
                chat_id=chat_id,
                open_id=open_id,
                session_id=new_session_id,
                chat_type=chat_type,
                user_id=user_id,
                parent_session_id=chat_session.session_id,
            )
            await db.commit()
            return new_session_id

    new_session_id = await _create_chat_session(db, user_id=user_id, source=source)
    await _create_mapping(
        db,
        chat_id=chat_id,
        open_id=open_id,
        session_id=new_session_id,
        chat_type=chat_type,
        user_id=user_id,
    )
    await db.commit()
    return new_session_id


async def touch_or_activate_feishu_session_mapping(
    db: AsyncSession,
    *,
    chat_id: str,
    open_id: str,
    session_id: str,
    chat_type: str,
    user_id: UUID | None = None,
) -> None:
    await _acquire_session_mapping_lock(db, chat_id=chat_id, open_id=open_id)
    existing = await db.execute(
        select(FeishuChatSessionMapping).where(
            FeishuChatSessionMapping.chat_id == chat_id,
            FeishuChatSessionMapping.open_id == open_id,
            FeishuChatSessionMapping.session_id == session_id,
        )
    )
    row = existing.scalar_one_or_none()
    if row:
        row.last_active_at = datetime.now(timezone.utc)
        row.message_count += 1
        row.is_active = True
        if row.archived_at is not None:
            row.archived_at = None
        await db.commit()
        return

    await db.execute(
        update(FeishuChatSessionMapping)
        .where(
            FeishuChatSessionMapping.chat_id == chat_id,
            FeishuChatSessionMapping.open_id == open_id,
            FeishuChatSessionMapping.is_active.is_(True),
        )
        .values(is_active=False, archived_at=datetime.now(timezone.utc))
    )
    await _create_mapping(
        db,
        chat_id=chat_id,
        open_id=open_id,
        session_id=session_id,
        chat_type=chat_type,
        user_id=user_id,
    )
    await db.commit()


async def _acquire_session_mapping_lock(
    db: AsyncSession,
    *,
    chat_id: str,
    open_id: str,
) -> None:
    """Serialize mapping updates per chat/open pair within the current transaction."""
    lock_key_material = f"feishu-session:{chat_id}:{open_id}".encode("utf-8")
    lock_key = int.from_bytes(hashlib.sha256(lock_key_material).digest()[:8], "big") & 0x7FFFFFFFFFFFFFFF
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": lock_key},
    )


async def _get_active_mapping(db: AsyncSession, *, chat_id: str, open_id: str) -> FeishuChatSessionMapping | None:
    result = await db.execute(
        select(FeishuChatSessionMapping)
        .where(
            FeishuChatSessionMapping.chat_id == chat_id,
            FeishuChatSessionMapping.open_id == open_id,
            FeishuChatSessionMapping.is_active.is_(True),
        )
        .order_by(FeishuChatSessionMapping.last_active_at.desc(), FeishuChatSessionMapping.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _get_chat_session(db: AsyncSession, session_id: str) -> ChatSession | None:
    result = await db.execute(
        select(ChatSession).where(ChatSession.session_id == session_id)
    )
    return result.scalar_one_or_none()


def _is_session_idle(last_active_at: datetime | None) -> bool:
    if last_active_at is None:
        return True
    threshold = datetime.now(timezone.utc) - timedelta(hours=settings.FEISHU_SESSION_IDLE_ARCHIVE_HOURS)
    if last_active_at.tzinfo is None:
        last_active_at = last_active_at.replace(tzinfo=timezone.utc)
    return last_active_at < threshold


async def _touch_mapping(db: AsyncSession, mapping: FeishuChatSessionMapping) -> None:
    mapping.last_active_at = datetime.now(timezone.utc)
    mapping.message_count += 1
    await db.commit()


async def _archive_mapping_and_session(
    db: AsyncSession,
    mapping: FeishuChatSessionMapping,
    chat_session: ChatSession,
) -> None:
    mapping.is_active = False
    mapping.archived_at = datetime.now(timezone.utc)
    chat_session.status = "archived"
    chat_session.last_active_at = datetime.now(timezone.utc)
    await db.flush()


async def _create_chat_session(
    db: AsyncSession,
    *,
    user_id: UUID | None,
    title: str | None = None,
    source: str = "feishu",
) -> str:
    session_id = str(uuid.uuid4())
    chat_session = ChatSession(
        session_id=session_id,
        user_id=user_id,
        title=title,
        source=source,
        status="active",
    )
    db.add(chat_session)
    await db.flush()
    return session_id


async def _create_mapping(
    db: AsyncSession,
    *,
    chat_id: str,
    open_id: str,
    session_id: str,
    chat_type: str,
    user_id: UUID | None,
    parent_session_id: str | None = None,
) -> None:
    db.add(
        FeishuChatSessionMapping(
            chat_id=chat_id,
            open_id=open_id,
            session_id=session_id,
            chat_type=chat_type,
            user_id=user_id,
            message_count=1,
            is_active=True,
            parent_session_id=parent_session_id,
        )
    )
    await db.flush()


async def _inherit_latest_summary(db: AsyncSession, old_session_id: str, new_session_id: str) -> None:
    result = await db.execute(
        select(ChatMessage)
        .where(
            ChatMessage.session_id == old_session_id,
            ChatMessage.is_compaction.is_(True),
        )
        .order_by(ChatMessage.created_at.desc())
        .limit(1)
    )
    latest_summary = result.scalar_one_or_none()
    if latest_summary is None:
        return

    db.add(
        ChatMessage(
            session_id=new_session_id,
            message_id=f"inherit_{uuid.uuid4().hex}",
            role=latest_summary.role,
            content=latest_summary.content,
            intent_primary=latest_summary.intent_primary,
            route=latest_summary.route,
            model=latest_summary.model,
            tool_calls=latest_summary.tool_calls,
            reasoning_trace=latest_summary.reasoning_trace,
            is_compaction=True,
            created_at=datetime.now(timezone.utc),
        )
    )
    log.info(
        "Inherited compaction summary into new Feishu session",
        old_session_id=old_session_id,
        new_session_id=new_session_id,
    )
