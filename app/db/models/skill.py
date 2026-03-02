"""
Skill 相关模型：
- Skill       — skill 主表（系统 skill + 用户私有 skill）
- UserSkillSetting — 用户个人开关（仅记录显式操作，未操作则回退到 is_default_enabled）
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.db.models.base import Base


class Skill(Base):
    """Skill 主表"""

    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("name", name="uq_skills_name"),
        {"schema": Base.__table_args__["schema"]},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7
    )
    name: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="Skill 名称（全局唯一，对应 skill_call 参数）"
    )
    description: Mapped[str] = mapped_column(
        Text, nullable=False, comment="Skill 描述，展示给 LLM 的 skill_call catalog"
    )
    # 相对于 SANDBOX_HOST_VOLUME 的路径
    # 不含开头 /，不含末尾 /，不含 /SKILL.md，仅到目录层级
    # 示例：skills/github  或  skills/users/1131618/my_skill
    path: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="volume 内相对路径（不含开头/和末尾/）"
    )
    scope: Mapped[str] = mapped_column(
        String(10), nullable=False, comment="skill 归属范围：system / user"
    )
    owner_usernumb: Mapped[str | None] = mapped_column(
        String(20), nullable=True, comment="user skill 的创建者工号；system skill 为 NULL"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true",
        comment="admin 下线开关：false 时对所有用户不可见，优先级最高"
    )
    is_default_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false",
        comment="system skill 的默认状态：true=开箱即用，false=需用户主动订阅"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        comment="更新时间"
    )


class UserSkillSetting(Base):
    """用户个人 Skill 开关（仅存储显式操作记录）"""

    __tablename__ = "user_skill_settings"
    __table_args__ = {"schema": Base.__table_args__["schema"]}

    usernumb: Mapped[str] = mapped_column(
        String(20), primary_key=True, comment="用户工号"
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{Base.__table_args__['schema']}.skills.id", ondelete="CASCADE"),
        primary_key=True,
        comment="关联 skills.id"
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, comment="用户个人开关（true=开启，false=关闭）"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        comment="更新时间"
    )
