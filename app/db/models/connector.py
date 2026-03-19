"""
MCP 连接器模型：用户连接器偏好 + 工具列表
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.config import get_settings
from app.db.models.base import Base

_schema = get_settings().DB_SCHEMA


class UserConnector(Base):
    """用户连接器偏好（来自公司 MCP 平台，用户添加后存本地）"""

    __tablename__ = "user_connectors"
    __table_args__ = (
        UniqueConstraint("usernumb", "connector_id", "mcp_url", name="uq_user_connectors"),
        Index("ix_user_connectors_usernumb", "usernumb"),
        {"schema": _schema},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7,
    )
    usernumb: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="用户工号",
    )
    # 来自 MCP 平台（添加时冗余存储）
    connector_id: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="MCP 平台 ID",
    )
    connector_code: Mapped[str | None] = mapped_column(
        String(128), comment="MCP 平台 code",
    )
    connector_name: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="展示名称",
    )
    connector_desc: Mapped[str | None] = mapped_column(
        Text, comment="描述",
    )
    classify: Mapped[str | None] = mapped_column(
        String(64), comment="分类",
    )
    mcp_url: Mapped[str] = mapped_column(
        Text, nullable=False, comment="MCP Server 地址（Streamable HTTP）",
    )
    mcp_env: Mapped[str] = mapped_column(
        String(8), server_default="2", comment="环境：1=测试 2=生产",
    )
    tool_prefix: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="工具名前缀（如 gx_four）",
    )
    # 用户偏好
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, server_default="true", comment="总开关",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )


class UserConnectorTool(Base):
    """用户连接器工具列表 + 工具级开关"""

    __tablename__ = "user_connector_tools"
    __table_args__ = (
        UniqueConstraint("usernumb", "connector_id", "tool_name", name="uq_user_connector_tools"),
        Index("ix_user_connector_tools_lookup", "usernumb", "connector_id", "is_enabled"),
        {"schema": _schema},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid7,
    )
    usernumb: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="用户工号",
    )
    connector_id: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="关联 connector_id",
    )
    tool_name: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="工具名称",
    )
    tool_description: Mapped[str | None] = mapped_column(
        Text, comment="工具描述",
    )
    tool_schema: Mapped[dict | None] = mapped_column(
        JSONB, comment="inputSchema（完整 JSON Schema）",
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, server_default="true", comment="工具级开关",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )
