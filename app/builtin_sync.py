"""
应用启动时扫描内置 Skill / Plugin 目录，自动同步到 DB + 挂载目录

以文件目录为单一事实源（Single Source of Truth）：
- 目录存在 + DB 无记录 → INSERT
- 目录存在 + DB 有记录 → UPDATE description/path 等
- DB 有记录 + 目录已删除 → 标记 is_active=FALSE

前置条件：挂载目录（SANDBOX_HOST_VOLUME）必须存在，否则跳过同步。
"""

import json
import shutil
from pathlib import Path

import structlog
from sqlalchemy import text

from app.api.upload_utils import parse_frontmatter
from app.config import get_settings
from app.db.engine import async_session

log = structlog.get_logger()
settings = get_settings()

# 内置目录（项目源码内）
_BUILTIN_SKILLS_DIR = Path(__file__).parent / "skills" / "builtin_skills"
_BUILTIN_PLUGINS_DIR = Path(__file__).parent / "plugins" / "builtin_plugins"

# 系统 Plugin 使用的哨兵 owner_usernumb（与 Skill 的 NULL 不同，Plugin 字段 NOT NULL）
SYSTEM_OWNER = "SYSTEM"


async def sync_builtin_skills_and_plugins() -> None:
    """应用启动时调用：扫描内置目录，同步 DB + 挂载目录"""
    volume_root = Path(settings.SANDBOX_HOST_VOLUME)
    if not volume_root.exists():
        log.warning(
            "挂载目录不存在，跳过内置 Skill/Plugin 同步",
            path=str(volume_root),
        )
        return

    await _sync_builtin_skills(volume_root)
    await _sync_builtin_plugins(volume_root)


# ── Skill 同步 ──────────────────────────────────────────────


async def _sync_builtin_skills(volume_root: Path) -> None:
    """扫描内置 Skill 目录，UPSERT 到 DB，复制到挂载目录"""
    if not _BUILTIN_SKILLS_DIR.exists():
        log.debug("内置 Skill 目录不存在，跳过", path=str(_BUILTIN_SKILLS_DIR))
        return

    # 扫描所有有 SKILL.md 的子目录
    found_names: set[str] = set()
    skills_data: list[dict] = []

    for item in sorted(_BUILTIN_SKILLS_DIR.iterdir()):
        if not item.is_dir() or item.name.startswith("."):
            continue

        skill_md = item / "SKILL.md"
        if not skill_md.exists():
            log.warning("内置 Skill 目录缺少 SKILL.md，跳过", dir=item.name)
            continue

        # 解析 frontmatter 获取 name / description
        content = skill_md.read_text(encoding="utf-8")
        fm, _ = parse_frontmatter(content)
        name = fm.get("name", item.name)
        description = fm.get("description", "")

        if not description:
            log.warning("内置 Skill 缺少 description，跳过", name=name)
            continue

        has_scripts = (item / "scripts").is_dir()
        db_path = f"skills/{name}"

        found_names.add(name)
        skills_data.append({
            "name": name,
            "description": description,
            "path": db_path,
            "has_scripts": has_scripts,
            "source_dir": item,
        })

    # DB 同步
    async with async_session() as db:
        # UPSERT 每个 Skill
        for s in skills_data:
            await db.execute(
                text("""
                    INSERT INTO sunny_agent.skills
                        (id, name, description, path, scope, owner_usernumb,
                         is_active, is_default_enabled, has_scripts)
                    VALUES
                        (gen_random_uuid(), :name, :description, :path,
                         'system', NULL, TRUE, TRUE, :has_scripts)
                    ON CONFLICT (name) DO UPDATE SET
                        description = EXCLUDED.description,
                        path = EXCLUDED.path,
                        has_scripts = EXCLUDED.has_scripts,
                        is_active = TRUE,
                        updated_at = now()
                """),
                {
                    "name": s["name"],
                    "description": s["description"],
                    "path": s["path"],
                    "has_scripts": s["has_scripts"],
                },
            )

        # 查询当前 DB 中所有活跃的系统 Skill，标记已删除的
        result = await db.execute(text("""
            SELECT name FROM sunny_agent.skills
            WHERE scope = 'system' AND is_active = TRUE
        """))
        existing_names = {row.name for row in result.fetchall()}
        to_deactivate = existing_names - found_names

        for name in to_deactivate:
            await db.execute(
                text("""
                    UPDATE sunny_agent.skills
                    SET is_active = FALSE, updated_at = now()
                    WHERE scope = 'system' AND name = :name
                """),
                {"name": name},
            )

        await db.commit()

    # 文件复制到挂载目录（全量覆盖，文件少不用优化）
    for s in skills_data:
        target = volume_root / s["path"]
        source: Path = s["source_dir"]
        try:
            if target.exists():
                shutil.rmtree(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target)
        except Exception:
            log.exception("复制内置 Skill 失败", name=s["name"])

    if skills_data:
        log.info(
            "内置 Skill 同步完成",
            synced=len(skills_data),
            deactivated=len(to_deactivate) if "to_deactivate" in dir() else 0,
            names=[s["name"] for s in skills_data],
        )
    if to_deactivate:
        log.info("已下线的系统 Skill", names=list(to_deactivate))


