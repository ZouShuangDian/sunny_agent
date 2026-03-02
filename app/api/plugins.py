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
import re
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.db.engine import async_session
from app.plugins.service import _parse_frontmatter, plugin_service
from app.security.auth import AuthenticatedUser, get_current_user

router = APIRouter(prefix="/api/plugins", tags=["Plugin 管理"])
log = structlog.get_logger()
settings = get_settings()

# plugin name 合法格式：小写字母开头，只含小写字母/数字/连字符，最长 63 字符
_PLUGIN_NAME_RE = re.compile(r'^[a-z][a-z0-9-]{0,62}$')


# ── 工具函数 ──────────────────────────────────────────────────

def _check_zip_safety(zf: zipfile.ZipFile) -> None:
    """
    校验 ZIP 成员路径安全性。
    拒绝：含 ".."、以 "/" 开头、含 ":" 或 "\\」（Windows 路径）的成员。
    """
    for info in zf.infolist():
        name = info.filename
        # 过滤目录条目（以 / 结尾）
        if name.endswith("/"):
            continue
        if ".." in name.split("/"):
            raise HTTPException(status_code=400, detail=f"ZIP 含路径穿越成员：{name}")
        if name.startswith("/") or name.startswith("\\"):
            raise HTTPException(status_code=400, detail=f"ZIP 含绝对路径成员：{name}")
        if ":" in name:
            raise HTTPException(status_code=400, detail=f"ZIP 成员路径含非法字符：{name}")


def _find_zip_root(zf: zipfile.ZipFile) -> str | None:
    """
    检测 ZIP 是否有统一根目录（所有文件都在同一个顶级目录下）。
    返回根目录名（含末尾 "/"），或 None（无根目录，文件在 ZIP 根部）。
    """
    names = [info.filename for info in zf.infolist() if not info.filename.endswith("/")]
    if not names:
        return None
    first_parts = {n.split("/")[0] for n in names}
    if len(first_parts) == 1:
        # 所有文件共享同一个顶级目录
        root = first_parts.pop()
        return root + "/"
    return None


def _validate_plugin_json(plugin_json_path: Path, usernumb: str) -> dict:
    """
    解析并校验 plugin.json。
    返回 plugin metadata dict。
    抛 HTTPException 如果不合法。
    """
    if not plugin_json_path.exists():
        raise HTTPException(status_code=400, detail="ZIP 缺少 .claude-plugin/plugin.json")

    try:
        data = json.loads(plugin_json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"plugin.json JSON 格式错误：{e}")

    # 必填字段
    required = ["name", "version", "description"]
    for field in required:
        if not data.get(field):
            raise HTTPException(status_code=400, detail=f"plugin.json 缺少必填字段：{field}")

    # author.usernumb 必须与登录用户一致
    author = data.get("author", {})
    if not isinstance(author, dict) or not author.get("usernumb"):
        raise HTTPException(status_code=400, detail="plugin.json 缺少 author.usernumb 字段")

    if author["usernumb"] != usernumb:
        raise HTTPException(
            status_code=403,
            detail=f"author.usernumb（{author['usernumb']}）与登录用户（{usernumb}）不符",
        )

    # name 格式校验
    name = data["name"]
    if not _PLUGIN_NAME_RE.match(name):
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
        fm, _ = _parse_frontmatter(content)

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

@router.post("/upload")
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
            _check_zip_safety(zf)
            zf.extractall(extract_dir)

        # 3. 找到实际插件根目录（自动处理有/无顶级目录两种打包方式）
        with zipfile.ZipFile(zip_tmp, "r") as zf:
            zip_root = _find_zip_root(zf)

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

        # 7. DB 写入（事务）
        async with async_session() as session:
            async with session.begin():
                # 查询是否已存在同名 Plugin
                existing = await session.execute(text("""
                    SELECT id FROM sunny_agent.plugins
                    WHERE owner_usernumb = :usernumb AND name = :name
                """), {"usernumb": user.usernumb, "name": plugin_name})
                row = existing.fetchone()

                if row:
                    plugin_id = row.id
                    # 更新 Plugin 记录
                    await session.execute(text("""
                        UPDATE sunny_agent.plugins
                        SET version = :version,
                            description = :description,
                            path = :path,
                            is_active = TRUE,
                            updated_at = now()
                        WHERE id = :plugin_id
                    """), {
                        "version": plugin_data["version"],
                        "description": plugin_data["description"],
                        "path": plugin_rel_path,
                        "plugin_id": plugin_id,
                    })
                    # 删除旧命令（重新插入）
                    await session.execute(text("""
                        DELETE FROM sunny_agent.plugin_commands WHERE plugin_id = :plugin_id
                    """), {"plugin_id": plugin_id})
                    log.info("Plugin 已更新", plugin_name=plugin_name, usernumb=user.usernumb)
                else:
                    plugin_id = uuid.uuid4()
                    # 插入新 Plugin 记录
                    await session.execute(text("""
                        INSERT INTO sunny_agent.plugins
                            (id, name, version, description, owner_usernumb, path, is_active)
                        VALUES
                            (:id, :name, :version, :description, :owner_usernumb, :path, TRUE)
                    """), {
                        "id": plugin_id,
                        "name": plugin_name,
                        "version": plugin_data["version"],
                        "description": plugin_data["description"],
                        "owner_usernumb": user.usernumb,
                        "path": plugin_rel_path,
                    })
                    log.info("Plugin 已注册", plugin_name=plugin_name, usernumb=user.usernumb)

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

        return {
            "plugin": plugin_name,
            "version": plugin_data["version"],
            "description": plugin_data["description"],
            "commands": [
                {"name": c["name"], "description": c["description"]}
                for c in commands
            ],
        }

    finally:
        # 始终清理临时目录
        if tmpdir.exists():
            shutil.rmtree(tmpdir, ignore_errors=True)


# ── GET /api/plugins/list ─────────────────────────────────────

@router.get("/list")
async def list_plugins(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """列出当前用户所有 Plugin（含命令数）"""
    plugins = await plugin_service.list_user_plugins(user.usernumb)
    return {"plugins": plugins, "total": len(plugins)}


# ── DELETE /api/plugins/{plugin_name} ────────────────────────

@router.delete("/{plugin_name}")
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
                SELECT id, path FROM sunny_agent.plugins
                WHERE name = :name AND owner_usernumb = :usernumb
            """), {"name": plugin_name, "usernumb": user.usernumb})
            row = result.fetchone()

            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Plugin '{plugin_name}' 不存在或无权限删除",
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
    return {"deleted": plugin_name}
