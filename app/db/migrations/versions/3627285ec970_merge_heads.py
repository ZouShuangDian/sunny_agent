"""merge heads

Revision ID: 3627285ec970
Revises: c4d5e6f7a8b9, m8n9o0p1q2r3
Create Date: 2026-03-10 21:10:11.970407
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3627285ec970'
down_revision: Union[str, None] = ('c4d5e6f7a8b9', 'm8n9o0p1q2r3')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
