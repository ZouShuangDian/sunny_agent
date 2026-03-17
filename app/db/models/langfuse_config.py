"""
Langfuse 服务配置模型：单行表（singleton, id=1）
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.config import get_settings
from app.db.models.base import Base

_schema = get_settings().DB_SCHEMA


class LangfuseConfig(Base):
    """Langfuse 配置（单行表，id 固定为 1）"""

    __tablename__ = "langfuse_config"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_langfuse_config_singleton"),
        {"schema": _schema},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    langfuse_host: Mapped[str | None] = mapped_column(String(500))
    langfuse_public_key: Mapped[str | None] = mapped_column(String(200))
    langfuse_secret_key: Mapped[str | None] = mapped_column(
        Text,
        comment="Fernet 加密存储的 Secret Key",
    )
    sample_rate: Mapped[float | None] = mapped_column(Float, default=1.0)
    flush_interval: Mapped[int | None] = mapped_column(Integer, default=5)
    pii_patterns: Mapped[str | None] = mapped_column(Text, default="")
    initialized: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
