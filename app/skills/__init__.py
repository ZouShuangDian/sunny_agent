"""
Skill 编排框架（Markdown-Based 版本，M08-5）

Skill 以 Markdown 文件定义，对齐 Claude Code 官方 Skill 格式：
- Tier 1：frontmatter（name, description, parameters）→ 始终加载，生成 function calling schema
- Tier 2：body（Markdown 正文）→ Skill 被调用时注入为 tool result，LLM 自主 ReAct 执行
- Tier 3：scripts/（可执行脚本）→ 自动注册为 ToolRegistry 中的子工具

新增 Skill 只需在 builtin_skills/ 下创建目录 + skill.md，无需修改任何 Python 代码。
"""

from app.skills.loader import MarkdownSkill, ScriptTool, load_skill_from_dir, scan_skills_dir
from app.skills.registry import SkillRegistry

__all__ = [
    "MarkdownSkill",
    "ScriptTool",
    "SkillRegistry",
    "load_skill_from_dir",
    "scan_skills_dir",
]
