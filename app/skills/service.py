"""
Skill 服务层：从 DB 动态加载当前用户可用的 Skill 列表

设计：
- 每次请求时调用 get_user_skills(usernumb) 查询 DB
- 返回 SkillInfo 列表，包含 name/description/path/scope/has_scripts
- SkillInfo 提供路径工具方法，防止路径穿越
- 无内存缓存（每次请求实时查，保证动态感知新 Skill）
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog
from sqlalchemy import select, text

from app.config import get_settings
from app.db.engine import async_session
from app.db.models.skill import Skill, UserSkillSetting

log = structlog.get_logger()
settings = get_settings()


@dataclass
class SkillInfo:
    """单个 Skill 的运行时元信息（由 DB 查询结果构建）"""

    id: str
    name: str
    description: str
    # DB 中存储的相对路径，不含开头/、末尾/、/SKILL.md
    # 例：skills/github  或  skills/users/1131618/my_skill
    path: str
    scope: str  # system / user

    def get_container_skill_path(self) -> str:
        """返回容器内 SKILL.md 的绝对路径"""
        return f"/mnt/{self.path}/SKILL.md"

    def get_container_scripts_path(self) -> str:
        """返回容器内 scripts/ 目录的绝对路径"""
        return f"/mnt/{self.path}/scripts"

    def get_host_skill_dir(self) -> Path:
        """
        将 DB 中的相对路径安全拼接为宿主机绝对目录路径。
        防止 ../ 路径穿越：若 path 越界则抛 ValueError。
        """
        volume_root = Path(settings.SANDBOX_HOST_VOLUME).resolve()
        full = (volume_root / self.path).resolve()
        # 必须仍在 volume_root 内
        full.relative_to(volume_root)
        return full


class SkillService:
    """Skill DB 查询服务（供 ExecutionRouter 每次请求调用）"""

    async def get_user_skills(self, usernumb: str) -> list[SkillInfo]:
        """
        查询当前用户可用的 Skill 列表。

        状态优先级：
        1. skills.is_active = false → 所有用户不可见（admin 下线）
        2. user_skill_settings.is_enabled 有记录 → 以用户显式设置为准
        3. 无用户记录 → 回退到 skills.is_default_enabled

        系统 Skill：COALESCE(uss.is_enabled, s.is_default_enabled) = TRUE
        用户 Skill：创建者始终可见，COALESCE(uss.is_enabled, TRUE) = TRUE
        """
        query = text("""
            SELECT s.id, s.name, s.description, s.path, s.scope
            FROM sunny_agent.skills s
            LEFT JOIN sunny_agent.user_skill_settings uss
                ON uss.skill_id = s.id AND uss.usernumb = :usernumb
            WHERE
                s.is_active = TRUE
                AND (
                    (s.scope = 'user'
                        AND s.owner_usernumb = :usernumb
                        AND COALESCE(uss.is_enabled, TRUE) = TRUE)
                    OR
                    (s.scope = 'system'
                        AND COALESCE(uss.is_enabled, s.is_default_enabled) = TRUE)
                )
            ORDER BY s.scope DESC, s.name ASC
        """)

        try:
            async with async_session() as session:
                result = await session.execute(query, {"usernumb": usernumb})
                rows = result.fetchall()

            skills = [
                SkillInfo(
                    id=str(row.id),
                    name=row.name,
                    description=row.description,
                    path=row.path,
                    scope=row.scope,
                )
                for row in rows
            ]

            log.debug(
                "用户 Skill 列表已加载",
                usernumb=usernumb,
                count=len(skills),
                names=[s.name for s in skills],
            )
            return skills

        except Exception:
            log.exception("加载用户 Skill 列表失败", usernumb=usernumb)
            return []


# 模块级单例
skill_service = SkillService()
