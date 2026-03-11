"""create_notifications_table

Revision ID: 467f7f4f551d
Revises: ec049df7b462
Create Date: 2026-03-11 15:02:24.907839
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '467f7f4f551d'
down_revision: Union[str, None] = 'ec049df7b462'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('notifications',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('usernumb', sa.String(length=20), nullable=False, comment='接收者工号'),
        sa.Column('type', sa.String(length=32), nullable=False, comment='通知类型：cron_completed / cron_failed'),
        sa.Column('title', sa.String(length=200), nullable=False, comment='通知标题'),
        sa.Column('content', sa.Text(), nullable=True, comment='通知详情（可选）'),
        sa.Column('session_id', sa.String(length=64), nullable=True, comment='关联的会话 ID（点击通知跳转用）'),
        sa.Column('cron_job_id', sa.String(length=64), nullable=True, comment='关联的定时任务 ID（str(UUID)）'),
        sa.Column('is_read', sa.Boolean(), nullable=False, comment='是否已读'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        schema='sunny_agent'
    )
    op.create_index(
        'ix_notifications_user_unread', 'notifications',
        ['usernumb', 'is_read', 'created_at'],
        unique=False, schema='sunny_agent',
    )


def downgrade() -> None:
    op.drop_index('ix_notifications_user_unread', table_name='notifications', schema='sunny_agent')
    op.drop_table('notifications', schema='sunny_agent')
