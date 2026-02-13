"""
SQLAlchemy 声明基类：所有模型继承此 Base
使用 sunny_agent schema 做数据隔离
"""

from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()


class Base(DeclarativeBase):
    """声明基类，统一使用 sunny_agent schema"""

    __abstract__ = True

    # 所有表放在 sunny_agent schema 下
    __table_args__ = {"schema": settings.DB_SCHEMA}
