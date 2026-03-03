"""add compaction support: is_compaction column + l3_steps table

Revision ID: b2c3d4e5f6a7
Revises: f1e2d3c4b5a6
Create Date: 2026-03-03 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'f1e2d3c4b5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "sunny_agent"


def upgrade() -> None:
    # 1. chat_messages 新增 is_compaction 字段（Level 2 摘要 genesis block 标记）
    op.add_column(
        'chat_messages',
        sa.Column(
            'is_compaction',
            sa.Boolean(),
            nullable=False,
            server_default='false',
            comment='是否为 Level 2 摘要节点（genesis block），加载历史时遇到此标记即停止往前读取',
        ),
        schema=SCHEMA,
    )

    # 2. 新建 l3_steps 表（存储 L3 ReAct 中间步骤，支持 Level 1 剪枝）
    op.create_table(
        'l3_steps',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, comment='主键'),
        sa.Column('session_id', sa.String(64), nullable=False, comment='所属会话 ID'),
        sa.Column(
            'message_id', sa.String(64), nullable=True,
            comment='关联的 assistant 最终消息 ID（chat_messages.message_id）',
        ),
        sa.Column('step_index', sa.Integer(), nullable=False, comment='步骤序号（0-based，同一 message_id 内）'),
        sa.Column('role', sa.String(20), nullable=False, comment="角色: 'assistant' | 'tool'"),
        sa.Column('content', sa.Text(), nullable=False, server_default='', comment='消息内容'),
        sa.Column(
            'tool_name', sa.String(100), nullable=True,
            comment='工具名称（tool 消息专用）',
        ),
        sa.Column(
            'tool_call_id', sa.String(100), nullable=True,
            comment='工具调用 ID（tool 消息专用，用于关联 assistant tool_calls）',
        ),
        sa.Column(
            'compacted', sa.Boolean(), nullable=False, server_default='false',
            comment='Level 1 剪枝标记：True 表示内容已被替换为占位符',
        ),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(), comment='创建时间',
        ),
        sa.PrimaryKeyConstraint('id'),
        schema=SCHEMA,
    )

    # 3. 创建索引（task 1.4）
    op.create_index(
        'ix_l3_steps_session_created',
        'l3_steps',
        ['session_id', 'created_at'],
        schema=SCHEMA,
    )
    op.create_index(
        'ix_l3_steps_session_compacted',
        'l3_steps',
        ['session_id', 'compacted'],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index('ix_l3_steps_session_compacted', table_name='l3_steps', schema=SCHEMA)
    op.drop_index('ix_l3_steps_session_created', table_name='l3_steps', schema=SCHEMA)
    op.drop_table('l3_steps', schema=SCHEMA)
    op.drop_column('chat_messages', 'is_compaction', schema=SCHEMA)
