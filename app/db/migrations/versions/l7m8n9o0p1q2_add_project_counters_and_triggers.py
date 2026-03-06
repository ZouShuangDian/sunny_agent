"""add project counters and triggers

Revision ID: l7m8n9o0p1q2
Revises: k6l7m8n9o0p1
Create Date: 2026-03-05 18:00:00.000000

Note: 计数器由应用层代码手动维护，不使用数据库触发器
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'l7m8n9o0p1q2'
down_revision: Union[str, None] = 'k6l7m8n9o0p1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('projects',
        sa.Column('file_count', sa.Integer(), nullable=False, server_default='0', comment='项目文件数量'),
        schema='sunny_agent'
    )
    op.add_column('projects',
        sa.Column('session_count', sa.Integer(), nullable=False, server_default='0', comment='项目内对话数量'),
        schema='sunny_agent'
    )


def downgrade() -> None:
    op.drop_column('projects', 'session_count', schema='sunny_agent')
    op.drop_column('projects', 'file_count', schema='sunny_agent')
