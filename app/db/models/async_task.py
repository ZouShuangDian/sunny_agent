"""
异步任务模型
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.config import get_settings
from app.db.models.base import Base

_schema = get_settings().DB_SCHEMA


class AsyncTask(Base):
    """异步任务（Agent 驱动，Worker 执行）"""

    __tablename__ = "async_tasks"
    __table_args__ = (
        Index("ix_async_tasks_usernumb_status", "usernumb", "status"),
        Index("ix_async_tasks_session", "session_id"),
        {"schema": _schema},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7,
        comment="主键（同时作为 arq job_id）",
    )
    usernumb: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="创建者工号",
    )
    user_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="创建者用户 UUID",
    )
    session_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="关联会话 ID（结果写入此会话）",
    )
    task_type: Mapped[str] = mapped_column(
        String(32), nullable=False,
        comment="任务类型：deep_research（后续可扩展）",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending",
        server_default="pending",
        comment="状态机：pending → running → completed | failed | timeout | cancelled",
    )
    input_text: Mapped[str] = mapped_column(
        Text, nullable=False, comment="Agent 加工后的完整任务描述",
    )
    result_summary: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="执行结果摘要（前 500 字符）",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="失败原因",
    )
    metadata_: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, nullable=True,
        comment="扩展字段（token_usage、iterations、duration_ms）",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
