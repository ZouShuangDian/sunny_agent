"""
Markdown-Based Skill 加载器

MarkdownSkill 数据类：承载从 skill.md 解析出的所有信息
- Tier 1：frontmatter（name, description）→ skill_call 工具目录（始终加载）
- Tier 2：body（Markdown 正文）→ Skill 被调用时注入为 tool result，LLM 读取后自主 ReAct 执行
- Tier 3：scripts/ 下的 .py 文件 → 白名单记录，由 skill_exec 按需执行

skill.md 格式：
    ---
    name: github
    description: 通过 GitHub API 搜索仓库、用户和趋势项目
    timeout_ms: 120000
    ---

    # Skill 正文（Tier 2）
    ...
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path

import structlog
import yaml

log = structlog.get_logger()


# ── ScriptTool：Tier 3 脚本工具信息 ──

@dataclass
class ScriptTool:
    """
    scripts/ 下单个脚本的信息（Tier 3）。

    命名规则：{skill_name}_{script_stem}
    例：skill_name="github"，脚本 "search_repos.py" → tool_name="github_search_repos"
    """
    tool_name: str       # 白名单校验用的名称
    script_path: Path    # 脚本绝对路径
    description: str = ""  # 从脚本模块 docstring 提取


# ── MarkdownSkill 数据类 ──

@dataclass
class MarkdownSkill:
    """
    从 skill.md 解析出的 Skill 完整定义。

    Tier 1（metadata）：name, description → 供 SkillCallTool 构建工具目录
    Tier 2（body）：body_md → Skill 被调用时注入为 tool result，LLM 读取后自主 ReAct 执行
    Tier 3（scripts）：script_tools → 白名单记录，SkillExecTool 校验后执行
    """
    name: str
    description: str
    body_md: str
    skill_dir: Path
    script_tools: list[ScriptTool] = field(default_factory=list)
    timeout_ms: int = 60_000

    def render_tool_result(self) -> str:
        """
        Tier 2 注入：生成注入给 LLM 的 tool result 字符串。

        LLM 接收到这段文字后，通过正常 ReAct 循环自主调用 web_search / web_fetch 等工具，
        按照 skill body 的指令完成多步任务。LLM 根据用户原始请求和 body 指令自行决定参数。
        """
        return (
            f"[Skill 执行指令 - {self.name}]\n\n"
            f"---\n\n"
            f"{self.body_md}"
        )


# ── 解析工具函数 ──

def _extract_script_description(script_path: Path) -> str:
    """从脚本文件的模块 docstring 提取第一行描述"""
    try:
        source = script_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        if (
            tree.body
            and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)
            and isinstance(tree.body[0].value.value, str)
        ):
            return tree.body[0].value.value.strip().split("\n")[0]
    except Exception:
        pass
    return ""


def load_skill_from_dir(skill_dir: Path) -> MarkdownSkill | None:
    """
    从单个 Skill 目录加载 MarkdownSkill。

    目录结构：
        skill_dir/
        ├── skill.md         ← 必须存在（frontmatter: name + description，body: 操作手册）
        └── scripts/         ← 可选，.py 文件加入白名单，由 skill_exec 执行

    Returns:
        MarkdownSkill 或 None（目录不合法时）
    """
    skill_md_path = skill_dir / "skill.md"
    if not skill_md_path.exists():
        log.debug("跳过非 Skill 目录（缺少 skill.md）", dir=str(skill_dir))
        return None

    raw = skill_md_path.read_text(encoding="utf-8")

    # 分割 frontmatter 和 body：格式为 ---\n{yaml}\n---\n{body}
    if not raw.startswith("---"):
        log.warning("skill.md 缺少 YAML frontmatter（---）", path=str(skill_md_path))
        return None

    parts = raw.split("---", 2)
    # parts[0]="" (---之前), parts[1]=frontmatter yaml, parts[2]=body
    if len(parts) < 3:
        log.warning("skill.md frontmatter 格式错误，缺少结束 ---", path=str(skill_md_path))
        return None

    frontmatter_raw = parts[1]
    body_md = parts[2].strip()

    try:
        fm = yaml.safe_load(frontmatter_raw) or {}
    except yaml.YAMLError as e:
        log.error("skill.md frontmatter YAML 解析失败", path=str(skill_md_path), error=str(e))
        return None

    # 必填字段校验
    name = fm.get("name")
    description = fm.get("description")
    if not name or not description:
        log.error(
            "skill.md 缺少必填字段 name/description",
            path=str(skill_md_path),
            found_fields=list(fm.keys()),
        )
        return None

    # 扫描 scripts/ 目录（Tier 3），加入白名单
    script_tools: list[ScriptTool] = []
    scripts_dir = skill_dir / "scripts"
    if scripts_dir.exists():
        for py_file in sorted(scripts_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue  # 跳过 __init__.py 等内部文件
            tool_name = f"{name}_{py_file.stem}"
            desc = _extract_script_description(py_file)
            script_tools.append(ScriptTool(
                tool_name=tool_name,
                script_path=py_file,
                description=desc,
            ))
            log.debug("发现 Skill 脚本（加入白名单）", tool_name=tool_name)

    timeout_ms = int(fm.get("timeout_ms", 60_000))

    skill = MarkdownSkill(
        name=str(name),
        description=str(description),
        body_md=body_md,
        skill_dir=skill_dir,
        script_tools=script_tools,
        timeout_ms=timeout_ms,
    )

    log.info(
        "Skill 已加载",
        name=name,
        script_count=len(script_tools),
        body_lines=len(body_md.splitlines()),
    )
    return skill


def scan_skills_dir(skills_root: Path) -> list[MarkdownSkill]:
    """
    扫描 builtin_skills 目录，加载所有合法的 MarkdownSkill。

    每个包含 skill.md 的子目录都被视为一个 Skill。
    """
    if not skills_root.exists():
        log.warning("Skills 根目录不存在", path=str(skills_root))
        return []

    skills: list[MarkdownSkill] = []
    for entry in sorted(skills_root.iterdir()):
        if entry.is_dir() and not entry.name.startswith("_"):
            skill = load_skill_from_dir(entry)
            if skill:
                skills.append(skill)

    log.info("Skills 扫描完成", count=len(skills), root=str(skills_root))
    return skills
