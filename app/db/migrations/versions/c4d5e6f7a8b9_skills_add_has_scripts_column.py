"""skills: add has_scripts column

Revision ID: c4d5e6f7a8b9
Revises: b3c355b0f944
Create Date: 2026-03-09 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, None] = 'b3c355b0f944'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'skills',
        sa.Column('has_scripts', sa.Boolean, server_default='false', nullable=False),
        schema='sunny_agent',
    )


def downgrade() -> None:
    op.drop_column('skills', 'has_scripts', schema='sunny_agent')
