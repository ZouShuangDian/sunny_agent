"""add task_id to notifications

Revision ID: b2c3d4e5f6a8
Revises: a1b2c3d4e5f7
Create Date: 2026-03-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a8"
down_revision: Union[str, None] = "a1b2c3d4e5f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_schema = "sunny_agent"


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("task_id", sa.String(length=64), nullable=True, comment="关联的异步任务 ID"),
        schema=_schema,
    )


def downgrade() -> None:
    op.drop_column("notifications", "task_id", schema=_schema)
