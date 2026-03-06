"""add project counters and triggers

Revision ID: l7m8n9o0p1q2
Revises: k6l7m8n9o0p1
Create Date: 2026-03-05 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'l7m8n9o0p1q2'
down_revision: Union[str, None] = 'k6l7m8n9o0p1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add counter columns to projects table
    op.add_column('projects',
        sa.Column('file_count', sa.Integer(), nullable=False, server_default='0', comment='项目文件数量'),
        schema='sunny_agent'
    )
    op.add_column('projects',
        sa.Column('session_count', sa.Integer(), nullable=False, server_default='0', comment='项目内对话数量'),
        schema='sunny_agent'
    )
    
    # Create function to update counters
    op.execute("""
        CREATE OR REPLACE FUNCTION update_project_counters()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.file_context = 'project' OR NEW.file_context = 'session_in_project' THEN
                    UPDATE sunny_agent.projects 
                    SET file_count = file_count + 1 
                    WHERE id = NEW.project_id;
                END IF;
            ELSIF TG_OP = 'DELETE' THEN
                IF OLD.file_context = 'project' OR OLD.file_context = 'session_in_project' THEN
                    UPDATE sunny_agent.projects 
                    SET file_count = file_count - 1 
                    WHERE id = OLD.project_id;
                END IF;
            END IF;
            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql;
    """)
    
    # Create trigger for file counter
    op.execute("""
        CREATE TRIGGER trg_update_project_file_count
        AFTER INSERT OR DELETE ON sunny_agent.files
        FOR EACH ROW EXECUTE FUNCTION update_project_counters();
    """)
    
    # Create function to update session counter
    op.execute("""
        CREATE OR REPLACE FUNCTION update_project_session_counter()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'INSERT' AND NEW.project_id IS NOT NULL THEN
                UPDATE sunny_agent.projects 
                SET session_count = session_count + 1 
                WHERE id = NEW.project_id;
            ELSIF TG_OP = 'UPDATE' THEN
                IF OLD.project_id IS NOT NULL AND NEW.project_id IS NULL THEN
                    UPDATE sunny_agent.projects 
                    SET session_count = session_count - 1 
                    WHERE id = OLD.project_id;
                ELSIF OLD.project_id IS NULL AND NEW.project_id IS NOT NULL THEN
                    UPDATE sunny_agent.projects 
                    SET session_count = session_count + 1 
                    WHERE id = NEW.project_id;
                END IF;
            ELSIF TG_OP = 'DELETE' AND OLD.project_id IS NOT NULL THEN
                UPDATE sunny_agent.projects 
                SET session_count = session_count - 1 
                WHERE id = OLD.project_id;
            END IF;
            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql;
    """)
    
    # Create trigger for session counter
    op.execute("""
        CREATE TRIGGER trg_update_project_session_count
        AFTER INSERT OR UPDATE OR DELETE ON sunny_agent.chat_sessions
        FOR EACH ROW EXECUTE FUNCTION update_project_session_counter();
    """)


def downgrade() -> None:
    # Drop triggers
    op.execute("DROP TRIGGER IF EXISTS trg_update_project_session_count ON sunny_agent.chat_sessions")
    op.execute("DROP TRIGGER IF EXISTS trg_update_project_file_count ON sunny_agent.files")
    
    # Drop functions
    op.execute("DROP FUNCTION IF EXISTS update_project_session_counter()")
    op.execute("DROP FUNCTION IF EXISTS update_project_counters()")
    
    # Drop columns
    op.drop_column('projects', 'session_count', schema='sunny_agent')
    op.drop_column('projects', 'file_count', schema='sunny_agent')
