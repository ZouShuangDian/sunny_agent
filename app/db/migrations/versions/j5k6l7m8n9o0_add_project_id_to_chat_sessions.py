"""add project_id to chat_sessions

Revision ID: j5k6l7m8n9o0
Revises: i4j5k6l7m8n9
Create Date: 2026-03-04 17:02:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'j5k6l7m8n9o0'
down_revision: Union[str, None] = 'i4j5k6l7m8n9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add project_id column to chat_sessions
    op.add_column('chat_sessions', 
        sa.Column('project_id', postgresql.UUID(as_uuid=True), nullable=True, comment='关联项目'),
        schema='sunny_agent'
    )
    
    # Create index
    op.create_index('ix_chat_sessions_project', 'chat_sessions', ['project_id'], schema='sunny_agent')
    
    # Add foreign key constraint
    op.create_foreign_key(
        'fk_chat_sessions_project',
        'chat_sessions', 'projects',
        ['project_id'], ['id'],
        source_schema='sunny_agent',
        referent_schema='sunny_agent',
        ondelete='SET NULL'
    )


def downgrade() -> None:
    op.drop_constraint('fk_chat_sessions_project', 'chat_sessions', schema='sunny_agent', type_='foreignkey')
    op.drop_index('ix_chat_sessions_project', 'chat_sessions', schema='sunny_agent')
    op.drop_column('chat_sessions', 'project_id', schema='sunny_agent')
