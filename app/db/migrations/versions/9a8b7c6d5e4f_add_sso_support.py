"""add_sso_support: 新增 SSO 相关字段和数据隔离策略

Revision ID: 9a8b7c6d5e4f
Revises: f1e2d3c4b5a6
Create Date: 2026-03-03

"""
from typing import Sequence, Union

import uuid
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '9a8b7c6d5e4f'
down_revision: Union[str, None] = 'f1e2d3c4b5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 扩展 users 表 - 新增 SSO 相关字段
    op.add_column('users', 
        sa.Column('source', sa.String(20), nullable=False, 
            server_default='local', comment='用户来源：local|sso|ldap|feishu'),
        schema='sunny_agent')
    
    op.add_column('users',
        sa.Column('company', sa.String(100), nullable=True, 
            comment='公司/事业部'),
        schema='sunny_agent')
    
    op.add_column('users',
        sa.Column('phone', sa.String(20), nullable=True, 
            comment='手机号'),
        schema='sunny_agent')
    
    op.add_column('users',
        sa.Column('avatar_url', sa.String(512), nullable=True, 
            comment='头像 URL'),
        schema='sunny_agent')
    
    op.add_column('users',
        sa.Column('sso_last_login', sa.DateTime(timezone=True), nullable=True, 
            comment='上次 SSO 登录时间'),
        schema='sunny_agent')
    
    # 2. 创建索引
    op.create_index('ix_users_company', 'users', ['company'], schema='sunny_agent')
    op.create_index('ix_users_source', 'users', ['source'], schema='sunny_agent')
    
    # 3. 创建 data_scope_policies 表
    op.create_table('data_scope_policies',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('company', sa.String(100), nullable=False, comment='公司名'),
        sa.Column('resource_type', sa.String(50), nullable=False, comment='资源类型'),
        sa.Column('isolation_level', sa.String(20), 
            nullable=False, server_default='strict', comment='隔离级别：strict|shared'),
        sa.Column('created_at', sa.DateTime(), 
            server_default=sa.text('now()'), comment='创建时间'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('company', 'resource_type', name='uq_company_resource'),
        schema='sunny_agent'
    )
    
    # 4. 预置"普通用户"角色
    roles_table = sa.table('roles',
        sa.column('id', sa.UUID()),
        sa.column('name', sa.String()),
        sa.column('permissions', postgresql.JSONB()),
        sa.column('description', sa.Text()),
        schema='sunny_agent'
    )
    
    op.bulk_insert(roles_table, [
        {
            'id': uuid.uuid4(),
            'name': '普通用户',
            'permissions': [
                'chat:send', 'chat:read',
                'files:upload', 'files:read',
                'plugins:use', 'plugins:list',
                'skills:use', 'skills:list'
            ],
            'description': '普通用户角色，基础对话和工具使用权限'
        }
    ])
    
    # 5. 预置数据隔离策略（仅舜宇光学科技）
    policies_table = sa.table('data_scope_policies',
        sa.column('id', sa.UUID()),
        sa.column('company', sa.String()),
        sa.column('resource_type', sa.String()),
        sa.column('isolation_level', sa.String()),
        schema='sunny_agent'
    )
    
    companies = ['舜宇光学科技']
    resource_types = ['chat_messages', 'chat_sessions', 'files']
    
    for company in companies:
        for resource_type in resource_types:
            op.bulk_insert(policies_table, [
                {
                    'id': uuid.uuid4(),
                    'company': company,
                    'resource_type': resource_type,
                    'isolation_level': 'strict'
                }
            ])


def downgrade() -> None:
    # 删除预置数据
    op.execute("DELETE FROM sunny_agent.data_scope_policies WHERE company = '舜宇光学科技'")
    op.execute("DELETE FROM sunny_agent.roles WHERE name = '普通用户'")
    
    # 删除表
    op.drop_table('data_scope_policies', schema='sunny_agent')
    
    # 删除索引
    op.drop_index('ix_users_source', 'users', schema='sunny_agent')
    op.drop_index('ix_users_company', 'users', schema='sunny_agent')
    
    # 删除字段
    op.drop_column('users', 'sso_last_login', schema='sunny_agent')
    op.drop_column('users', 'avatar_url', schema='sunny_agent')
    op.drop_column('users', 'phone', schema='sunny_agent')
    op.drop_column('users', 'company', schema='sunny_agent')
    op.drop_column('users', 'source', schema='sunny_agent')
