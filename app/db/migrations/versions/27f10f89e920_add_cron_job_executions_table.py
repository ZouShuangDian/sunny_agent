"""add cron_job_executions table

Revision ID: 27f10f89e920
Revises: b2c3d4e5f6a8
Create Date: 2026-03-12 11:14:14.473033
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '27f10f89e920'
down_revision: Union[str, None] = 'b2c3d4e5f6a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('cron_job_executions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('cron_job_id', sa.UUID(), nullable=False, comment='关联的定时任务 ID'),
        sa.Column('usernumb', sa.String(length=20), nullable=False, comment='任务所属用户工号'),
        sa.Column('name', sa.String(length=200), nullable=False, comment='执行时的任务名称（快照）'),
        sa.Column('input_text', sa.Text(), nullable=False, comment='执行时的输入文本（快照）'),
        sa.Column('session_id', sa.String(length=64), nullable=True, comment='执行结果写入的会话 ID'),
        sa.Column('status', sa.String(length=16), nullable=False, comment='执行状态：running / completed / failed / timeout'),
        sa.Column('error_message', sa.Text(), nullable=True, comment='失败原因'),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        schema='sunny_agent'
    )
    op.create_index('ix_cron_exec_job_id', 'cron_job_executions', ['cron_job_id'], unique=False, schema='sunny_agent')
    op.create_index('ix_cron_exec_usernumb_status', 'cron_job_executions', ['usernumb', 'status'], unique=False, schema='sunny_agent')
    op.create_index('ix_cron_exec_completed_at', 'cron_job_executions', ['completed_at'], unique=False, schema='sunny_agent')


def downgrade() -> None:
    op.drop_index('ix_cron_exec_completed_at', table_name='cron_job_executions', schema='sunny_agent')
    op.drop_index('ix_cron_exec_usernumb_status', table_name='cron_job_executions', schema='sunny_agent')
    op.drop_index('ix_cron_exec_job_id', table_name='cron_job_executions', schema='sunny_agent')
    op.drop_table('cron_job_executions', schema='sunny_agent')
