"""username_drop_unique_usernumb_not_null_audit_rename

Revision ID: 684e135865f9
Revises: edbe70b998c2
Create Date: 2026-02-11 15:35:41.653091
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '684e135865f9'
down_revision: Union[str, None] = 'edbe70b998c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. audit_logs: 重命名 username → usernumb，缩短长度，更新 comment
    op.alter_column(
        'audit_logs', 'username',
        new_column_name='usernumb',
        existing_type=sa.VARCHAR(length=64),
        type_=sa.String(length=32),
        comment='人员工号（冗余，方便查询）',
        existing_comment='用户名（冗余，方便查询）',
        existing_nullable=True,
        schema='sunny_agent',
    )

    # 2. users: username 去掉唯一约束，更新 comment
    op.drop_constraint('users_username_key', 'users', schema='sunny_agent', type_='unique')
    op.alter_column(
        'users', 'username',
        existing_type=sa.VARCHAR(length=64),
        comment='姓名',
        existing_comment='用户名',
        existing_nullable=False,
        schema='sunny_agent',
    )

    # 3. users: usernumb 先填充已有 NULL 记录（用 username 作为临时工号），再设 NOT NULL
    op.execute(
        "UPDATE sunny_agent.users SET usernumb = username WHERE usernumb IS NULL"
    )
    op.alter_column(
        'users', 'usernumb',
        existing_type=sa.VARCHAR(length=32),
        nullable=False,
        existing_comment='人员工号',
        schema='sunny_agent',
    )


def downgrade() -> None:
    # 3. users: usernumb 恢复为 nullable
    op.alter_column(
        'users', 'usernumb',
        existing_type=sa.VARCHAR(length=32),
        nullable=True,
        existing_comment='人员工号',
        schema='sunny_agent',
    )

    # 2. users: username 恢复唯一约束和 comment
    op.alter_column(
        'users', 'username',
        existing_type=sa.VARCHAR(length=64),
        comment='用户名',
        existing_comment='姓名',
        existing_nullable=False,
        schema='sunny_agent',
    )
    op.create_unique_constraint('users_username_key', 'users', ['username'], schema='sunny_agent')

    # 1. audit_logs: 重命名 usernumb → username
    op.alter_column(
        'audit_logs', 'usernumb',
        new_column_name='username',
        existing_type=sa.String(length=32),
        type_=sa.VARCHAR(length=64),
        comment='用户名（冗余，方便查询）',
        existing_comment='人员工号（冗余，方便查询）',
        existing_nullable=True,
        schema='sunny_agent',
    )
