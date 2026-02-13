"""
模型统一导出：Alembic 自动发现需要导入所有模型
"""

from app.db.models.base import Base
from app.db.models.user import User, Role
from app.db.models.audit import AuditLog
from app.db.models.codebook import Codebook
from app.db.models.template import PromptTemplate

__all__ = ["Base", "User", "Role", "AuditLog", "Codebook", "PromptTemplate"]
