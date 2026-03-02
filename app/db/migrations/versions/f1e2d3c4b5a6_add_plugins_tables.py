"""add plugins and plugin_commands tables

Revision ID: f1e2d3c4b5a6
Revises: a1b2c3d4e5f6
Create Date: 2026-03-01 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f1e2d3c4b5a6'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "sunny_agent"


def upgrade() -> None:
    # 创建 plugins 主表
    op.create_table(
        'plugins',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, comment='主键'),
        sa.Column(
            'name', sa.String(64), nullable=False,
            comment='插件名称（同一用户下唯一，用于命令前缀）'
        ),
        sa.Column('version', sa.String(20), nullable=False, comment='语义化版本号（semver）'),
        sa.Column('description', sa.Text(), nullable=False, comment='插件描述，展示给用户'),
        sa.Column('owner_usernumb', sa.String(20), nullable=False, comment='上传用户工号'),
        sa.Column(
            'path', sa.String(500), nullable=False,
            comment='volume 内相对路径（plugins/{usernumb}/{name}）'
        ),
        sa.Column(
            'is_active', sa.Boolean(), nullable=False, server_default='true',
            comment='admin 下线开关：false 时对所有用户不可见'
        ),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(), comment='创建时间'
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(), comment='更新时间'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('owner_usernumb', 'name', name='uq_plugins_owner_name'),
        schema=SCHEMA,
    )

    # 创建 plugin_commands 表
    op.create_table(
        'plugin_commands',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, comment='主键'),
        sa.Column(
            'plugin_id', postgresql.UUID(as_uuid=True), nullable=False,
            comment='关联 plugins.id'
        ),
        sa.Column('name', sa.String(64), nullable=False, comment='命令名（文件名不含 .md）'),
        sa.Column(
            'description', sa.String(500), nullable=False,
            comment='命令描述（来自 frontmatter description）'
        ),
        sa.Column(
            'argument_hint', sa.String(200), nullable=True,
            comment='参数提示（来自 frontmatter argument-hint）'
        ),
        sa.Column(
            'path', sa.String(500), nullable=False,
            comment='相对于插件根的路径（commands/xxx.md）'
        ),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(), comment='创建时间'
        ),
        sa.ForeignKeyConstraint(
            ['plugin_id'],
            [f'{SCHEMA}.plugins.id'],
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('plugin_id', 'name', name='uq_plugin_commands'),
        schema=SCHEMA,
    )


def downgrade() -> None:
    # 按 FK 依赖顺序：先删 plugin_commands，再删 plugins
    op.drop_table('plugin_commands', schema=SCHEMA)
    op.drop_table('plugins', schema=SCHEMA)
