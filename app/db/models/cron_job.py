"""
定时任务模型
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.db.models.base import Base


class CronJob(Base):
    """用户定时任务"""

    __tablename__ = "cron_jobs"
    __table_args__ = (
        Index("ix_cron_jobs_next_run", "enabled", "next_run_at"),
        {"schema": Base.__table_args__["schema"]},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    usernumb: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="创建者工号"
    )
    name: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="任务名称"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- 调度 --
    cron_expr: Mapped[str] = mapped_column(
        String(100), nullable=False,
        comment="标准 5 字段 Cron 表达式：分 时 日 月 周",
    )
    timezone: Mapped[str] = mapped_column(
        String(50), nullable=False, default="Asia/Shanghai"
    )

    # -- 执行参数 --
    input_text: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="投喂给 Agent 的用户消息（等同于 /chat 的 message）",
    )
    session_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
        comment="结果推送到哪个会话（null 则每次创建新会话）",
    )

    # -- 状态 --
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="到期日期（可选），到期后 Scanner 自动禁用",
    )
    next_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        comment="下次触发时间（Scanner 按此查询，原子 UPDATE 推进）",
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_status: Mapped[str | None] = mapped_column(
        String(16), nullable=True,
        comment="上次执行状态：running / completed / failed / timeout",
    )
    last_error: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="上次失败原因"
    )

    # -- 统计 --
    run_count: Mapped[int] = mapped_column(Integer, default=0, comment="累计执行次数")
    fail_count: Mapped[int] = mapped_column(Integer, default=0, comment="累计失败次数")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
