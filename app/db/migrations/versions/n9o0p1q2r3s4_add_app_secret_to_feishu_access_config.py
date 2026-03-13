"""
feishu_access_config 添加 app_secret 列

Revision ID: n9o0p1q2r3s4
Revises: f8e9d0c1b2a3
Create Date: 2026-03-13 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'n9o0p1q2r3s4'
down_revision = 'f8e9d0c1b2a3'
branch_labels = None
depends_on = None


def upgrade():
    schema = 'sunny_agent'
    op.add_column(
        'feishu_access_config',
        sa.Column('app_secret', sa.String(256), nullable=True, comment='飞书应用密钥 (App Secret)'),
        schema=schema,
    )


def downgrade():
    schema = 'sunny_agent'
    op.drop_column('feishu_access_config', 'app_secret', schema=schema)
