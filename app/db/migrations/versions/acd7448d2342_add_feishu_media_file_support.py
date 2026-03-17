"""add_feishu_media_file_support

Revision ID: acd7448d2342
Revises: n9o0p1q2r3s4
Create Date: 2026-03-17 18:11:16.659864
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'acd7448d2342'
down_revision: Union[str, None] = 'n9o0p1q2r3s4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

schema = 'sunny_agent'


def upgrade() -> None:
    """升级：添加飞书媒体文件支持"""

    # 1. FeishuAccessConfig 表添加 app_name 字段
    op.add_column(
        'feishu_access_config',
        sa.Column('app_name', sa.String(length=128), nullable=True, comment='飞书应用名称（机器人名称）'),
        schema=schema,
    )

    # 2. File 表添加飞书相关字段
    op.add_column(
        'files',
        sa.Column('feishu_app_id', sa.String(length=64), nullable=True, comment='飞书应用 ID'),
        schema=schema,
    )
    op.add_column(
        'files',
        sa.Column('feishu_message_id', sa.String(length=64), nullable=True, comment='飞书消息 ID'),
        schema=schema,
    )
    op.add_column(
        'files',
        sa.Column('feishu_file_key', sa.String(length=256), nullable=True, comment='飞书文件 key'),
        schema=schema,
    )
    op.add_column(
        'files',
        sa.Column('feishu_chat_type', sa.String(length=16), nullable=True, comment='聊天类型：p2p/group'),
        schema=schema,
    )

    # 3. 修改 file_context 字段长度
    op.alter_column(
        'files',
        'file_context',
        existing_type=sa.String(20),
        type_=sa.String(32),
        existing_nullable=False,
        existing_server_default='project',
        schema=schema,
    )

    # 4. FeishuMediaFiles 表添加 file_id 外键字段
    op.add_column(
        'feishu_media_files',
        sa.Column('file_id', sa.UUID(), nullable=True, comment='关联的 File 表 ID'),
        schema=schema,
    )

    # 5. 创建索引
    op.create_index(
        'ix_files_feishu_message', 'files',
        ['feishu_message_id'], schema=schema,
    )
    op.create_index(
        'ix_files_feishu_app_chat', 'files',
        ['feishu_app_id', 'feishu_chat_type', 'feishu_message_id'], schema=schema,
    )
    op.create_index(
        'ix_feishu_media_files_file', 'feishu_media_files',
        ['file_id'], schema=schema,
    )

    # 6. 添加外键约束
    op.create_foreign_key(
        'fk_feishu_media_files_file',
        'feishu_media_files', 'files',
        ['file_id'], ['id'],
        source_schema=schema, referent_schema=schema,
        ondelete='SET NULL',
    )


def downgrade() -> None:
    """降级：移除飞书媒体文件支持"""

    # 6. 删除外键约束
    op.drop_constraint('fk_feishu_media_files_file', 'feishu_media_files', schema=schema, type_='foreignkey')

    # 5. 删除索引
    op.drop_index('ix_feishu_media_files_file', 'feishu_media_files', schema=schema)
    op.drop_index('ix_files_feishu_app_chat', 'files', schema=schema)
    op.drop_index('ix_files_feishu_message', 'files', schema=schema)

    # 4. 删除 feishu_media_files.file_id
    op.drop_column('feishu_media_files', 'file_id', schema=schema)

    # 3. 还原 file_context 字段长度
    op.alter_column(
        'files', 'file_context',
        existing_type=sa.String(32),
        type_=sa.String(20),
        existing_nullable=False,
        existing_server_default='project',
        schema=schema,
    )

    # 2. 删除 files 飞书字段
    op.drop_column('files', 'feishu_chat_type', schema=schema)
    op.drop_column('files', 'feishu_file_key', schema=schema)
    op.drop_column('files', 'feishu_message_id', schema=schema)
    op.drop_column('files', 'feishu_app_id', schema=schema)

    # 1. 删除 feishu_access_config.app_name
    op.drop_column('feishu_access_config', 'app_name', schema=schema)
