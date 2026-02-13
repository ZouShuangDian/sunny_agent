"""
审计日志模型：只追加，不更新不删除
审计日志直接存储用户原始输入，无需加密
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.db.models.base import Base


class AuditLog(Base):
    """审计日志表"""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    trace_id: Mapped[str] = mapped_column(String(36), nullable=False, comment="链路追踪ID")
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), comment="用户ID")
    usernumb: Mapped[str | None] = mapped_column(String(32), comment="人员工号（冗余，方便查询）")
    action: Mapped[str] = mapped_column(String(32), nullable=False, comment="操作类型: query/analyze/write/approve")
    risk_level: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="read", comment="风险等级: read/suggest/write/critical"
    )
    route: Mapped[str | None] = mapped_column(String(16), comment="路由: fast_track/deep_engine")
    input_text: Mapped[str | None] = mapped_column(Text, comment="用户原始输入（完整保存）")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="success", comment="状态: success/blocked/error"
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, default=dict, server_default="{}", comment="扩展字段"
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, comment="请求耗时(毫秒)")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, comment="创建时间"
    )
