"""
模型统一导出：Alembic 自动发现需要导入所有模型
"""

from app.db.models.base import Base
from app.db.models.user import User, Role
from app.db.models.audit import AuditLog
from app.db.models.chat import ChatSession, ChatMessage, L3Step
from app.db.models.skill import Skill, UserSkillSetting
from app.db.models.plugin import Plugin, PluginCommand
from app.db.models.data_scope import DataScopePolicy
from app.db.models.project import Project
from app.db.models.file import File
from app.db.models.cron_job import CronJob

__all__ = [
    "Base", "User", "Role", "AuditLog",
    "ChatSession", "ChatMessage", "L3Step",
    "Skill", "UserSkillSetting",
    "Plugin", "PluginCommand",
    "DataScopePolicy",
    "Project", "File",
    "CronJob",
]
