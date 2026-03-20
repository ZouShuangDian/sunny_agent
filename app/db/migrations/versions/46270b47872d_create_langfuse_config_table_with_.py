"""create langfuse_config table with enabled column

Revision ID: 46270b47872d
Revises: 27f10f89e920
Create Date: 2026-03-16 15:27:13.690502
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '46270b47872d'
down_revision: Union[str, None] = '27f10f89e920'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('langfuse_config',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('langfuse_host', sa.String(length=500), nullable=True),
        sa.Column('langfuse_public_key', sa.String(length=200), nullable=True),
        sa.Column('langfuse_secret_key', sa.Text(), nullable=True, comment='Fernet 加密存储的 Secret Key'),
        sa.Column('sample_rate', sa.Float(), nullable=True),
        sa.Column('flush_interval', sa.Integer(), nullable=True),
        sa.Column('pii_patterns', sa.Text(), nullable=True),
        sa.Column('initialized', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('id = 1', name='ck_langfuse_config_singleton'),
        sa.PrimaryKeyConstraint('id'),
        schema='sunny_agent'
    )


def downgrade() -> None:
    op.drop_table('langfuse_config', schema='sunny_agent')
