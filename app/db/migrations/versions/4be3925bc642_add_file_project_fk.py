"""add_file_project_fk

Revision ID: 4be3925bc642
Revises: l7m8n9o0p1q2
Create Date: 2026-03-06 08:33:47.998022
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4be3925bc642'
down_revision: Union[str, None] = 'l7m8n9o0p1q2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add foreign key constraint on files.project_id -> projects.id
    op.create_foreign_key(
        'fk_files_project',
        'files', 'projects',
        ['project_id'], ['id'],
        source_schema='sunny_agent',
        referent_schema='sunny_agent',
        ondelete='SET NULL'
    )


def downgrade() -> None:
    # Drop foreign key constraint
    op.drop_constraint('fk_files_project', 'files', schema='sunny_agent', type_='foreignkey')
