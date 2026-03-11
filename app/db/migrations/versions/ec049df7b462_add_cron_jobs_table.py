"""add cron_jobs table

Revision ID: ec049df7b462
Revises: 3627285ec970
Create Date: 2026-03-10 21:11:00.385555
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ec049df7b462'
down_revision: Union[str, None] = '3627285ec970'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('cron_jobs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('usernumb', sa.String(length=20), nullable=False, comment='创建者工号'),
    sa.Column('name', sa.String(length=200), nullable=False, comment='任务名称'),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('cron_expr', sa.String(length=100), nullable=False, comment='标准 5 字段 Cron 表达式：分 时 日 月 周'),
    sa.Column('timezone', sa.String(length=50), nullable=False),
    sa.Column('input_text', sa.Text(), nullable=False, comment='投喂给 Agent 的用户消息（等同于 /chat 的 message）'),
    sa.Column('session_id', sa.String(length=64), nullable=True, comment='结果推送到哪个会话（null 则每次创建新会话）'),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=False, comment='下次触发时间（Scanner 按此查询，原子 UPDATE 推进）'),
    sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_status', sa.String(length=16), nullable=True, comment='上次执行结果：completed / failed / timeout'),
    sa.Column('last_error', sa.Text(), nullable=True, comment='上次失败原因'),
    sa.Column('run_count', sa.Integer(), nullable=False, comment='累计执行次数'),
    sa.Column('fail_count', sa.Integer(), nullable=False, comment='累计失败次数'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    schema='sunny_agent'
    )
    op.create_index('ix_cron_jobs_next_run', 'cron_jobs', ['enabled', 'next_run_at'], unique=False, schema='sunny_agent')


def downgrade() -> None:
    op.drop_index('ix_cron_jobs_next_run', table_name='cron_jobs', schema='sunny_agent')
    op.drop_table('cron_jobs', schema='sunny_agent')
