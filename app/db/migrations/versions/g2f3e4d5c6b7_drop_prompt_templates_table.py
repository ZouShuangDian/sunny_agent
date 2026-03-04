"""drop prompt_templates table

L1 路由删除重构：prompt_templates 表不再需要，整表删除。
历史数据不做迁移（无消费者）。

Revision ID: g2f3e4d5c6b7
Revises: f1e2d3c4b5a6
Create Date: 2026-03-04 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'g2f3e4d5c6b7'
down_revision: Union[str, None] = '9a8b7c6d5e4f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table('prompt_templates', schema='sunny_agent')


def downgrade() -> None:
    # 回滚时重建表结构（数据不可恢复）
    op.create_table(
        'prompt_templates',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('tag', sa.String(50), nullable=False),
        sa.Column('tier', sa.String(10), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('system_prompt', sa.Text(), nullable=False),
        sa.Column('match_keywords', sa.ARRAY(sa.String()), nullable=True),
        sa.Column('is_default', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tag', 'tier', name='uq_prompt_templates_tag_tier'),
        schema='sunny_agent',
    )
