"""create_user_connectors_and_tools_tables

Revision ID: 653e6aa466c3
Revises: ef0a5535227b
Create Date: 2026-03-19 16:46:40.344388
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = '653e6aa466c3'
down_revision: Union[str, None] = 'ef0a5535227b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

schema = 'sunny_agent'


def upgrade() -> None:
    """创建用户连接器 + 工具表"""

    # 1. user_connectors 表
    op.create_table(
        'user_connectors',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('usernumb', sa.String(32), nullable=False, comment='用户工号'),
        sa.Column('connector_id', sa.String(128), nullable=False, comment='MCP 平台 ID'),
        sa.Column('connector_name', sa.String(128), nullable=False, comment='展示名称'),
        sa.Column('connector_desc', sa.Text, nullable=True, comment='描述'),
        sa.Column('classify', sa.String(64), nullable=True, comment='分类'),
        sa.Column('connector_code', sa.String(128), nullable=True, comment='MCP 平台 code'),
        sa.Column('mcp_url', sa.Text, nullable=False, comment='MCP Server 地址'),
        sa.Column('mcp_env', sa.String(8), server_default='2', comment='环境：1=测试 2=生产'),
        sa.Column('tool_prefix', sa.String(32), nullable=False, comment='工具名前缀'),
        sa.Column('is_enabled', sa.Boolean, server_default='true', comment='总开关'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('usernumb', 'connector_id', 'mcp_url', name='uq_user_connectors'),
        schema=schema,
    )
    op.create_index(
        'ix_user_connectors_usernumb', 'user_connectors',
        ['usernumb'], schema=schema,
    )

    # 2. user_connector_tools 表
    op.create_table(
        'user_connector_tools',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('usernumb', sa.String(32), nullable=False, comment='用户工号'),
        sa.Column('connector_id', sa.String(128), nullable=False, comment='关联 connector_id'),
        sa.Column('tool_name', sa.String(128), nullable=False, comment='工具名称'),
        sa.Column('tool_description', sa.Text, nullable=True, comment='工具描述'),
        sa.Column('tool_schema', JSONB, nullable=True, comment='inputSchema'),
        sa.Column('is_enabled', sa.Boolean, server_default='true', comment='工具级开关'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('usernumb', 'connector_id', 'tool_name', name='uq_user_connector_tools'),
        schema=schema,
    )
    op.create_index(
        'ix_user_connector_tools_lookup', 'user_connector_tools',
        ['usernumb', 'connector_id', 'is_enabled'], schema=schema,
    )


def downgrade() -> None:
    """删除连接器相关表"""
    op.drop_index('ix_user_connector_tools_lookup', 'user_connector_tools', schema=schema)
    op.drop_table('user_connector_tools', schema=schema)
    op.drop_index('ix_user_connectors_usernumb', 'user_connectors', schema=schema)
    op.drop_table('user_connectors', schema=schema)
