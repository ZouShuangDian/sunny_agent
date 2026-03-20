from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models.chat import ChatMessage
from app.db.models.feishu import FeishuChatSessionMapping
from app.feishu.session_mapping_service import (
    acquire_feishu_session_lock,
    archive_mapping_and_session,
    create_chat_session,
    create_feishu_session_mapping,
    get_chat_session,
    inherit_latest_summary,
    list_due_feishu_mappings,
)
from app.llm.client import LLMClient
from app.memory.chat_persistence import ChatPersistence
from app.memory.schemas import ConversationHistory

log = structlog.get_logger()
settings = get_settings()

ARCHIVE_PROMPT = """Summarize this Feishu session for the next session.

Use this structure:
1. User goals
2. Confirmed facts
3. Important completed work
4. Pending topics
5. Preferences and constraints

Keep it concise, factual, and reusable for follow-up turns."""


@dataclass
class ArchiveStats:
    scanned: int = 0
    archived: int = 0
    skipped: int = 0
    inherited: int = 0


class FeishuSessionArchiveService:
    def __init__(self, llm: LLMClient, persistence: ChatPersistence):
        self.llm = llm
        self.persistence = persistence

    async def archive_due_sessions(
        self,
        db: AsyncSession,
        *,
        cutoff_at: datetime,
        limit: int,
    ) -> ArchiveStats:
        stats = ArchiveStats()
        mappings = await list_due_feishu_mappings(db, cutoff_at=cutoff_at, limit=limit)
        stats.scanned = len(mappings)

        for mapping in mappings:
            archived = await self.archive_mapping(db, mapping=mapping, cutoff_at=cutoff_at)
            if archived is None:
                stats.skipped += 1
            else:
                stats.archived += 1
                stats.inherited += 1

        return stats

    async def archive_mapping(
        self,
        db: AsyncSession,
        *,
        mapping: FeishuChatSessionMapping,
        cutoff_at: datetime,
    ) -> str | None:
        await acquire_feishu_session_lock(
            db,
            chat_id=mapping.chat_id,
            open_id=mapping.open_id,
        )

        if not mapping.is_active:
            return None
        if mapping.last_active_at and mapping.last_active_at >= cutoff_at:
            return None

        old_session = await get_chat_session(db, mapping.session_id)
        if old_session is None or old_session.status == "archived":
            return None

        history = await self.persistence.load_history(mapping.session_id)
        if history is None:
            return None
        if len(history.messages) < settings.FEISHU_SESSION_ARCHIVE_MIN_MESSAGES:
            return None

        summary_content = await self.build_archive_summary(
            session_id=mapping.session_id,
            history=history,
            model=settings.FEISHU_SESSION_ARCHIVE_MODEL,
        )

        if summary_content:
            db.add(
                ChatMessage(
                    session_id=mapping.session_id,
                    message_id=f"archive_{uuid.uuid4().hex}",
                    role="system",
                    content=summary_content,
                    model=settings.FEISHU_SESSION_ARCHIVE_MODEL,
                    is_compaction=True,
                    created_at=datetime.now(timezone.utc),
                )
            )
            await db.flush()

        await archive_mapping_and_session(db, mapping, old_session)
        new_session_id = await create_chat_session(
            db,
            user_id=old_session.user_id,
            title=old_session.title,
            source=old_session.source,
        )

        if summary_content:
            await inherit_latest_summary(db, old_session.session_id, new_session_id)

        await create_feishu_session_mapping(
            db,
            chat_id=mapping.chat_id,
            open_id=mapping.open_id,
            session_id=new_session_id,
            chat_type=mapping.chat_type,
            user_id=mapping.user_id,
            parent_session_id=old_session.session_id,
        )
        await db.commit()

        log.info(
            "Archived Feishu session mapping",
            old_session_id=old_session.session_id,
            new_session_id=new_session_id,
            chat_id=mapping.chat_id,
            open_id=mapping.open_id,
        )
        return new_session_id

    async def build_archive_summary(
        self,
        *,
        session_id: str,
        history: ConversationHistory,
        model: str,
    ) -> str:
        history_text = "\n\n".join(
            f"[{msg.role}] {msg.content}" for msg in history.messages if msg.content
        ).strip()
        if not history_text:
            return ""

        response = await self.llm.chat(
            messages=[
                {
                    "role": "user",
                    "content": f"{ARCHIVE_PROMPT}\n\nSession history:\n{history_text}",
                }
            ],
            model=model,
            max_tokens=settings.COMPACTION_MAX_TOKENS,
            temperature=0.0,
        )
        summary = response.content.strip()
        log.info(
            "Built Feishu archive summary",
            session_id=session_id,
            summary_length=len(summary),
        )
        return summary
