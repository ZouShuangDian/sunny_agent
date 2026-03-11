"""merge heads: source column and has_scripts

Revision ID: 8d2810dc757a
Revises: m8n9o0p1q2r3, c4d5e6f7a8b9
Create Date: 2026-03-11 09:26:31.712216
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8d2810dc757a'
down_revision: Union[str, None] = ('m8n9o0p1q2r3', 'c4d5e6f7a8b9')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
