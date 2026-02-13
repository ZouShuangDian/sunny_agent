"""
Prompt 模板模型：L1/L3 执行层使用

模板存储在 PG 中（权威数据源），通过同步脚本推送到 Milvus 做向量检索。
match_text 字段用于生成 embedding，应包含意图描述 + 示例查询，
确保语义检索时能高准确率匹配到用户的真实意图。
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.db.models.base import Base


class PromptTemplate(Base):
    """Prompt 模板表"""

    __tablename__ = "prompt_templates"
    __table_args__ = (
        UniqueConstraint("name", "version", "tenant_id", name="uq_template_name_ver_tenant"),
        {"schema": Base.__table_args__["schema"]},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="模板名称")
    tier: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="L1", comment="执行层级: L1 / L3"
    )
    intent_tags: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]",
        comment="关联意图标签，如 [\"writing\", \"summarize\"]",
    )
    match_text: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="",
        comment="用于 embedding 匹配的文本（意图描述 + 示例查询）",
    )
    template: Mapped[str] = mapped_column(Text, nullable=False, comment="Prompt 正文")
    description: Mapped[str] = mapped_column(
        String(256), nullable=False, server_default="", comment="模板描述（人类可读）"
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", comment="是否为默认 Prompt"
    )
    version: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="1.0.0", comment="版本号"
    )
    tenant_id: Mapped[str] = mapped_column(
        String(64), server_default="default", comment="租户ID"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", comment="是否启用"
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, server_default="0", comment="排序权重（越大优先级越高）"
    )
    metrics: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        server_default='{"usage_count": 0, "avg_accuracy": 0, "avg_rt_ms": 0}',
        comment="使用指标",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )
