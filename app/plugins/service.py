"""
Plugin 服务层：DB 查询 + 文件读取 + Skills 目录扫描

设计：
- PluginCommandInfo dataclass：运行时命令元信息（参考 SkillInfo 模式）
- PluginService：DB 查询（get_user_command）+ 文件操作（read_command_content / scan_plugin_skills）
- 路径安全：所有宿主机路径操作均通过 resolve() + relative_to() 防止路径穿越
- frontmatter 解析：统一使用 upload_utils.parse_frontmatter
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import structlog
from sqlalchemy import text

from app.api.upload_utils import parse_frontmatter
from app.config import get_settings
from app.db.engine import async_session

log = structlog.get_logger()
settings = get_settings()


@dataclass
class PluginCommandInfo:
    """单个 Plugin Command 的运行时元信息（由 DB 查询结果构建）"""

    plugin_id: str
    plugin_name: str
    # DB 中存储的相对路径，如 plugins/1131618/analyze-excel
    plugin_path: str
    command_name: str
    # 相对于插件根目录的命令文件路径，如 commands/analyze-xlsx.md
    command_path: str
    owner_usernumb: str

    def get_host_command_path(self) -> Path:
        """
        宿主机 COMMAND.md 绝对路径，带路径穿越防护。
        越界抛 ValueError（调用方应捕获并返回 404/500）。
        """
        volume_root = Path(settings.SANDBOX_HOST_VOLUME).resolve()
        full = (volume_root / self.plugin_path / self.command_path).resolve()
        full.relative_to(volume_root)  # 越界时抛 ValueError
        return full

    def get_host_skills_dir(self) -> Path:
        """宿主机 skills/ 目录路径（不校验存在性，调用方自行检查）"""
        volume_root = Path(settings.SANDBOX_HOST_VOLUME).resolve()
        return (volume_root / self.plugin_path / "skills").resolve()

    def get_container_skills_base(self) -> str:
        """容器内 skills/ 目录路径前缀 /mnt/{plugin_path}/skills"""
        return f"/mnt/{self.plugin_path}/skills"


class PluginService:
    """Plugin DB 查询服务（供 chat.py Plugin 命令处理路径调用）"""

    async def get_user_command(
        self,
        plugin_name: str,
        command_name: str,
        usernumb: str,
    ) -> PluginCommandInfo | None:
        """
        从 DB 查询 Plugin 命令。

        限制：只能访问自己上传的 Plugin（owner_usernumb = usernumb），
        且 Plugin 必须处于激活状态（is_active = TRUE）。

        返回 PluginCommandInfo 或 None（不存在/无权限）。
        """
        query = text("""
            SELECT
                p.id        AS plugin_id,
                p.name      AS plugin_name,
                p.path      AS plugin_path,
                p.owner_usernumb,
                pc.name     AS command_name,
                pc.path     AS command_path
            FROM sunny_agent.plugins p
            JOIN sunny_agent.plugin_commands pc
                ON pc.plugin_id = p.id
            WHERE
                p.name = :plugin_name
                AND p.owner_usernumb IN (:usernumb, 'system')
                AND p.is_active = TRUE
                AND pc.name = :command_name
            LIMIT 1
        """)

        try:
            async with async_session() as session:
                result = await session.execute(query, {
                    "plugin_name": plugin_name,
                    "usernumb": usernumb,
                    "command_name": command_name,
                })
                row = result.fetchone()

            if row is None:
                log.warning(
                    "Plugin 命令不存在或无权限",
                    plugin_name=plugin_name,
                    command_name=command_name,
                    usernumb=usernumb,
                )
                return None

            return PluginCommandInfo(
                plugin_id=str(row.plugin_id),
                plugin_name=row.plugin_name,
                plugin_path=row.plugin_path,
                command_name=row.command_name,
                command_path=row.command_path,
                owner_usernumb=row.owner_usernumb,
            )

        except Exception:
            log.exception(
                "查询 Plugin 命令失败",
                plugin_name=plugin_name,
                command_name=command_name,
                usernumb=usernumb,
            )
            return None

    def read_command_content(self, info: PluginCommandInfo) -> str:
        """
        读取 COMMAND.md 文件完整内容（同步 IO，文件通常较小）。

        抛 FileNotFoundError 若文件不存在（调用方处理）。
        抛 ValueError 若路径穿越（调用方处理为 500）。
        """
        host_path = info.get_host_command_path()
        if not host_path.exists():
            raise FileNotFoundError(f"COMMAND.md 不存在：{host_path}")
        return host_path.read_text(encoding="utf-8")

    def scan_plugin_skills(self, info: PluginCommandInfo) -> list[dict]:
        """
        扫描插件 skills/ 目录，返回可用 Skill 列表。

        若 skills/ 目录不存在，返回空列表。
        每项格式：{"name": str, "skill_md_path": "/mnt/.../skills/{name}/SKILL.md"}

        只扫描直接子目录（不递归），每个子目录对应一个 Skill。
        """
        skills_dir = info.get_host_skills_dir()
        if not skills_dir.exists() or not skills_dir.is_dir():
            return []

        container_base = info.get_container_skills_base()
        result = []

        for item in sorted(skills_dir.iterdir()):
            if not item.is_dir():
                continue
            skill_name = item.name
            # 跳过隐藏目录（如 .gitkeep 之类）
            if skill_name.startswith("."):
                continue
            skill_md_host = item / "SKILL.md"
            # 只列出确实有 SKILL.md 的目录
            if skill_md_host.exists():
                result.append({
                    "name": skill_name,
                    "skill_md_path": f"{container_base}/{skill_name}/SKILL.md",
                })

        log.debug(
            "扫描插件 Skills",
            plugin_name=info.plugin_name,
            count=len(result),
            skills=[s["name"] for s in result],
        )
        return result

    async def list_user_plugins(self, usernumb: str) -> list[dict]:
        """
        查询当前用户所有 Plugin（含每个 Plugin 的命令数）。
        供 GET /api/plugins/list 接口使用。
        """
        query = text("""
            SELECT
                p.id,
                p.name,
                p.version,
                p.description,
                p.path,
                p.is_active,
                p.created_at,
                p.updated_at,
                COUNT(pc.id) AS command_count
            FROM sunny_agent.plugins p
            LEFT JOIN sunny_agent.plugin_commands pc
                ON pc.plugin_id = p.id
            WHERE p.owner_usernumb IN (:usernumb, 'system')
            GROUP BY p.id, p.name, p.version, p.description, p.path,
                     p.is_active, p.created_at, p.updated_at
            ORDER BY p.created_at DESC
        """)

        try:
            async with async_session() as session:
                result = await session.execute(query, {"usernumb": usernumb})
                rows = result.fetchall()

            return [
                {
                    "id": str(row.id),
                    "name": row.name,
                    "version": row.version,
                    "description": row.description,
                    "is_active": row.is_active,
                    "command_count": row.command_count,
                    "created_at": row.created_at.isoformat(),
                    "updated_at": row.updated_at.isoformat(),
                }
                for row in rows
            ]

        except Exception:
            log.exception("查询用户 Plugin 列表失败", usernumb=usernumb)
            return []


# 模块级单例
plugin_service = PluginService()
