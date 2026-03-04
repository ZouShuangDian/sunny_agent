"""create files table

Revision ID: h3i4j5k6l7m8
Revises: g2f3e4d5c6b7
Create Date: 2026-03-04 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'h3i4j5k6l7m8'
down_revision: Union[str, None] = 'g2f3e4d5c6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create files table
    op.create_table(
        'files',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('file_name', sa.String(length=255), nullable=False, comment='Original filename with extension'),
        sa.Column('file_path', sa.String(length=1024), nullable=False, comment='Relative path'),
        sa.Column('file_size', sa.BigInteger(), nullable=False, comment='File size in bytes'),
        sa.Column('mime_type', sa.String(length=255), nullable=False, comment='MIME type'),
        sa.Column('file_extension', sa.String(length=50), nullable=False, comment='File extension (lowercase)'),
        sa.Column('storage_filename', sa.String(length=255), nullable=False, comment='Storage filename with UUID'),
        sa.Column('file_hash', sa.String(length=64), nullable=True, comment='SHA256 hash'),
        sa.Column('description', sa.String(length=500), nullable=True, comment='File description (max 500 chars)'),
        sa.Column('tags', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='Tags array (JSONB)'),
        sa.Column('uploaded_by', postgresql.UUID(as_uuid=True), nullable=False, comment='Uploader user ID'),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment='Upload timestamp'),
        sa.Column('session_id', sa.String(length=100), nullable=True, comment='Associated session ID'),
        sa.Column('project_id', postgresql.UUID(as_uuid=True), nullable=True, comment='Associated project ID'),
        sa.Column('file_context', sa.String(length=20), nullable=False, comment='Context: session or project'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['uploaded_by'], ['sunny_agent.users.id'], ),
        schema='sunny_agent'
    )
    
    # Create indexes
    op.create_index('ix_files_session', 'files', ['session_id'], schema='sunny_agent')
    op.create_index('ix_files_project', 'files', ['project_id'], schema='sunny_agent')
    op.create_index('ix_files_hash', 'files', ['file_hash'], schema='sunny_agent')
    op.create_index('ix_files_uploaded_by', 'files', ['uploaded_by'], schema='sunny_agent')
    op.create_index('ix_files_context', 'files', ['file_context'], schema='sunny_agent')


def downgrade() -> None:
    op.drop_index('ix_files_context', 'files', schema='sunny_agent')
    op.drop_index('ix_files_uploaded_by', 'files', schema='sunny_agent')
    op.drop_index('ix_files_hash', 'files', schema='sunny_agent')
    op.drop_index('ix_files_project', 'files', schema='sunny_agent')
    op.drop_index('ix_files_session', 'files', schema='sunny_agent')
    op.drop_table('files', schema='sunny_agent')
