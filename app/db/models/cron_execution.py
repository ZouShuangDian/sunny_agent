"""
定时任务执行记录模型
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.db.models.base import Base


class CronJobExecution(Base):
    """定时任务单次执行记录"""

    __tablename__ = "cron_job_executions"
    __table_args__ = (
        Index("ix_cron_exec_job_id", "cron_job_id"),
        Index("ix_cron_exec_usernumb_status", "usernumb", "status"),
        Index("ix_cron_exec_completed_at", "completed_at"),
        {"schema": Base.__table_args__["schema"]},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7,
    )
    cron_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False,
        comment="关联的定时任务 ID",
    )
    usernumb: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="任务所属用户工号",
    )
    name: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="执行时的任务名称（快照）",
    )
    input_text: Mapped[str] = mapped_column(
        Text, nullable=False, comment="执行时的输入文本（快照）",
    )
    session_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="执行结果写入的会话 ID",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="running",
        comment="执行状态：running / completed / failed / timeout",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="失败原因",
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
