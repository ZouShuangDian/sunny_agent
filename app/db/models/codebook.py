"""
码表模型：业务术语标准化
入库时 alias 字段存储归一化后的值，alias_display 保留原始形式
"""

import re
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.db.models.base import Base


def normalize_alias(raw: str) -> str:
    """
    码表别名归一化：统一小写 + 去除分隔符 + 去除首尾空格
    "A-100" → "a100"
    " A 100 " → "a100"
    "A_100" → "a100"
    """
    s = raw.strip().lower()
    s = re.sub(r"[\s\-_]+", "", s)
    return s


class Codebook(Base):
    """码表：alias 存归一化值，alias_display 存原始展示名"""

    __tablename__ = "codebook"
    __table_args__ = (
        UniqueConstraint("alias", "entity_type", name="uq_codebook_alias_type"),
        {"schema": Base.__table_args__["schema"]},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    alias: Mapped[str] = mapped_column(String(128), nullable=False, comment="归一化别名，如 a100")
    alias_display: Mapped[str] = mapped_column(String(128), nullable=False, comment="原始展示名，如 A-100")
    standard_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="标准名称")
    entity_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="实体类型: product/line/metric/department"
    )
    entity_meta: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}", comment="扩展属性")
    status: Mapped[str] = mapped_column(
        String(16), server_default="active", comment="状态: active/candidate/deprecated"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )
