"""
用户通知模型
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.config import get_settings
from app.db.models.base import Base

_schema = get_settings().DB_SCHEMA


class Notification(Base):
    """用户通知"""

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_unread", "usernumb", "is_read", "created_at"),
        {"schema": _schema},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    usernumb: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="接收者工号"
    )

    # ── 通知内容 ──
    type: Mapped[str] = mapped_column(
        String(32), nullable=False,
        comment="通知类型：cron_completed / cron_failed",
    )
    title: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="通知标题"
    )
    content: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="通知详情（可选）"
    )

    # ── 关联资源（可选） ──
    session_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
        comment="关联的会话 ID（点击通知跳转用）",
    )
    cron_job_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
        comment="关联的定时任务 ID（str(UUID)）",
    )

    # ── 状态 ──
    is_read: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", comment="是否已读"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
