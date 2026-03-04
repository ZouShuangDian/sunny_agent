"""
数据隔离策略模型
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7 as uuid7_func

from app.config import get_settings
from app.db.models.base import Base

settings = get_settings()
_schema = settings.DB_SCHEMA


class DataScopePolicy(Base):
    """数据隔离策略表"""
    
    __tablename__ = "data_scope_policies"
    
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), 
        primary_key=True, 
        default=uuid7_func
    )
    
    company: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="公司名"
    )
    
    resource_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="资源类型"
    )
    
    isolation_level: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="strict",
        comment="隔离级别：strict(完全隔离) | shared(只读共享)"
    )
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment="创建时间"
    )
