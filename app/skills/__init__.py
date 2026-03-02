"""
Skill 编排框架（DB 驱动版本）

Skill 以 SKILL.md 文件定义，始终采用 pull 模式：
- 元数据（name, description, path）来自数据库，每次请求动态加载
- body（Markdown 正文）→ LLM 通过 read_file 按需拉取
- scripts/（可执行脚本）→ LLM 通过 bash_tool 直接执行

新增 Skill：将 SKILL.md 目录放入 volume，在 skills 表插入一条记录即可，无需重启服务。
"""

from app.skills.service import SkillInfo, skill_service

__all__ = [
    "SkillInfo",
    "skill_service",
]
