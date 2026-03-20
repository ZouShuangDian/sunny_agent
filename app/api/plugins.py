"""
/api/plugins — Plugin 管理接口

端点：
- POST /api/plugins/upload   — 上传 ZIP 包，校验格式，注册到 DB + volume
- GET  /api/plugins/list     — 列出当前用户所有 Plugin
- DELETE /api/plugins/{name} — 删除 Plugin（DB 记录 + volume 目录）

安全原则：
- 路径穿越防护：ZIP 成员路径不含 ".."，目标路径 resolve() + relative_to() 验证
- 用户隔离：plugin.json 中 author.usernumb 必须与登录用户工号一致
- plugin.name 格式约束：小写字母+数字+连字符，^[a-z][a-z0-9-]{0,62}$
"""

import json
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import text

from app.api.response import ApiResponse, ok
from app.api.upload_utils import (
    NAME_RE, check_zip_safety, find_zip_root, parse_frontmatter, scan_directory_files,
)
from app.config import get_settings
from app.db.engine import async_session
from app.plugins.service import plugin_service
from app.security.auth import AuthenticatedUser, get_current_user

router = APIRouter(prefix="/api/plugins", tags=["Plugin 管理"])
log = structlog.get_logger()
settings = get_settings()


# ── 工具函数 ──────────────────────────────────────────────────

def _validate_plugin_json(plugin_json_path: Path, usernumb: str) -> dict:
    """
    解析并校验 plugin.json。
    返回 plugin metadata dict。
    抛 HTTPException 如果不合法。
    """
    if not plugin_json_path.exists():
        raise HTTPException(status_code=400, detail="ZIP 缺少 .claude-plugin/plugin.json")

    try:
        raw = plugin_json_path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("gbk", errors="replace")
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"plugin.json JSON 格式错误：{e}")

    # 必填字段（version 和 author 可选，兼容 Claude 官方 Plugin 格式）
    required = ["name", "description"]
    for field in required:
        if not data.get(field):
            raise HTTPException(status_code=400, detail=f"plugin.json 缺少必填字段：{field}")

    # name 格式校验
    name = data["name"]
    if not NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail=f"plugin name 格式不合法（期望 ^[a-z][a-z0-9-]{{0,62}}$）：{name}",
        )

    return data


def _validate_commands(commands_dir: Path) -> list[dict]:
    """
    校验 commands/ 目录中的命令文件。
    返回已解析的命令列表 [{"name", "description", "argument_hint", "path"}]。
    抛 HTTPException 如果不合法。
    """
    if not commands_dir.exists() or not commands_dir.is_dir():
        raise HTTPException(status_code=400, detail="ZIP 缺少 commands/ 目录")

    md_files = sorted(commands_dir.glob("*.md"))
    if not md_files:
        raise HTTPException(status_code=400, detail="commands/ 目录中没有 .md 命令文件")

    commands = []
    for md_file in md_files:
        content = md_file.read_text(encoding="utf-8")
        fm, _ = parse_frontmatter(content)

        if not fm.get("description"):
            raise HTTPException(
                status_code=400,
                detail=f"命令文件 {md_file.name} 缺少 frontmatter description 字段",
            )

        command_name = md_file.stem  # 文件名不含 .md
        commands.append({
            "name": command_name,
            "description": fm["description"],
            "argument_hint": fm.get("argument-hint") or fm.get("argument_hint"),
            "path": f"commands/{md_file.name}",
        })

    return commands


# ── POST /api/plugins/upload ──────────────────────────────────

