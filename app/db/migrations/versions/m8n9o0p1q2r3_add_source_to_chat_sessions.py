"""add source column to chat_sessions

Revision ID: m8n9o0p1q2r3
Revises: l7m8n9o0p1q2
Create Date: 2026-03-10 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.config import get_settings

settings = get_settings()
_schema = settings.DB_SCHEMA

# revision identifiers, used by Alembic.
revision: str = 'm8n9o0p1q2r3'
down_revision: Union[str, None] = 'l7m8n9o0p1q2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'chat_sessions',
        sa.Column(
            'source',
            sa.String(20),
            nullable=False,
            server_default='chat',
            comment='来源: chat / async_task / cron',
        ),
        schema=_schema,
    )


def downgrade() -> None:
    op.drop_column('chat_sessions', 'source', schema=_schema)
