"""create projects table

Revision ID: i4j5k6l7m8n9
Revises: h3i4j5k6l7m8
Create Date: 2026-03-04 17:01:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'i4j5k6l7m8n9'
down_revision: Union[str, None] = 'h3i4j5k6l7m8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create projects table
    op.create_table(
        'projects',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False, comment='Project name'),
        sa.Column('owner_id', postgresql.UUID(as_uuid=True), nullable=False, comment='Project owner user ID'),
        sa.Column('company', sa.String(length=255), nullable=True, comment='Company for data isolation'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment='Creation timestamp'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment='Last update timestamp'),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['owner_id'], ['sunny_agent.users.id'], ),
        sa.UniqueConstraint('owner_id', 'name', name='uq_projects_owner_name'),
        schema='sunny_agent'
    )
    
    # Create indexes
    op.create_index('ix_projects_owner', 'projects', ['owner_id'], schema='sunny_agent')
    op.create_index('ix_projects_company', 'projects', ['company'], schema='sunny_agent')
    op.create_index('ix_projects_updated', 'projects', [sa.desc('updated_at')], schema='sunny_agent')


def downgrade() -> None:
    op.drop_index('ix_projects_updated', 'projects', schema='sunny_agent')
    op.drop_index('ix_projects_company', 'projects', schema='sunny_agent')
    op.drop_index('ix_projects_owner', 'projects', schema='sunny_agent')
    op.drop_table('projects', schema='sunny_agent')
