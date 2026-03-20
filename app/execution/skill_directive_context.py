"""
Skill 指令上下文 ContextVar — 用户通过 /skill:skillname 显式指定 Skill

与 plugin_context.py / mode_context.py 完全相同的 ContextVar 模式。

生命周期：
- 设置：chat.py _run_intent_pipeline() 检测到 /skill:xxx 时
- 读取：L3ReActEngine._build_initial_messages()，将 SKILL.md 内容注入 system prompt
- 重置：chat.py finally 块精确还原

与 skill_call 工具的区别：
- skill_call：LLM 自主决策是否调用 Skill（间接、pull 模式）
- /skill:xxx：用户显式指定，SKILL.md 直接注入 system prompt（直接、push 模式）
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass
class SkillDirectiveContext:
    """用户通过 /skill:skillname 显式指定的 Skill 上下文"""

    skill_name: str
    # SKILL.md 完整内容（注入 system prompt）
    skill_md_content: str
    # scripts/ 目录在容器内的路径（如 /mnt/skills/pdf/scripts）
    scripts_dir: str
    # Skill 根目录在容器内的路径（如 /mnt/skills/pdf）
    skill_dir: str


_skill_directive_var: ContextVar[SkillDirectiveContext | None] = ContextVar(
    "skill_directive_ctx", default=None
)


def get_skill_directive_context() -> SkillDirectiveContext | None:
    """获取当前请求的 Skill 指令上下文（非 /skill:xxx 请求返回 None）"""
    return _skill_directive_var.get()


def set_skill_directive_context(ctx: SkillDirectiveContext) -> Token:
    """设置 Skill 指令上下文，返回 Token（用于精确还原）"""
    return _skill_directive_var.set(ctx)


def reset_skill_directive_context(token: Token) -> None:
    """精确还原 ContextVar 到 set 前的状态"""
    _skill_directive_var.reset(token)
