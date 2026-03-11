"""add expires_at to cron_jobs

Revision ID: 338f7fe446e4
Revises: e15e0cccf0c3
Create Date: 2026-03-11 17:10:09.734871
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '338f7fe446e4'
down_revision: Union[str, None] = 'e15e0cccf0c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cron_jobs",
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="到期日期（可选），到期后 Scanner 自动禁用",
        ),
        schema="sunny_agent",
    )


def downgrade() -> None:
    op.drop_column("cron_jobs", "expires_at", schema="sunny_agent")
