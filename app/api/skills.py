"""
/api/skills — Skill 管理接口

端点：
- POST   /api/skills/upload       — 上传 ZIP 包，解析 SKILL.md，注册到 DB + volume
- GET    /api/skills/list          — 列出当前用户所有可见 Skill（含启用状态）
- DELETE /api/skills/{skill_name}  — 删除个人 Skill（DB + volume）
- PATCH  /api/skills/{skill_name}  — 开关 Skill（user_skill_settings UPSERT）

安全原则：
- ZIP 路径穿越防护（与 Plugin 共用 upload_utils）
- 用户隔离：只能覆盖/删除自己的 user Skill，系统 Skill 不可覆盖/删除
- 文件原子写入：先写临时目录 → DB 成功 → rename 替换
"""

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
from app.api.upload_utils import check_zip_safety, find_zip_root, parse_frontmatter, validate_name
from app.config import get_settings
from app.db.engine import async_session
from app.security.auth import AuthenticatedUser, get_current_user

router = APIRouter(prefix="/api/skills", tags=["Skill 管理"])
log = structlog.get_logger()
settings = get_settings()


# ── POST /api/skills/upload ──────────────────────────────────

@router.post("/upload", response_model=ApiResponse)
async def upload_skill(
    file: UploadFile,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    上传 Skill ZIP 包，解析 SKILL.md frontmatter，注册到 DB + volume。

    ZIP 内目录结构（支持有无顶级根目录两种打包方式）：
    {skill-name}/              ← 根目录（可选）
    ├── SKILL.md               ← 必须，frontmatter 含 name + description
    └── ...                    ← 其余文件不限，原样保留
    """
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="只接受 .zip 格式文件")

    tmpdir = Path(tempfile.mkdtemp(prefix="sunny_skill_"))
    try:
        # 1. 写入临时文件
        zip_tmp = tmpdir / "upload.zip"
        zip_tmp.write_bytes(await file.read())

        # 2. 解压 + 路径安全检查 + 根目录检测（单次打开）
        extract_dir = tmpdir / "extracted"
        extract_dir.mkdir()

        with zipfile.ZipFile(zip_tmp, "r") as zf:
            check_zip_safety(zf)
            zip_root = find_zip_root(zf)
            zf.extractall(extract_dir)

        # 3. 找到实际 Skill 根目录

        if zip_root:
            skill_src = extract_dir / zip_root.rstrip("/")
        else:
            skill_src = extract_dir

        # 4. 解析 SKILL.md
        skill_md = skill_src / "SKILL.md"
        if not skill_md.exists():
            raise HTTPException(status_code=400, detail="ZIP 缺少 SKILL.md 文件")

        content = skill_md.read_text(encoding="utf-8")
        fm, _ = parse_frontmatter(content)

        skill_name = (fm.get("name") or "").strip()
        description = (fm.get("description") or "").strip()

        # 5. 校验
        validate_name(skill_name, label="Skill name")
        if not description:
            raise HTTPException(status_code=400, detail="SKILL.md frontmatter 缺少 description 字段")

        # 6. 检测 has_scripts
        has_scripts = (skill_src / "scripts").is_dir()

        # 7. 计算目标路径
        volume_root = Path(settings.SANDBOX_HOST_VOLUME).resolve()
        skill_rel_path = f"users/{user.usernumb}/skills/{skill_name}"
        target_dir = (volume_root / skill_rel_path).resolve()

        # 路径穿越防护
        try:
            target_dir.relative_to(volume_root)
        except ValueError:
            raise HTTPException(status_code=400, detail="目标路径越界，拒绝写入")

        # 8. 前置检查 + DB UPSERT（同一个事务，消除竞态窗口）
        async with async_session() as session:
            async with session.begin():
                # 前置检查：同名 Skill 归属
                existing = await session.execute(text("""
                    SELECT scope, owner_usernumb FROM sunny_agent.skills
                    WHERE name = :name
                """), {"name": skill_name})
                row = existing.fetchone()

                if row:
                    if row.scope == "system":
                        raise HTTPException(
                            status_code=403,
                            detail=f"Skill '{skill_name}' 是系统 Skill，不允许覆盖",
                        )
                    if row.owner_usernumb != user.usernumb:
                        raise HTTPException(
                            status_code=403,
                            detail=f"Skill 名称 '{skill_name}' 已被其他用户占用",
                        )

                # UPSERT skills 表
                skill_id = uuid.uuid4()
                result = await session.execute(text("""
                    INSERT INTO sunny_agent.skills
                        (id, name, description, path, scope, owner_usernumb,
                         is_active, is_default_enabled, has_scripts)
                    VALUES
                        (:id, :name, :description, :path, 'user', :owner_usernumb,
                         TRUE, FALSE, :has_scripts)
                    ON CONFLICT (name) DO UPDATE SET
                        description = EXCLUDED.description,
                        path = EXCLUDED.path,
                        has_scripts = EXCLUDED.has_scripts,
                        is_active = TRUE,
                        updated_at = now()
                    RETURNING id, (xmax = 0) AS is_new
                """), {
                    "id": skill_id,
                    "name": skill_name,
                    "description": description,
                    "path": skill_rel_path,
                    "owner_usernumb": user.usernumb,
                    "has_scripts": has_scripts,
                })
                row = result.fetchone()
                skill_id = row.id
                is_new = row.is_new

                # UPSERT user_skill_settings（首次默认启用）
                await session.execute(text("""
                    INSERT INTO sunny_agent.user_skill_settings
                        (usernumb, skill_id, is_enabled)
                    VALUES
                        (:usernumb, :skill_id, TRUE)
                    ON CONFLICT (usernumb, skill_id) DO NOTHING
                """), {
                    "usernumb": user.usernumb,
                    "skill_id": skill_id,
                })

        # 9. DB 成功后：写文件 + 原子替换
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        tmp_target = target_dir.with_name(f"{skill_name}.tmp_{uuid.uuid4().hex[:8]}")
        shutil.copytree(str(skill_src), str(tmp_target))

        old_backup = None
        if target_dir.exists():
            old_backup = target_dir.with_name(f"{skill_name}.old_{uuid.uuid4().hex[:8]}")
            target_dir.rename(old_backup)
        tmp_target.rename(target_dir)
        if old_backup and old_backup.exists():
            shutil.rmtree(old_backup, ignore_errors=True)

        log.info(
            "Skill 上传完成",
            skill_name=skill_name,
            usernumb=user.usernumb,
            is_new=is_new,
            has_scripts=has_scripts,
        )

        message = "Skill 上传成功" if is_new else "Skill 已更新（覆盖同名）"
        return ok(
            data={
                "skill": skill_name,
                "description": description,
                "has_scripts": has_scripts,
                "is_new": is_new,
            },
            message=message,
            status_code=201,
        )

    finally:
        if tmpdir.exists():
            shutil.rmtree(tmpdir, ignore_errors=True)


# ── GET /api/skills/list ─────────────────────────────────────

@router.get("/list", response_model=ApiResponse)
async def list_skills(
    user: AuthenticatedUser = Depends(get_current_user),
):
    """列出当前用户所有可见 Skill（系统 + 个人），含启用状态"""
    async with async_session() as session:
        result = await session.execute(text("""
            SELECT s.id, s.name, s.description, s.scope, s.path,
                   s.is_default_enabled, s.has_scripts,
                   COALESCE(uss.is_enabled,
                       CASE WHEN s.scope = 'user' THEN TRUE
                            ELSE s.is_default_enabled
                       END
                   ) AS is_enabled
            FROM sunny_agent.skills s
            LEFT JOIN sunny_agent.user_skill_settings uss
                ON uss.skill_id = s.id AND uss.usernumb = :usernumb
            WHERE s.is_active = TRUE
              AND (
                  (s.scope = 'user' AND s.owner_usernumb = :usernumb)
                  OR s.scope = 'system'
              )
            ORDER BY s.scope DESC, s.name ASC
        """), {"usernumb": user.usernumb})
        rows = result.fetchall()

    skills = [
        {
            "name": row.name,
            "description": row.description,
            "scope": row.scope,
            "is_enabled": row.is_enabled,
            "is_default_enabled": row.is_default_enabled,
            "has_scripts": row.has_scripts,
        }
        for row in rows
    ]

    return ok(data={"skills": skills, "total": len(skills)})


# ── DELETE /api/skills/{skill_name} ──────────────────────────

@router.delete("/{skill_name}", response_model=ApiResponse)
async def delete_skill(
    skill_name: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    删除个人 Skill。
    1. 从 DB 删除 skills 记录（user_skill_settings 通过 CASCADE 自动删除）
    2. 从 volume 删除 Skill 目录
    """
    async with async_session() as session:
        async with session.begin():
            result = await session.execute(text("""
                DELETE FROM sunny_agent.skills
                WHERE name = :name
                  AND scope = 'user'
                  AND owner_usernumb = :usernumb
                RETURNING id, path
            """), {"name": skill_name, "usernumb": user.usernumb})
            row = result.fetchone()

            if row is None:
                # 区分不存在 vs 无权限
                check = await session.execute(text("""
                    SELECT scope FROM sunny_agent.skills WHERE name = :name
                """), {"name": skill_name})
                exists = check.fetchone()
                if exists is None:
                    raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' 不存在")
                if exists.scope == "system":
                    raise HTTPException(status_code=403, detail="系统 Skill 不允许删除")
                raise HTTPException(status_code=403, detail=f"Skill '{skill_name}' 不属于当前用户")

    # 删除 volume 目录（DB 已删除，文件清理失败仅告警）
    volume_root = Path(settings.SANDBOX_HOST_VOLUME).resolve()
    target_dir = (volume_root / row.path).resolve()

    try:
        target_dir.relative_to(volume_root)
        if target_dir.exists():
            shutil.rmtree(target_dir)
            log.info("Skill 目录已删除", skill_name=skill_name, path=row.path)
    except ValueError:
        log.warning("Skill 目录路径越界，跳过文件删除", path=row.path)
    except Exception:
        log.warning("Skill 目录删除失败", skill_name=skill_name, path=row.path, exc_info=True)

    log.info("Skill 已删除", skill_name=skill_name, usernumb=user.usernumb)
    return ok(message=f"Skill '{skill_name}' 已删除")


# ── PATCH /api/skills/{skill_name} ───────────────────────────

class _ToggleBody(BaseModel):
    is_enabled: bool


@router.patch("/{skill_name}", response_model=ApiResponse)
async def toggle_skill(
    skill_name: str,
    body: _ToggleBody,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """开关 Skill（系统和个人 Skill 都可操作）"""
    async with async_session() as session:
        async with session.begin():
            # 查询 Skill 是否存在且 active
            result = await session.execute(text("""
                SELECT id FROM sunny_agent.skills
                WHERE name = :name AND is_active = TRUE
            """), {"name": skill_name})
            row = result.fetchone()

            if row is None:
                raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' 不存在")

            # UPSERT 用户开关
            await session.execute(text("""
                INSERT INTO sunny_agent.user_skill_settings (usernumb, skill_id, is_enabled)
                VALUES (:usernumb, :skill_id, :is_enabled)
                ON CONFLICT (usernumb, skill_id) DO UPDATE SET
                    is_enabled = EXCLUDED.is_enabled,
                    updated_at = now()
            """), {
                "usernumb": user.usernumb,
                "skill_id": row.id,
                "is_enabled": body.is_enabled,
            })

    state = "已开启" if body.is_enabled else "已关闭"
    log.info("Skill 开关切换", skill_name=skill_name, is_enabled=body.is_enabled, usernumb=user.usernumb)
    return ok(message=f"Skill '{skill_name}' {state}")
