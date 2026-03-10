"""
项目模型：Project - 管理用户项目

用于组织对话和文件的项目维度管理
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func, desc
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid6 import uuid7

from app.config import get_settings
from app.db.models.base import Base

settings = get_settings()
_schema = settings.DB_SCHEMA


class Project(Base):
    """项目表：用户项目的基本信息"""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    name: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="项目名称"
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{_schema}.users.id"),
        nullable=False,
        comment="项目所有者",
    )
    company: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="公司（数据隔离）"
    )
    
    # 计数器冗余字段
    file_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", comment="项目文件数量"
    )
    session_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", comment="项目内对话数量"
    )
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    # 反向关联
    owner: Mapped["User"] = relationship("User", back_populates="projects")
    sessions: Mapped[list["ChatSession"]] = relationship(
        "ChatSession", back_populates="project"
    )
    files: Mapped[list["File"]] = relationship(
        "File", back_populates="project"
    )

    # 索引
    __table_args__ = (
        Index("ix_projects_owner", "owner_id"),
        Index("ix_projects_company", "company"),
        Index("ix_projects_updated", desc("updated_at")),
        {"schema": _schema},
    )
