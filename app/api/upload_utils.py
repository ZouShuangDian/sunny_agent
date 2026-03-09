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
    """
    names = [info.filename for info in zf.infolist() if not info.filename.endswith("/")]
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


def validate_name(name: str, label: str = "name") -> None:
    """校验 name 格式，不合法则抛 400"""
    if not name:
        raise HTTPException(status_code=400, detail=f"{label} 不能为空")
    if not NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail=f"{label} 格式不合法（期望 ^[a-z][a-z0-9-]{{0,62}}$）：{name}",
        )
