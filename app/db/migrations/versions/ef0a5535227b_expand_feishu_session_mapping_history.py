"""expand_feishu_session_mapping_history

Revision ID: ef0a5535227b
Revises: e41f29d93bd3
Create Date: 2026-03-19 10:07:07.481200
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ef0a5535227b'
down_revision: Union[str, None] = 'e41f29d93bd3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

schema = 'sunny_agent'


def upgrade() -> None:
    """扩展飞书会话映射表：支持会话归档和历史追溯"""

    # 1. 删除旧的唯一索引（chat_id + open_id）
    op.drop_index(
        'ix_feishu_chat_session_mapping_chat_open',
        table_name='feishu_chat_session_mapping',
        schema=schema,
    )

    # 2. 添加 parent_session_id 字段（归档后关联前一个 session）
    op.add_column(
        'feishu_chat_session_mapping',
        sa.Column('parent_session_id', sa.String(length=64), nullable=True, comment='归档前的 session_id'),
        schema=schema,
    )

    # 3. 添加 archived_at 字段（归档时间）
    op.add_column(
        'feishu_chat_session_mapping',
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True, comment='归档时间'),
        schema=schema,
    )

    # 4. 创建新的复合索引（支持按活跃状态查询）
    op.create_index(
        'ix_feishu_chat_session_mapping_chat_open_active',
        'feishu_chat_session_mapping',
        ['chat_id', 'open_id', 'is_active', 'last_active_at'],
        unique=False,
        schema=schema,
    )


def downgrade() -> None:
    """回滚：恢复旧的唯一索引，删除新增字段"""

    # 4. 删除新索引
    op.drop_index(
        'ix_feishu_chat_session_mapping_chat_open_active',
        table_name='feishu_chat_session_mapping',
        schema=schema,
    )

    # 3. 删除 archived_at
    op.drop_column('feishu_chat_session_mapping', 'archived_at', schema=schema)

    # 2. 删除 parent_session_id
    op.drop_column('feishu_chat_session_mapping', 'parent_session_id', schema=schema)

    # 1. 恢复旧的唯一索引
    op.create_index(
        'ix_feishu_chat_session_mapping_chat_open',
        'feishu_chat_session_mapping',
        ['chat_id', 'open_id'],
        unique=True,
        schema=schema,
    )
