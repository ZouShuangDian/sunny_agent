"""
添加飞书集成相关表

Revision ID: f8e9d0c1b2a3
Revises: 
Create Date: 2025-03-13 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import uuid

# revision identifiers, used by Alembic.
revision = 'f8e9d0c1b2a3'
down_revision = '27f10f89e920'
branch_labels = None
depends_on = None


def upgrade():
    schema = 'sunny_agent'

    # 1. 创建 feishu_access_config 表
    op.create_table(
        'feishu_access_config',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column('app_id', sa.String(64), nullable=False, comment='飞书应用ID'),
        sa.Column('dm_policy', sa.String(16), nullable=False, server_default='open', comment='私信策略: open/allowlist/disabled'),
        sa.Column('group_policy', sa.String(16), nullable=False, server_default='open', comment='群聊策略: open/allowlist/disabled'),
        sa.Column('dm_allowlist', postgresql.JSONB, server_default='[]', comment='私信白名单员工号列表'),
        sa.Column('group_allowlist', postgresql.JSONB, server_default='[]', comment='群聊白名单群组ID列表'),
        sa.Column('require_mention', sa.Boolean, nullable=False, server_default='true', comment='群聊是否需要@提及'),
        sa.Column('block_streaming_config', postgresql.JSONB, comment='BlockStreaming配置'),
        sa.Column('debounce_config', postgresql.JSONB, comment='Debounce防抖配置'),
        sa.Column('human_like_delay', postgresql.JSONB, comment='人机延迟配置'),
        sa.Column('encrypt_key', sa.String(256), nullable=True, comment='飞书Encrypt Key (用于Webhook解密)'),
        sa.Column('verification_token', sa.String(256), nullable=True, comment='飞书Verification Token'),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default='true', comment='是否启用'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), comment='更新时间'),
        schema=schema
    )
    
    op.create_index('ix_feishu_access_config_app_id', 'feishu_access_config', ['app_id'], unique=True, schema=schema)
    
    # 2. 创建 feishu_group_config 表
    op.create_table(
        'feishu_group_config',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column('chat_id', sa.String(64), nullable=False, comment='飞书群组ID'),
        sa.Column('chat_name', sa.String(128), comment='群组名称'),
        sa.Column('access_config_id', postgresql.UUID(as_uuid=True), sa.ForeignKey(f'{schema}.feishu_access_config.id'), nullable=False, comment='关联的访问配置ID'),
        sa.Column('override_block_streaming', postgresql.JSONB, nullable=True, comment='覆盖的BlockStreaming配置'),
        sa.Column('override_debounce', postgresql.JSONB, nullable=True, comment='覆盖的Debounce配置'),
        sa.Column('override_human_like_delay', postgresql.JSONB, nullable=True, comment='覆盖的人机延迟配置'),
        sa.Column('extra_allowlist_users', postgresql.JSONB, server_default='[]', comment='额外允许的用户员工号'),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default='true', comment='是否启用'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), comment='更新时间'),
        schema=schema
    )
    
    op.create_index('ix_feishu_group_config_chat_id', 'feishu_group_config', ['chat_id'], unique=True, schema=schema)
    
    # 3. 创建 feishu_user_bindings 表
    op.create_table(
        'feishu_user_bindings',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column('open_id', sa.String(64), nullable=False, comment='飞书用户open_id'),
        sa.Column('union_id', sa.String(64), comment='飞书用户union_id'),
        sa.Column('employee_no', sa.String(32), comment='员工工号'),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey(f'{schema}.users.id'), nullable=True, comment='关联的系统用户ID'),
        sa.Column('feishu_name', sa.String(64), comment='飞书用户名'),
        sa.Column('feishu_email', sa.String(128), comment='飞书邮箱'),
        sa.Column('feishu_mobile', sa.String(20), comment='飞书手机号'),
        sa.Column('feishu_avatar', sa.String(512), comment='飞书头像URL'),
        sa.Column('app_id', sa.String(64), nullable=False, comment='飞书应用ID'),
        sa.Column('is_bound', sa.Boolean, nullable=False, server_default='false', comment='是否已绑定系统用户'),
        sa.Column('last_sync_at', sa.DateTime(timezone=True), comment='上次同步时间'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), comment='更新时间'),
        schema=schema
    )
    
    op.create_index('ix_feishu_user_bindings_open_id_app', 'feishu_user_bindings', ['open_id', 'app_id'], unique=True, schema=schema)
    op.create_index('ix_feishu_user_bindings_employee_no', 'feishu_user_bindings', ['employee_no'], schema=schema)
    op.create_index('ix_feishu_user_bindings_user_id', 'feishu_user_bindings', ['user_id'], schema=schema)
    
    # 4. 创建 feishu_media_files 表
    op.create_table(
        'feishu_media_files',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column('file_key', sa.String(256), nullable=False, comment='飞书文件key'),
        sa.Column('file_name', sa.String(256), nullable=False, comment='文件名'),
        sa.Column('file_type', sa.String(16), nullable=False, comment='文件类型: image/file/audio/media/sticker'),
        sa.Column('message_id', sa.String(64), nullable=False, comment='关联消息ID'),
        sa.Column('open_id', sa.String(64), nullable=False, comment='发送者open_id'),
        sa.Column('chat_id', sa.String(64), comment='群组ID'),
        sa.Column('file_size', sa.Integer, nullable=False, server_default='0', comment='文件大小(字节)'),
        sa.Column('mime_type', sa.String(64), comment='MIME类型'),
        sa.Column('sha256_hash', sa.String(64), comment='SHA256哈希'),
        sa.Column('local_path', sa.String(512), nullable=False, comment='本地存储路径'),
        sa.Column('download_status', sa.String(16), nullable=False, server_default='pending', comment='状态: pending/downloading/completed/failed'),
        sa.Column('download_retry_count', sa.Integer, nullable=False, server_default='0', comment='下载重试次数'),
        sa.Column('download_error', sa.Text, comment='下载错误信息'),
        sa.Column('is_duplicate', sa.Boolean, nullable=False, server_default='false', comment='是否重复文件'),
        sa.Column('duplicate_of', postgresql.UUID(as_uuid=True), sa.ForeignKey(f'{schema}.feishu_media_files.id'), nullable=True, comment='指向原始文件ID'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), comment='更新时间'),
        schema=schema
    )
    
    op.create_index('ix_feishu_media_files_file_key', 'feishu_media_files', ['file_key'], schema=schema)
    op.create_index('ix_feishu_media_files_message_id', 'feishu_media_files', ['message_id'], schema=schema)
    op.create_index('ix_feishu_media_files_sha256', 'feishu_media_files', ['sha256_hash'], schema=schema)
    op.create_index('ix_feishu_media_files_open_id', 'feishu_media_files', ['open_id'], schema=schema)
    
    # 5. 创建 feishu_message_logs 表
    op.create_table(
        'feishu_message_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column('event_id', sa.String(64), nullable=False, comment='飞书事件ID'),
        sa.Column('message_id', sa.String(64), nullable=False, comment='飞书消息ID'),
        sa.Column('open_id', sa.String(64), nullable=False, comment='发送者open_id'),
        sa.Column('employee_no', sa.String(32), comment='员工工号'),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey(f'{schema}.users.id'), nullable=True, comment='系统用户ID'),
        sa.Column('chat_id', sa.String(64), comment='群组ID'),
        sa.Column('chat_type', sa.String(16), nullable=False, comment='会话类型: p2p/group'),
        sa.Column('msg_type', sa.String(32), nullable=False, comment='消息类型'),
        sa.Column('content', postgresql.JSONB, server_default='{}', comment='消息内容(JSON)'),
        sa.Column('content_text', sa.Text, comment='消息文本内容'),
        sa.Column('status', sa.String(16), nullable=False, server_default='received', comment='状态: received/buffering/processing/completed/failed/rejected'),
        sa.Column('processing_started_at', sa.DateTime(timezone=True), comment='处理开始时间'),
        sa.Column('processing_completed_at', sa.DateTime(timezone=True), comment='处理完成时间'),
        sa.Column('processing_duration_ms', sa.Integer, comment='处理耗时(毫秒)'),
        sa.Column('reply_message_id', sa.String(64), comment='AI回复消息ID'),
        sa.Column('reply_content', sa.Text, comment='AI回复内容'),
        sa.Column('reply_card_id', sa.String(64), comment='流式卡片ID'),
        sa.Column('error_type', sa.String(32), comment='错误类型'),
        sa.Column('error_message', sa.Text, comment='错误信息'),
        sa.Column('arq_job_id', sa.String(64), comment='ARQ任务ID'),
        sa.Column('extra_metadata', postgresql.JSONB, server_default='{}', comment='额外元数据'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), comment='更新时间'),
        schema=schema
    )
    
    op.create_index('ix_feishu_message_logs_event_id', 'feishu_message_logs', ['event_id'], schema=schema)
    op.create_index('ix_feishu_message_logs_message_id', 'feishu_message_logs', ['message_id'], schema=schema)
    op.create_index('ix_feishu_message_logs_open_id', 'feishu_message_logs', ['open_id'], schema=schema)
    op.create_index('ix_feishu_message_logs_chat_id', 'feishu_message_logs', ['chat_id'], schema=schema)
    op.create_index('ix_feishu_message_logs_status', 'feishu_message_logs', ['status'], schema=schema)
    op.create_index('ix_feishu_message_logs_created_at', 'feishu_message_logs', ['created_at'], schema=schema)
    op.create_index('ix_feishu_message_logs_arq_job_id', 'feishu_message_logs', ['arq_job_id'], schema=schema)
    
    # 6. 创建 feishu_chat_session_mapping 表
    op.create_table(
        'feishu_chat_session_mapping',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column('chat_id', sa.String(64), nullable=False, comment='飞书群组ID'),
        sa.Column('open_id', sa.String(64), nullable=False, comment='用户open_id'),
        sa.Column('session_id', sa.String(64), nullable=False, comment='系统会话ID'),
        sa.Column('chat_type', sa.String(16), nullable=False, comment='会话类型: p2p/group'),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey(f'{schema}.users.id'), nullable=True, comment='系统用户ID'),
        sa.Column('last_active_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), comment='最后活跃时间'),
        sa.Column('message_count', sa.Integer, nullable=False, server_default='0', comment='消息计数'),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default='true', comment='是否活跃'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), comment='创建时间'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), comment='更新时间'),
        schema=schema
    )
    
    op.create_index('ix_feishu_chat_session_mapping_chat_open', 'feishu_chat_session_mapping', ['chat_id', 'open_id'], unique=True, schema=schema)
    op.create_index('ix_feishu_chat_session_mapping_session', 'feishu_chat_session_mapping', ['session_id'], schema=schema)
    op.create_index('ix_feishu_chat_session_mapping_user', 'feishu_chat_session_mapping', ['user_id'], schema=schema)
    op.create_index('ix_feishu_chat_session_mapping_active', 'feishu_chat_session_mapping', ['is_active', 'last_active_at'], schema=schema)


def downgrade():
    schema = 'sunny_agent'

    # 删除表的顺序（先删除有外键依赖的表）
    op.drop_table('feishu_chat_session_mapping', schema=schema)
    op.drop_table('feishu_message_logs', schema=schema)
    op.drop_table('feishu_media_files', schema=schema)
    op.drop_table('feishu_user_bindings', schema=schema)
    op.drop_table('feishu_group_config', schema=schema)
    op.drop_table('feishu_access_config', schema=schema)
