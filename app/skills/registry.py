"""
Skill 注册中心（Markdown-Based 版本）

核心设计：单一 skill_call 元工具
- 不再将每个 Skill 注册为独立 Tool（避免 N Skill → N Tool，撑大 context）
- 只维护内存中的 Skill 索引（name → MarkdownSkill）
- get_catalog() 供 SkillCallTool 动态构建工具描述和 enum

多目录加载：
- from_directories([builtin_dir, user_dir]) 按顺序扫描
- 同名 Skill 后加载的覆盖先加载的（用户目录 > 内置目录）

Tier 3 脚本执行模型：
- 脚本不注册为全局 Tool，不出现在 LLM 的 function schema 中
- LLM 必须先调用 skill_call 读取 Skill body，才能调用 skill_exec 执行脚本
- SkillRegistry 通过 get_script_path / get_script_names 提供白名单查询接口给 SkillExecTool
"""

from __future__ import annotations

from pathlib import Path

import structlog

from app.skills.loader import MarkdownSkill, scan_skills_dir

log = structlog.get_logger()


class SkillRegistry:
    """Skill 注册中心（Markdown-Based）"""

    def __init__(self):
        self._skills: dict[str, MarkdownSkill] = {}

    @classmethod
    def from_directory(cls, skills_root: Path) -> "SkillRegistry":
        """工厂方法：从单个目录扫描创建 SkillRegistry（向后兼容）"""
        return cls.from_directories([skills_root])

    @classmethod
    def from_directories(cls, skill_dirs: list[Path]) -> "SkillRegistry":
        """
        工厂方法：从多个目录扫描创建 SkillRegistry。

        按顺序加载，同名 Skill 后加载的覆盖先加载的。
        典型用法：from_directories([builtin_dir, user_dir])
        - builtin_dir：项目内置 Skills
        - user_dir：~/.sunny-agent/skills/（用户自定义，优先级更高）
        """
        registry = cls()
        for skill_dir in skill_dirs:
            if not skill_dir.exists():
                log.debug("Skill 目录不存在，跳过", path=str(skill_dir))
                continue
            skills = scan_skills_dir(skill_dir)
            for skill in skills:
                registry.register(skill)
        return registry

    def register(self, skill: MarkdownSkill) -> None:
        """
        注册单个 MarkdownSkill。

        同名 Skill 会被覆盖（后注册优先，支持用户 override 内置）。
        脚本信息保留在 MarkdownSkill.script_tools 中作为白名单，不注册为 Tool。
        """
        if skill.name in self._skills:
            log.info("Skill 同名覆盖", skill=skill.name, tip="用户目录 Skill 覆盖内置 Skill")

        self._skills[skill.name] = skill

        log.debug(
            "Skill 已注册",
            skill=skill.name,
            scripts=[st.tool_name for st in skill.script_tools],
        )

    def has_skill(self, name: str) -> bool:
        return name in self._skills

    def get_script_path(self, skill_name: str, script_name: str) -> Path | None:
        """
        返回指定 Skill 下指定脚本的绝对路径（白名单查询，供 SkillExecTool 使用）。

        Args:
            skill_name: 已注册的 Skill 名称
            script_name: 脚本文件名（不含 .py 后缀），例 "search_repos"

        Returns:
            脚本绝对路径；Skill 不存在或脚本不在白名单内时返回 None
        """
        skill = self._skills.get(skill_name)
        if not skill:
            return None
        expected_tool_name = f"{skill_name}_{script_name}"
        for st in skill.script_tools:
            if st.tool_name == expected_tool_name:
                return st.script_path
        return None

    def get_script_names(self, skill_name: str) -> list[str] | None:
        """
        返回指定 Skill 下所有合法脚本名（不含 .py 后缀）列表。

        Returns:
            脚本名列表（可为空列表）；Skill 不存在时返回 None
        """
        skill = self._skills.get(skill_name)
        if not skill:
            return None
        prefix = f"{skill_name}_"
        return [st.tool_name.removeprefix(prefix) for st in skill.script_tools]

    def get_skill_timeout_s(self, skill_name: str) -> float:
        """返回指定 Skill 的超时时间（秒），Skill 不存在时返回默认值 60s"""
        skill = self._skills.get(skill_name)
        return (skill.timeout_ms / 1000) if skill else 60.0

    def get_catalog(self) -> list[tuple[str, str]]:
        """
        返回所有已注册 Skill 的目录，每项为 (name, description) 二元组。

        供 SkillCallTool 动态构建工具描述和 skill_name enum。
        """
        return [
            (skill.name, skill.description)
            for skill in self._skills.values()
        ]

    def execute(self, name: str) -> str:
        """
        Prompt-Driven 执行：注入 Tier 2 body 给 LLM，让 LLM 自主 ReAct。

        此方法是同步的（无 IO，只做字符串拼接）。

        Args:
            name: Skill 名称

        Returns:
            tool result 字符串（供 SkillCallTool.execute() 包装后返回）
        """
        skill = self._skills.get(name)
        if not skill:
            return f"错误：未知 Skill '{name}'"

        return skill.render_tool_result()

    @property
    def skill_names(self) -> list[str]:
        return list(self._skills.keys())

    @property
    def skill_count(self) -> int:
        return len(self._skills)
