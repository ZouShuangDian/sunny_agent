"""drop codebook table

Revision ID: k6l7m8n9o0p1
Revises: j5k6l7m8n9o0
Create Date: 2026-03-04 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'k6l7m8n9o0p1'
down_revision: Union[str, None] = 'j5k6l7m8n9o0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS sunny_agent.codebook")


def downgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS sunny_agent.codebook (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            alias VARCHAR(128) NOT NULL,
            alias_display VARCHAR(128) NOT NULL,
            standard_name VARCHAR(128) NOT NULL,
            entity_type VARCHAR(32) NOT NULL,
            entity_meta JSONB DEFAULT '{}'::jsonb,
            status VARCHAR(16) DEFAULT 'active',
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT uq_codebook_alias_type UNIQUE (alias, entity_type)
        )
    """)
