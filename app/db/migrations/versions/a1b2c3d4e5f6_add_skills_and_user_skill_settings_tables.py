"""add skills and user_skill_settings tables

Revision ID: a1b2c3d4e5f6
Revises: 5582a6f57d74
Create Date: 2026-02-28 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '5582a6f57d74'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "sunny_agent"


def upgrade() -> None:
    # 创建 skills 主表
    op.create_table(
        'skills',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, comment='主键'),
        sa.Column('name', sa.String(64), nullable=False, comment='Skill 名称（全局唯一，对应 skill_call 参数）'),
        sa.Column('description', sa.Text(), nullable=False, comment='Skill 描述，展示给 LLM 的 skill_call catalog'),
        sa.Column(
            'path', sa.String(500), nullable=False,
            comment='volume 内相对路径（不含开头/和末尾/），例：skills/github'
        ),
        sa.Column(
            'scope', sa.String(10), nullable=False,
            comment='skill 归属范围：system / user'
        ),
        sa.Column(
            'owner_usernumb', sa.String(20), nullable=True,
            comment='user skill 的创建者工号；system skill 为 NULL'
        ),
        sa.Column(
            'is_active', sa.Boolean(), nullable=False, server_default='true',
            comment='admin 下线开关：false 时对所有用户不可见，优先级最高'
        ),
        sa.Column(
            'is_default_enabled', sa.Boolean(), nullable=False, server_default='false',
            comment='system skill 的默认状态：true=开箱即用，false=需用户主动订阅'
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment='更新时间'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_skills_name'),
        schema=SCHEMA,
    )

    # 创建 user_skill_settings 用户个人开关表
    op.create_table(
        'user_skill_settings',
        sa.Column('usernumb', sa.String(20), nullable=False, comment='用户工号'),
        sa.Column('skill_id', postgresql.UUID(as_uuid=True), nullable=False, comment='关联 skills.id'),
        sa.Column('is_enabled', sa.Boolean(), nullable=False, comment='用户个人开关（true=开启，false=关闭）'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment='更新时间'),
        sa.ForeignKeyConstraint(
            ['skill_id'],
            [f'{SCHEMA}.skills.id'],
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('usernumb', 'skill_id'),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table('user_skill_settings', schema=SCHEMA)
    op.drop_table('skills', schema=SCHEMA)
