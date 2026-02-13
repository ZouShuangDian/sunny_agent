"""
用户与角色模型
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid6 import uuid7

from app.config import get_settings
from app.db.models.base import Base

settings = get_settings()
_schema = settings.DB_SCHEMA


class Role(Base):
    """角色表：admin / manager / operator / viewer"""

    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, comment="角色名")
    permissions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, comment="权限列表")
    description: Mapped[str | None] = mapped_column(Text, comment="角色描述")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )

    # 反向关联
    users: Mapped[list["User"]] = relationship(back_populates="role")


class User(Base):
    """用户表"""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    username: Mapped[str] = mapped_column(String(64), nullable=False, comment="姓名")
    usernumb: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, comment="人员工号")
    email: Mapped[str | None] = mapped_column(String(128), comment="邮箱")
    hashed_pwd: Mapped[str] = mapped_column(String(256), nullable=False, comment="密码哈希")
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{_schema}.roles.id"),
        nullable=False,
        comment="角色ID",
    )
    department: Mapped[str | None] = mapped_column(String(64), comment="部门")
    data_scope: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}", comment="数据权限范围"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", comment="是否激活")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )

    # 正向关联
    role: Mapped["Role"] = relationship(back_populates="users", lazy="joined")
