"""drop_connector_code_column

Revision ID: 6b7506a396a9
Revises: 653e6aa466c3
Create Date: 2026-03-20 10:42:05.984817
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '6b7506a396a9'
down_revision: Union[str, None] = '653e6aa466c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

schema = 'sunny_agent'


def upgrade() -> None:
    op.drop_column('user_connectors', 'connector_code', schema=schema)


def downgrade() -> None:
    op.add_column(
        'user_connectors',
        sa.Column('connector_code', sa.String(128), nullable=True, comment='MCP 平台 code'),
        schema=schema,
    )
