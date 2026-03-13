"""
上传公共工具函数：ZIP 安全检查、根目录检测、frontmatter 解析、name 格式校验

供 plugins.py 和 skills.py 共用。
"""

import re
import zipfile

from fastapi import HTTPException

# name 合法格式：小写字母开头，只含小写字母/数字/连字符，最长 63 字符
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")


def check_zip_safety(zf: zipfile.ZipFile) -> None:
    """
    校验 ZIP 成员路径安全性。
    拒绝：含 ".."、以 "/" 开头、含 ":" 或 "\\" 的成员。
    """
    for info in zf.infolist():
        name = info.filename
        if name.endswith("/"):
            continue
        if ".." in name.split("/"):
            raise HTTPException(status_code=400, detail=f"ZIP 含路径穿越成员：{name}")
        if name.startswith("/") or name.startswith("\\"):
            raise HTTPException(status_code=400, detail=f"ZIP 含绝对路径成员：{name}")
        if ":" in name:
            raise HTTPException(status_code=400, detail=f"ZIP 成员路径含非法字符：{name}")


def find_zip_root(zf: zipfile.ZipFile) -> str | None:
    """
    检测 ZIP 是否有统一根目录（所有文件都在同一个顶级目录下）。
    返回根目录名（含末尾 "/"），或 None（无根目录，文件在 ZIP 根部）。

    自动忽略 Mac 压缩工具生成的 __MACOSX 目录。
    """
    names = [
        info.filename for info in zf.infolist()
        if not info.filename.endswith("/")
        and not info.filename.startswith("__MACOSX/")
    ]
    if not names:
        return None
    first_parts = {n.split("/")[0] for n in names}
    if len(first_parts) == 1:
        root = first_parts.pop()
        return root + "/"
    return None


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """
    解析 Markdown frontmatter（YAML 块 between ---）。

    返回 (fm_dict, body_text)。
    若无合法 frontmatter，返回 ({}, content)。
    """
    if not content.startswith("---"):
        return {}, content

    end = content.find("\n---", 3)
    if end == -1:
        return {}, content

    fm_text = content[3:end].strip()
    body = content[end + 4 :].strip()

    fm: dict = {}
    for line in fm_text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip().strip('"').strip("'")

    return fm, body


def scan_directory_files(root_dir: "Path", *, max_file_size: int = 512_000) -> list[dict]:
    """
    递归扫描目录，返回所有文件的路径和内容。

    跳过隐藏文件/目录（. 开头）和 __pycache__。
    文本文件返回 content 字符串，超大文件或二进制文件只返回路径不含 content。

    返回格式：[{"path": "scripts/search.py", "type": "file", "content": "..."}]
    """
    from pathlib import Path

    root = Path(root_dir).resolve()
    files = []

    # 跳过的目录名
    skip_dirs = {"__pycache__", ".git", "node_modules"}

    for item in sorted(root.rglob("*")):
        if not item.is_file():
            continue

        # 跳过隐藏文件和特定目录
        rel = item.relative_to(root)
        parts = rel.parts
        if any(p.startswith(".") or p in skip_dirs for p in parts):
            continue

        entry: dict = {"path": str(rel), "type": "file"}

        # 尝试读取文本内容（小文件）
        if item.stat().st_size <= max_file_size:
            try:
                entry["content"] = item.read_text(encoding="utf-8")
            except (UnicodeDecodeError, ValueError):
                entry["content"] = None  # 二进制文件
                entry["binary"] = True
        else:
            entry["content"] = None
            entry["truncated"] = True

        files.append(entry)

    return files


def validate_name(name: str, label: str = "name") -> None:
    """校验 name 格式，不合法则抛 400"""
    if not name:
        raise HTTPException(status_code=400, detail=f"{label} 不能为空")
    if not NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail=f"{label} 格式不合法（期望 ^[a-z][a-z0-9-]{{0,62}}$）：{name}",
        )
