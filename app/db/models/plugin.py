"""
Plugin 相关模型：
- Plugin       — 插件主表（用户上传的 Plugin 包）
- PluginCommand — 插件命令表（来自 commands/*.md）

Plugin 与 Skill 系统完全独立：
- Skill 由 LLM 自主调用（model-invoked）
- Plugin Command 由用户显式触发（/{plugin-name}:{command-name}）
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.db.models.base import Base


class Plugin(Base):
    """Plugin 主表（用户上传的 Plugin 包）"""

    __tablename__ = "plugins"
    __table_args__ = (
        UniqueConstraint("owner_usernumb", "name", name="uq_plugins_owner_name"),
        {"schema": Base.__table_args__["schema"]},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    name: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="插件名称（同一用户下唯一，用于命令前缀）"
    )
    version: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="语义化版本号（semver）"
    )
    description: Mapped[str] = mapped_column(
        Text, nullable=False, comment="插件描述，展示给用户"
    )
    owner_usernumb: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="上传用户工号"
    )
    # 相对于 SANDBOX_HOST_VOLUME 的路径
    # 格式：users/{usernumb}/plugins/{name}（不含开头/和末尾/）
    path: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="volume 内相对路径（users/{usernumb}/plugins/{name}）"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true",
        comment="admin 下线开关：false 时对所有用户不可见"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        comment="更新时间"
    )


class PluginCommand(Base):
    """Plugin 命令表（来自 commands/*.md 文件的 frontmatter）"""

    __tablename__ = "plugin_commands"
    __table_args__ = (
        UniqueConstraint("plugin_id", "name", name="uq_plugin_commands"),
        {"schema": Base.__table_args__["schema"]},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    plugin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{Base.__table_args__['schema']}.plugins.id", ondelete="CASCADE"),
        nullable=False,
        comment="关联 plugins.id"
    )
    name: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="命令名（文件名不含 .md）"
    )
    description: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="命令描述（来自 frontmatter description）"
    )
    argument_hint: Mapped[str | None] = mapped_column(
        String(200), nullable=True, comment="参数提示（来自 frontmatter argument-hint）"
    )
    # 相对于插件根目录的路径，如 commands/analyze-xlsx.md
    path: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="相对于插件根的路径（commands/xxx.md）"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
