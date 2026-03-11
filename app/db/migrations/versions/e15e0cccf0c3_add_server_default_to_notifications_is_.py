"""add server_default to notifications is_read

Revision ID: e15e0cccf0c3
Revises: 467f7f4f551d
Create Date: 2026-03-11 15:25:11.166610
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e15e0cccf0c3'
down_revision: Union[str, None] = '467f7f4f551d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "notifications",
        "is_read",
        server_default=sa.text("false"),
        schema="sunny_agent",
    )


def downgrade() -> None:
    op.alter_column(
        "notifications",
        "is_read",
        server_default=None,
        schema="sunny_agent",
    )
