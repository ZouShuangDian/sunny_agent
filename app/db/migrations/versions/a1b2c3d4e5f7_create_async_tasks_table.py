"""create async_tasks table

Revision ID: a1b2c3d4e5f7
Revises: 338f7fe446e4
Create Date: 2026-03-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f7"
down_revision: Union[str, None] = "338f7fe446e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_schema = "sunny_agent"


def upgrade() -> None:
    op.create_table(
        "async_tasks",
        sa.Column("id", sa.UUID(), nullable=False, comment="主键（同时作为 arq job_id）"),
        sa.Column("usernumb", sa.String(length=20), nullable=False, comment="创建者工号"),
        sa.Column("user_id", sa.String(length=64), nullable=False, comment="创建者用户 UUID"),
        sa.Column("session_id", sa.String(length=64), nullable=False, comment="关联会话 ID"),
        sa.Column("task_type", sa.String(length=32), nullable=False, comment="任务类型：deep_research"),
        sa.Column(
            "status", sa.String(length=16), nullable=False,
            server_default="pending",
            comment="状态机：pending → running → completed | failed | timeout | cancelled",
        ),
        sa.Column("input_text", sa.Text(), nullable=False, comment="Agent 加工后的完整任务描述"),
        sa.Column("result_summary", sa.Text(), nullable=True, comment="执行结果摘要"),
        sa.Column("error_message", sa.Text(), nullable=True, comment="失败原因"),
        sa.Column("metadata", JSONB, nullable=True, comment="扩展字段"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        schema=_schema,
    )
    op.create_index(
        "ix_async_tasks_usernumb_status", "async_tasks",
        ["usernumb", "status"],
        unique=False, schema=_schema,
    )
    op.create_index(
        "ix_async_tasks_session", "async_tasks",
        ["session_id"],
        unique=False, schema=_schema,
    )


def downgrade() -> None:
    op.drop_index("ix_async_tasks_session", table_name="async_tasks", schema=_schema)
    op.drop_index("ix_async_tasks_usernumb_status", table_name="async_tasks", schema=_schema)
    op.drop_table("async_tasks", schema=_schema)
