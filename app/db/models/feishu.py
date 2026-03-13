"""
Feishu 数据库模型定义
包含访问控制、用户绑定、媒体文件、消息日志等表
"""

import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid6 import uuid7

from app.db.models.base import Base
from app.db.models.user import User


class DMPolicy(str, PyEnum):
    """私信访问策略"""
    OPEN = "open"           # 开放，所有人可用
    ALLOWLIST = "allowlist" # 白名单控制
    DISABLED = "disabled"   # 禁用


class GroupPolicy(str, PyEnum):
    """群聊访问策略"""
    OPEN = "open"           # 开放，所有群可用
    ALLOWLIST = "allowlist" # 白名单控制
    DISABLED = "disabled"   # 禁用


class MessageStatus(str, PyEnum):
    """消息处理状态"""
    RECEIVED = "received"       # 已接收
    BUFFERING = "buffering"     # 防抖缓冲中
    PROCESSING = "processing"   # 处理中
    COMPLETED = "completed"     # 已完成
    FAILED = "failed"           # 处理失败
    REJECTED = "rejected"       # 被拒绝


class MediaType(str, PyEnum):
    """媒体文件类型"""
    IMAGE = "image"
    FILE = "file"
    AUDIO = "audio"
    MEDIA = "media"
    STICKER = "sticker"


class FeishuAccessConfig(Base):
    """飞书访问控制配置表"""
    
    __tablename__ = "feishu_access_config"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    app_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="飞书应用ID")
    
    # 访问控制策略
    dm_policy: Mapped[str] = mapped_column(
        String(16), 
        nullable=False, 
        default=DMPolicy.OPEN.value,
        comment="私信策略: open/allowlist/disabled"
    )
    group_policy: Mapped[str] = mapped_column(
        String(16), 
        nullable=False, 
        default=GroupPolicy.OPEN.value,
        comment="群聊策略: open/allowlist/disabled"
    )
    
    # 白名单配置 (JSON数组存储 employee_no 或 chat_id)
    dm_allowlist: Mapped[list] = mapped_column(
        JSONB, 
        default=list, 
        server_default="[]",
        comment="私信白名单员工号列表"
    )
    group_allowlist: Mapped[list] = mapped_column(
        JSONB, 
        default=list, 
        server_default="[]",
        comment="群聊白名单群组ID列表"
    )
    
    # 群聊额外配置
    require_mention: Mapped[bool] = mapped_column(
        Boolean, 
        default=True, 
        server_default="true",
        comment="群聊是否需要@提及"
    )
    
    # BlockStreaming 配置
    block_streaming_config: Mapped[dict] = mapped_column(
        JSONB,
        default=lambda: {
            "enabled": True,
            "min_chars": 800,
            "max_chars": 1200,
            "idle_ms": 1000,
            "flush_on_enqueue": True,
            "paragraph_aware": True,
            "chunk_size": 2000,
        },
        comment="BlockStreaming配置"
    )
    
    # Debounce 配置
    debounce_config: Mapped[dict] = mapped_column(
        JSONB,
        default=lambda: {
            "debounce_wait_seconds": 2.0,
            "no_text_debounce": {
                "enabled": True,
                "max_wait_seconds": 3.0,
            },
            "max_batch_size": 10,
            "should_debounce_hook": None,
        },
        comment="Debounce防抖配置"
    )
    
    # 人机延迟配置
    human_like_delay: Mapped[dict] = mapped_column(
        JSONB,
        default=lambda: {
            "enabled": True,
            "min_ms": 500,
            "max_ms": 1500,
        },
        comment="人机延迟配置"
    )
    
    # 应用凭证 (加密存储)
    app_secret: Mapped[str] = mapped_column(
        String(256),
        nullable=False,
        comment="飞书应用密钥 (App Secret)"
    )
    encrypt_key: Mapped[str | None] = mapped_column(
        String(256), 
        nullable=True,
        comment="飞书Encrypt Key (用于Webhook解密)"
    )
    verification_token: Mapped[str | None] = mapped_column(
        String(256), 
        nullable=True,
        comment="飞书Verification Token"
    )
    
    is_active: Mapped[bool] = mapped_column(
        Boolean, 
        default=True, 
        server_default="true",
        comment="是否启用"
    )
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        onupdate=func.now(), 
        comment="更新时间"
    )
    
    # 索引
    __table_args__ = (
        Index("ix_feishu_access_config_app_id", "app_id", unique=True),
        {"schema": Base.__table_args__["schema"]},
    )


class FeishuGroupConfig(Base):
    """飞书群组特定配置表"""
    
    __tablename__ = "feishu_group_config"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    chat_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="飞书群组ID")
    chat_name: Mapped[str | None] = mapped_column(String(128), comment="群组名称")
    
    # 关联的应用配置
    access_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{Base.__table_args__['schema']}.feishu_access_config.id"),
        nullable=False,
        comment="关联的访问配置ID"
    )
    
    # 群组级覆盖配置 (可选)
    override_block_streaming: Mapped[dict | None] = mapped_column(
        JSONB, 
        nullable=True,
        comment="覆盖的BlockStreaming配置"
    )
    override_debounce: Mapped[dict | None] = mapped_column(
        JSONB, 
        nullable=True,
        comment="覆盖的Debounce配置"
    )
    override_human_like_delay: Mapped[dict | None] = mapped_column(
        JSONB, 
        nullable=True,
        comment="覆盖的人机延迟配置"
    )
    
    # 群组特定白名单 (合并到全局白名单)
    extra_allowlist_users: Mapped[list] = mapped_column(
        JSONB, 
        default=list, 
        server_default="[]",
        comment="额外允许的用户员工号"
    )
    
    is_active: Mapped[bool] = mapped_column(
        Boolean, 
        default=True, 
        server_default="true",
        comment="是否启用"
    )
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        onupdate=func.now(), 
        comment="更新时间"
    )
    
    # 关系
    access_config: Mapped["FeishuAccessConfig"] = relationship("FeishuAccessConfig")
    
    # 索引
    __table_args__ = (
        Index("ix_feishu_group_config_chat_id", "chat_id", unique=True),
        {"schema": Base.__table_args__["schema"]},
    )


class FeishuUserBindings(Base):
    """飞书用户绑定表：open_id 与系统用户映射"""
    
    __tablename__ = "feishu_user_bindings"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    
    # 飞书身份信息
    open_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="飞书用户open_id")
    union_id: Mapped[str | None] = mapped_column(String(64), comment="飞书用户union_id")
    employee_no: Mapped[str | None] = mapped_column(String(32), comment="员工工号")
    
    # 关联的系统用户
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{Base.__table_args__['schema']}.users.id"),
        nullable=True,
        comment="关联的系统用户ID"
    )
    
    # 飞书用户信息缓存
    feishu_name: Mapped[str | None] = mapped_column(String(64), comment="飞书用户名")
    feishu_email: Mapped[str | None] = mapped_column(String(128), comment="飞书邮箱")
    feishu_mobile: Mapped[str | None] = mapped_column(String(20), comment="飞书手机号")
    feishu_avatar: Mapped[str | None] = mapped_column(String(512), comment="飞书头像URL")
    
    # 关联的应用
    app_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="飞书应用ID")
    
    # 绑定状态
    is_bound: Mapped[bool] = mapped_column(
        Boolean, 
        default=False, 
        server_default="false",
        comment="是否已绑定系统用户"
    )
    
    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        comment="上次同步时间"
    )
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        onupdate=func.now(), 
        comment="更新时间"
    )
    
    # 关系
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    
    # 索引
    __table_args__ = (
        Index("ix_feishu_user_bindings_open_id_app", "open_id", "app_id", unique=True),
        Index("ix_feishu_user_bindings_employee_no", "employee_no"),
        Index("ix_feishu_user_bindings_user_id", "user_id"),
        {"schema": Base.__table_args__["schema"]},
    )


class FeishuMediaFiles(Base):
    """飞书媒体文件表"""
    
    __tablename__ = "feishu_media_files"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    
    # 文件元数据
    file_key: Mapped[str] = mapped_column(String(256), nullable=False, comment="飞书文件key")
    file_name: Mapped[str] = mapped_column(String(256), nullable=False, comment="文件名")
    file_type: Mapped[str] = mapped_column(
        String(16), 
        nullable=False,
        comment="文件类型: image/file/audio/media/sticker"
    )
    
    # 关联信息
    message_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="关联消息ID")
    open_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="发送者open_id")
    chat_id: Mapped[str | None] = mapped_column(String(64), comment="群组ID")
    
    # 文件信息
    file_size: Mapped[int] = mapped_column(
        Integer, 
        nullable=False, 
        default=0,
        comment="文件大小(字节)"
    )
    mime_type: Mapped[str | None] = mapped_column(String(64), comment="MIME类型")
    sha256_hash: Mapped[str | None] = mapped_column(String(64), comment="SHA256哈希")
    
    # 存储路径
    local_path: Mapped[str] = mapped_column(
        String(512), 
        nullable=False,
        comment="本地存储路径"
    )
    
    # 下载状态
    download_status: Mapped[str] = mapped_column(
        String(16),
        default="pending",
        server_default="'pending'",
        comment="状态: pending/downloading/completed/failed"
    )
    download_retry_count: Mapped[int] = mapped_column(
        Integer, 
        default=0,
        server_default="0",
        comment="下载重试次数"
    )
    download_error: Mapped[str | None] = mapped_column(Text, comment="下载错误信息")
    
    # 是否重复文件
    is_duplicate: Mapped[bool] = mapped_column(
        Boolean, 
        default=False, 
        server_default="false",
        comment="是否重复文件"
    )
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{Base.__table_args__['schema']}.feishu_media_files.id"),
        nullable=True,
        comment="指向原始文件ID"
    )
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        onupdate=func.now(), 
        comment="更新时间"
    )
    
    # 关系
    original_file: Mapped["FeishuMediaFiles"] = relationship(
        "FeishuMediaFiles", 
        remote_side="FeishuMediaFiles.id",
        foreign_keys=[duplicate_of]
    )
    
    # 索引
    __table_args__ = (
        Index("ix_feishu_media_files_file_key", "file_key"),
        Index("ix_feishu_media_files_message_id", "message_id"),
        Index("ix_feishu_media_files_sha256", "sha256_hash"),
        Index("ix_feishu_media_files_open_id", "open_id"),
        {"schema": Base.__table_args__["schema"]},
    )


