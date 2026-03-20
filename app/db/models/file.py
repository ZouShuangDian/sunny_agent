"""
文件模型：File - 统一管理所有文件元数据

包括会话生成的文件和用户上传的文件
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid6 import uuid7

from app.config import get_settings
from app.db.models.base import Base

settings = get_settings()
_schema = settings.DB_SCHEMA


class File(Base):
    """文件表：统一管理所有文件（会话文件 + 项目文件）"""

    __tablename__ = "files"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    file_name: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="原始文件名（含扩展名）"
    )
    file_path: Mapped[str] = mapped_column(
        String(1024), nullable=False, comment="相对路径"
    )
    file_size: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="文件大小（字节）"
    )
    mime_type: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="MIME 类型"
    )
    file_extension: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="扩展名（小写）"
    )
    storage_filename: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="存储文件名（带UUID）"
    )
    file_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="SHA256 hash（去重/完整性）"
    )
    description: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="文件描述（max 500字符）"
    )
    tags: Mapped[list[str] | None] = mapped_column(
        JSONB, nullable=True, comment="标签数组（JSONB）"
    )
    
    # 上传信息
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{_schema}.users.id"),
        nullable=False,
        comment="上传者",
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="上传时间"
    )
    
    # 关联关系
    session_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="关联会话 ID"
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{_schema}.projects.id", ondelete="SET NULL"),
        nullable=True,
        comment="关联项目 ID"
    )
    file_context: Mapped[str] = mapped_column(
        String(32), nullable=False, default="project", comment="上下文：project/session/session_in_project/feishu_private/feishu_group"
    )
    
    # 飞书来源文件字段
    feishu_app_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True, comment="飞书应用 ID"
    )
    feishu_message_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True, comment="飞书消息 ID"
    )
    feishu_file_key: Mapped[str | None] = mapped_column(
        String(256), nullable=True, comment="飞书文件 key"
    )
    feishu_chat_type: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="聊天类型：p2p/group"
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{_schema}.projects.id", ondelete="SET NULL"),
        nullable=True,
        comment="关联项目ID"
    )
    file_context: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="上下文：project | session | session_in_project"
    )
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    # 反向关联
    uploader: Mapped["User"] = relationship("User", back_populates="files")
    project: Mapped["Project"] = relationship("Project", back_populates="files")
    feishu_media: Mapped[list["FeishuMediaFiles"]] = relationship(
        "FeishuMediaFiles", 
        back_populates="file_record",
        foreign_keys="FeishuMediaFiles.file_id"
    )

    # 索引
    __table_args__ = (
        Index("ix_files_session", "session_id"),
        Index("ix_files_project", "project_id"),
        Index("ix_files_hash", "file_hash"),
        Index("ix_files_uploaded_by", "uploaded_by"),
        Index("ix_files_context", "file_context"),
        Index("ix_files_project_session", "project_id", "session_id"),
        Index("ix_files_feishu_message", "feishu_message_id"),
        Index("ix_files_feishu_app_chat", "feishu_app_id", "feishu_chat_type", "feishu_message_id"),
        {"schema": _schema},
    )