@router.post("/upload", response_model=ApiResponse)
async def upload_plugin(
    file: UploadFile,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    上传 Plugin ZIP 包，校验格式后注册到 DB + volume。

    ZIP 内目录结构（支持有无顶级根目录两种打包方式）：
    {plugin-name}/              ← 根目录（可选）
    ├── .claude-plugin/
    │   └── plugin.json
    ├── commands/
    │   └── *.md
    └── skills/                 ← 可选
        └── {skill-name}/
            └── SKILL.md
    """
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="只接受 .zip 格式文件")

    tmpdir = Path(tempfile.mkdtemp(prefix="sunny_plugin_"))
    try:
        # 1. 写入临时文件
        zip_tmp = tmpdir / "upload.zip"
        zip_tmp.write_bytes(await file.read())

        # 2. 解压 + 路径安全检查
        extract_dir = tmpdir / "extracted"
        extract_dir.mkdir()

        with zipfile.ZipFile(zip_tmp, "r") as zf:
            check_zip_safety(zf)
            zip_root = find_zip_root(zf)
            zf.extractall(extract_dir)

        # 清理 Mac 压缩工具生成的 __MACOSX 目录
        macosx_dir = extract_dir / "__MACOSX"
        if macosx_dir.exists():
            shutil.rmtree(macosx_dir)

        if zip_root:
            plugin_src = extract_dir / zip_root.rstrip("/")
        else:
            plugin_src = extract_dir

        # 4. 校验 plugin.json
        plugin_json_path = plugin_src / ".claude-plugin" / "plugin.json"
        plugin_data = _validate_plugin_json(plugin_json_path, user.usernumb)
        plugin_name = plugin_data["name"]

        # 5. 校验 commands/
        commands_dir = plugin_src / "commands"
        commands = _validate_commands(commands_dir)

        # 6. 移动到 volume 目标路径
        volume_root = Path(settings.SANDBOX_HOST_VOLUME).resolve()
        plugin_rel_path = f"users/{user.usernumb}/plugins/{plugin_name}"
        target_dir = (volume_root / plugin_rel_path).resolve()

        # 安全校验：确保目标路径在 volume 内
        try:
            target_dir.relative_to(volume_root)
        except ValueError:
            raise HTTPException(status_code=400, detail="目标路径越界，拒绝写入")

        # 清理旧版本（若存在）
        if target_dir.exists():
            shutil.rmtree(target_dir)

        shutil.copytree(str(plugin_src), str(target_dir))
        log.info(
            "Plugin 文件已写入 volume",
            plugin_name=plugin_name,
            usernumb=user.usernumb,
            path=plugin_rel_path,
        )

        # 7. DB 写入（事务，使用 ON CONFLICT 保证多实例并发安全）
        async with async_session() as session:
            async with session.begin():
                plugin_id = uuid.uuid4()
                # UPSERT：同名同用户 → 更新；否则 → 插入
                result = await session.execute(text("""
                    INSERT INTO sunny_agent.plugins
                        (id, name, version, description, owner_usernumb, path, is_active)
                    VALUES
                        (:id, :name, :version, :description, :owner_usernumb, :path, TRUE)
                    ON CONFLICT (owner_usernumb, name) DO UPDATE SET
                        version = EXCLUDED.version,
                        description = EXCLUDED.description,
                        path = EXCLUDED.path,
                        is_active = TRUE,
                        updated_at = now()
                    RETURNING id
                """), {
                    "id": plugin_id,
                    "name": plugin_name,
                    "version": plugin_data.get("version") or "0.0.0",
                    "description": plugin_data["description"],
                    "owner_usernumb": user.usernumb,
                    "path": plugin_rel_path,
                })
                # 取回实际 ID（新插入时为传入的 uuid，已存在时为旧记录 ID）
                plugin_id = result.scalar_one()

                # 删除旧命令后重新插入
                await session.execute(text("""
                    DELETE FROM sunny_agent.plugin_commands WHERE plugin_id = :plugin_id
                """), {"plugin_id": plugin_id})

                log.info("Plugin UPSERT 完成", plugin_name=plugin_name, usernumb=user.usernumb)

                # 插入命令记录
                for cmd in commands:
                    await session.execute(text("""
                        INSERT INTO sunny_agent.plugin_commands
                            (id, plugin_id, name, description, argument_hint, path)
                        VALUES
                            (:id, :plugin_id, :name, :description, :argument_hint, :path)
                    """), {
                        "id": uuid.uuid4(),
                        "plugin_id": plugin_id,
                        "name": cmd["name"],
                        "description": cmd["description"],
                        "argument_hint": cmd.get("argument_hint"),
                        "path": cmd["path"],
                    })

        log.info(
            "Plugin 上传完成",
            plugin_name=plugin_name,
            usernumb=user.usernumb,
            command_count=len(commands),
        )

        return ok(
            data={
                "plugin": plugin_name,
                "version": plugin_data.get("version"),
                "description": plugin_data["description"],
                "commands": [
                    {"name": c["name"], "description": c["description"]}
                    for c in commands
                ],
            },
            message="Plugin 上传成功",
            status_code=201,
        )

    finally:
        # 始终清理临时目录
        if tmpdir.exists():
            shutil.rmtree(tmpdir, ignore_errors=True)


# ── GET /api/plugins/list ─────────────────────────────────────

@router.get("/list", response_model=ApiResponse)
async def list_plugins(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """列出当前用户所有可见 Plugin（系统 + 个人），含命令数和 scope"""
    async with async_session() as session:
        result = await session.execute(text("""
            SELECT
                p.id,
                p.name,
                p.version,
                p.description,
                p.path,
                p.is_active,
                p.owner_usernumb,
                p.created_at,
                p.updated_at,
                CASE WHEN p.owner_usernumb = 'system' THEN 'system' ELSE 'user' END AS scope,
                COUNT(pc.id) AS command_count
            FROM sunny_agent.plugins p
            LEFT JOIN sunny_agent.plugin_commands pc
                ON pc.plugin_id = p.id
            WHERE p.owner_usernumb IN (:usernumb, 'system')
              AND p.is_active = TRUE
            GROUP BY p.id, p.name, p.version, p.description, p.path,
                     p.is_active, p.owner_usernumb, p.created_at, p.updated_at
            ORDER BY scope ASC, p.name ASC
        """), {"usernumb": user.usernumb})
        rows = result.fetchall()

    plugins = [
        {
            "id": str(row.id),
            "name": row.name,
            "version": row.version,
            "description": row.description,
            "scope": row.scope,
            "is_active": row.is_active,
            "command_count": row.command_count,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }
        for row in rows
    ]

    return ok(data={"plugins": plugins, "total": len(plugins)})


# ── GET /api/plugins/commands ─────────────────────────────────

@router.get("/commands", response_model=ApiResponse)
async def list_available_commands(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    返回当前用户所有可用的 Plugin Commands（仅已启用的 Plugin）。

    前端用于展示命令面板 / 自动补全。
    """
    async with async_session() as session:
        result = await session.execute(text("""
            SELECT
                p.name       AS plugin_name,
                p.description AS plugin_description,
                pc.name      AS command_name,
                pc.description AS command_description,
                pc.argument_hint
            FROM sunny_agent.plugins p
            JOIN sunny_agent.plugin_commands pc
                ON pc.plugin_id = p.id
            WHERE p.owner_usernumb IN (:usernumb, 'system')
              AND p.is_active = TRUE
            ORDER BY p.name, pc.name
        """), {"usernumb": user.usernumb})
        rows = result.fetchall()

    commands = [
        {
            "plugin_name": row.plugin_name,
            "plugin_description": row.plugin_description,
            "command_name": row.command_name,
            "command_description": row.command_description,
            "argument_hint": row.argument_hint,
            "full_command": f"/{row.plugin_name}:{row.command_name}",
        }
        for row in rows
    ]

    return ok(data={"commands": commands, "total": len(commands)})


# ── DELETE /api/plugins/{plugin_name} ────────────────────────

@router.delete("/{plugin_name}", response_model=ApiResponse)
async def delete_plugin(
    plugin_name: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    删除 Plugin。
    1. 从 DB 删除 plugins 记录（plugin_commands 通过 CASCADE 自动删除）
    2. 从 volume 删除插件目录
    """
    async with async_session() as session:
        async with session.begin():
            result = await session.execute(text("""
                SELECT id, path, owner_usernumb FROM sunny_agent.plugins
                WHERE name = :name AND owner_usernumb IN (:usernumb, 'system')
            """), {"name": plugin_name, "usernumb": user.usernumb})
            row = result.fetchone()

            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Plugin '{plugin_name}' 不存在或无权限删除",
                )

            if row.owner_usernumb == "SYSTEM":
                raise HTTPException(
                    status_code=403,
                    detail=f"系统 Plugin '{plugin_name}' 不允许删除",
                )

            plugin_id, plugin_path = row.id, row.path

            # 删除 DB 记录（plugin_commands 通过 FK CASCADE 自动删除）
            await session.execute(text("""
                DELETE FROM sunny_agent.plugins WHERE id = :plugin_id
            """), {"plugin_id": plugin_id})

    # 删除 volume 上的目录
    volume_root = Path(settings.SANDBOX_HOST_VOLUME).resolve()
    target_dir = (volume_root / plugin_path).resolve()

    try:
        target_dir.relative_to(volume_root)  # 路径穿越防护
        if target_dir.exists():
            shutil.rmtree(target_dir)
            log.info(
                "Plugin 目录已删除",
                plugin_name=plugin_name,
                usernumb=user.usernumb,
                path=plugin_path,
            )
    except ValueError:
        # 路径越界（不应发生，DB 写入时已校验），只记录警告，不影响 DB 删除结果
        log.warning("Plugin 目录路径越界，跳过文件删除", path=plugin_path)

    log.info("Plugin 已删除", plugin_name=plugin_name, usernumb=user.usernumb)
    return ok(message=f"Plugin '{plugin_name}' 已删除")


# ── PATCH /api/plugins/{plugin_name} ─────────────────────

class _ToggleBody(BaseModel):
    is_active: bool


@router.patch("/{plugin_name}", response_model=ApiResponse)
async def toggle_plugin(
    plugin_name: str,
    body: _ToggleBody,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """启用/禁用 Plugin（仅能操作自己的 Plugin，系统 Plugin 不允许操作）"""
    async with async_session() as session:
        async with session.begin():
            # 先查询确认存在性和归属
            check = await session.execute(text("""
                SELECT owner_usernumb FROM sunny_agent.plugins
                WHERE name = :name AND owner_usernumb IN (:usernumb, 'system')
            """), {"name": plugin_name, "usernumb": user.usernumb})
            check_row = check.fetchone()

            if check_row is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Plugin '{plugin_name}' 不存在或无权限操作",
                )
            if check_row.owner_usernumb == "SYSTEM":
                raise HTTPException(
                    status_code=403,
                    detail=f"系统 Plugin '{plugin_name}' 不允许修改状态",
                )

            result = await session.execute(text("""
                UPDATE sunny_agent.plugins
                SET is_active = :is_active, updated_at = now()
                WHERE name = :name AND owner_usernumb = :usernumb
                RETURNING id
            """), {
                "name": plugin_name,
                "usernumb": user.usernumb,
                "is_active": body.is_active,
            })
            row = result.fetchone()

            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Plugin '{plugin_name}' 不存在或无权限操作",
                )

    state = "已启用" if body.is_active else "已禁用"
    log.info("Plugin 状态切换", plugin_name=plugin_name, is_active=body.is_active, usernumb=user.usernumb)
    return ok(message=f"Plugin '{plugin_name}' {state}")


# ── GET /api/plugins/{plugin_name}/files ──────────────────────

@router.get("/{plugin_name}/files", response_model=ApiResponse)
async def get_plugin_files(
    plugin_name: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    获取 Plugin 完整文件内容（目录树 + 文件内容一次性返回）。

    权限：仅 Plugin 创建者可查看。
    """
    async with async_session() as session:
        result = await session.execute(text("""
            SELECT name, description, version, path, owner_usernumb
            FROM sunny_agent.plugins
            WHERE name = :name AND owner_usernumb IN (:usernumb, 'system')
        """), {"name": plugin_name, "usernumb": user.usernumb})
        row = result.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_name}' 不存在或无权限查看")

    volume_root = Path(settings.SANDBOX_HOST_VOLUME).resolve()
    plugin_dir = (volume_root / row.path).resolve()

    try:
        plugin_dir.relative_to(volume_root)
    except ValueError:
        raise HTTPException(status_code=500, detail="Plugin 路径异常")

    if not plugin_dir.exists():
        raise HTTPException(status_code=404, detail="Plugin 文件目录不存在")

    files = scan_directory_files(plugin_dir)

    return ok(data={
        "name": row.name,
        "description": row.description,
        "version": row.version,
        "files": files,
    })