# ── Plugin 同步 ──────────────────────────────────────────────


async def _sync_builtin_plugins(volume_root: Path) -> None:
    """扫描内置 Plugin 目录，UPSERT 到 DB，复制到挂载目录"""
    if not _BUILTIN_PLUGINS_DIR.exists():
        log.debug("内置 Plugin 目录不存在，跳过", path=str(_BUILTIN_PLUGINS_DIR))
        return

    found_names: set[str] = set()
    plugins_data: list[dict] = []

    for item in sorted(_BUILTIN_PLUGINS_DIR.iterdir()):
        if not item.is_dir() or item.name.startswith("."):
            continue

        # 解析 .claude-plugin/plugin.json
        plugin_json_path = item / ".claude-plugin" / "plugin.json"
        if not plugin_json_path.exists():
            log.warning("内置 Plugin 缺少 plugin.json，跳过", dir=item.name)
            continue

        try:
            plugin_data = json.loads(plugin_json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("内置 Plugin plugin.json 解析失败，跳过", dir=item.name, error=str(e))
            continue

        name = plugin_data.get("name", "")
        description = plugin_data.get("description", "")
        version = plugin_data.get("version", "0.0.0")

        if not name or not description:
            log.warning("内置 Plugin 缺少 name 或 description，跳过", dir=item.name)
            continue

        # 扫描 commands/ 目录
        commands_dir = item / "commands"
        commands: list[dict] = []
        if commands_dir.exists() and commands_dir.is_dir():
            for md_file in sorted(commands_dir.glob("*.md")):
                content = md_file.read_text(encoding="utf-8")
                fm, _ = parse_frontmatter(content)
                cmd_desc = fm.get("description", "")
                if not cmd_desc:
                    log.warning(
                        "内置 Plugin 命令缺少 description，跳过",
                        plugin=name, command=md_file.stem,
                    )
                    continue
                commands.append({
                    "name": md_file.stem,
                    "description": cmd_desc,
                    "argument_hint": fm.get("argument-hint"),
                    "path": f"commands/{md_file.name}",
                })

        if not commands:
            log.warning("内置 Plugin 无有效命令，跳过", name=name)
            continue

        db_path = f"plugins/{name}"

        found_names.add(name)
        plugins_data.append({
            "name": name,
            "description": description,
            "version": version,
            "path": db_path,
            "commands": commands,
            "source_dir": item,
        })

    # DB 同步
    async with async_session() as db:
        for p in plugins_data:
            # UPSERT 插件主记录
            result = await db.execute(
                text("""
                    INSERT INTO sunny_agent.plugins
                        (id, name, version, description, owner_usernumb, path, is_active)
                    VALUES
                        (gen_random_uuid(), :name, :version, :description,
                         :owner, :path, TRUE)
                    ON CONFLICT (owner_usernumb, name) DO UPDATE SET
                        version = EXCLUDED.version,
                        description = EXCLUDED.description,
                        path = EXCLUDED.path,
                        is_active = TRUE,
                        updated_at = now()
                    RETURNING id
                """),
                {
                    "name": p["name"],
                    "version": p["version"],
                    "description": p["description"],
                    "owner": SYSTEM_OWNER,
                    "path": p["path"],
                },
            )
            plugin_id = result.scalar_one()

            # 先删旧命令，再插新命令
            await db.execute(
                text("DELETE FROM sunny_agent.plugin_commands WHERE plugin_id = :pid"),
                {"pid": plugin_id},
            )
            for cmd in p["commands"]:
                await db.execute(
                    text("""
                        INSERT INTO sunny_agent.plugin_commands
                            (id, plugin_id, name, description, argument_hint, path)
                        VALUES
                            (gen_random_uuid(), :pid, :name, :desc, :hint, :path)
                    """),
                    {
                        "pid": plugin_id,
                        "name": cmd["name"],
                        "desc": cmd["description"],
                        "hint": cmd["argument_hint"],
                        "path": cmd["path"],
                    },
                )

        # 标记已删除的系统 Plugin
        result = await db.execute(text("""
            SELECT name FROM sunny_agent.plugins
            WHERE owner_usernumb = :owner AND is_active = TRUE
        """), {"owner": SYSTEM_OWNER})
        existing_names = {row.name for row in result.fetchall()}
        to_deactivate = existing_names - found_names

        for name in to_deactivate:
            await db.execute(
                text("""
                    UPDATE sunny_agent.plugins
                    SET is_active = FALSE, updated_at = now()
                    WHERE owner_usernumb = :owner AND name = :name
                """),
                {"owner": SYSTEM_OWNER, "name": name},
            )

        await db.commit()

    # 文件复制到挂载目录
    for p in plugins_data:
        target = volume_root / p["path"]
        source: Path = p["source_dir"]
        try:
            if target.exists():
                shutil.rmtree(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target)
        except Exception:
            log.exception("复制内置 Plugin 失败", name=p["name"])

    if plugins_data:
        log.info(
            "内置 Plugin 同步完成",
            synced=len(plugins_data),
            names=[p["name"] for p in plugins_data],
        )
    if to_deactivate:
        log.info("已下线的系统 Plugin", names=list(to_deactivate))
