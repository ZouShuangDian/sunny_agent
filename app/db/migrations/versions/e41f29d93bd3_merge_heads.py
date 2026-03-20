"""merge_heads

Revision ID: e41f29d93bd3
Revises: 46270b47872d, acd7448d2342
Create Date: 2026-03-19 10:06:42.931915
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e41f29d93bd3'
down_revision: Union[str, None] = ('46270b47872d', 'acd7448d2342')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
