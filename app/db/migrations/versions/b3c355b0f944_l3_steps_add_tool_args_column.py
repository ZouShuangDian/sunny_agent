"""l3_steps: add tool_args column

Revision ID: b3c355b0f944
Revises: 4be3925bc642
Create Date: 2026-03-09 13:03:32.909236
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b3c355b0f944'
down_revision: Union[str, None] = '4be3925bc642'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'l3_steps',
        sa.Column(
            'tool_args',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment='工具调用入参（assistant 消息专用，{tool_name: args_dict, ...}）',
        ),
        schema='sunny_agent',
    )


def downgrade() -> None:
    op.drop_column('l3_steps', 'tool_args', schema='sunny_agent')