class FeishuMessageLogs(Base):
    """飞书消息审计日志表"""
    
    __tablename__ = "feishu_message_logs"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    
    # 消息标识
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="飞书事件ID")
    message_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="飞书消息ID")
    
    # 发送者信息
    open_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="发送者open_id")
    employee_no: Mapped[str | None] = mapped_column(String(32), comment="员工工号")
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{Base.__table_args__['schema']}.users.id"),
        nullable=True,
        comment="系统用户ID"
    )
    
    # 会话信息
    chat_id: Mapped[str | None] = mapped_column(String(64), comment="群组ID")
    chat_type: Mapped[str] = mapped_column(
        String(16), 
        nullable=False,
        comment="会话类型: p2p/group"
    )
    
    # 消息内容
    msg_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="消息类型")
    content: Mapped[dict] = mapped_column(JSONB, default=dict, comment="消息内容(JSON)")
    content_text: Mapped[str | None] = mapped_column(Text, comment="消息文本内容")
    
    # 处理状态
    status: Mapped[str] = mapped_column(
        String(16),
        default="received",
        server_default="'received'",
        comment="状态: received/buffering/processing/completed/failed/rejected"
    )
    
    # 处理详情
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        comment="处理开始时间"
    )
    processing_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        comment="处理完成时间"
    )
    processing_duration_ms: Mapped[int | None] = mapped_column(
        Integer,
        comment="处理耗时(毫秒)"
    )
    
    # AI回复信息
    reply_message_id: Mapped[str | None] = mapped_column(String(64), comment="AI回复消息ID")
    reply_content: Mapped[str | None] = mapped_column(Text, comment="AI回复内容")
    reply_card_id: Mapped[str | None] = mapped_column(String(64), comment="流式卡片ID")
    
    # 错误信息
    error_type: Mapped[str | None] = mapped_column(String(32), comment="错误类型")
    error_message: Mapped[str | None] = mapped_column(Text, comment="错误信息")
    
    # ARQ任务信息
    arq_job_id: Mapped[str | None] = mapped_column(String(64), comment="ARQ任务ID")
    
    # 元数据
    extra_metadata: Mapped[dict] = mapped_column(
        JSONB, 
        default=dict, 
        server_default="{}",
        comment="额外元数据"
    )
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        onupdate=func.now(), 
        comment="更新时间"
    )
    
    # 关系
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    
    # 索引
    __table_args__ = (
        Index("ix_feishu_message_logs_event_id", "event_id"),
        Index("ix_feishu_message_logs_message_id", "message_id"),
        Index("ix_feishu_message_logs_open_id", "open_id"),
        Index("ix_feishu_message_logs_chat_id", "chat_id"),
        Index("ix_feishu_message_logs_status", "status"),
        Index("ix_feishu_message_logs_created_at", "created_at"),
        Index("ix_feishu_message_logs_arq_job_id", "arq_job_id"),
        {"schema": Base.__table_args__["schema"]},
    )


class FeishuChatSessionMapping(Base):
    """飞书会话与系统会话映射表"""
    
    __tablename__ = "feishu_chat_session_mapping"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    
    # 飞书会话标识
    chat_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="飞书群组ID")
    open_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="用户open_id")
    
    # 系统会话ID
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="系统会话ID")
    
    # 会话类型
    chat_type: Mapped[str] = mapped_column(
        String(16), 
        nullable=False,
        comment="会话类型: p2p/group"
    )
    
    # 关联的用户
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{Base.__table_args__['schema']}.users.id"),
        nullable=True,
        comment="系统用户ID"
    )
    
    # 最后活跃时间
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(),
        onupdate=func.now(),
        comment="最后活跃时间"
    )
    
    # 消息计数
    message_count: Mapped[int] = mapped_column(
        Integer, 
        default=0,
        server_default="0",
        comment="消息计数"
    )
    
    is_active: Mapped[bool] = mapped_column(
        Boolean, 
        default=True, 
        server_default="true",
        comment="是否活跃"
    )
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        onupdate=func.now(), 
        comment="更新时间"
    )
    
    # 关系
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    
    # 索引
    __table_args__ = (
        Index("ix_feishu_chat_session_mapping_chat_open", "chat_id", "open_id", unique=True),
        Index("ix_feishu_chat_session_mapping_session", "session_id"),
        Index("ix_feishu_chat_session_mapping_user", "user_id"),
        Index("ix_feishu_chat_session_mapping_active", "is_active", "last_active_at"),
        {"schema": Base.__table_args__["schema"]},
    )
