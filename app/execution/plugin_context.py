"""
Plugin 命令上下文 ContextVar

与 skill_context.py / session_context.py / user_context.py 完全相同的 ContextVar 模式。

生命周期：
- 设置：chat.py _handle_plugin_command() 入口处，execution_router.execute() 前
- 读取：L3ReActEngine._build_initial_messages()，注入 COMMAND.md + Skills 列表到 system prompt
- 重置：chat.py finally 块精确还原（不影响并发请求）

SubAgent 隔离：execute_raw() 不设置此 ContextVar，SubAgent 读取到 None，不注入 Plugin 上下文。
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field


@dataclass
class PluginCommandContext:
    """当前请求正在执行的 Plugin 命令上下文"""

    plugin_name: str
    command_name: str
    # COMMAND.md 文件完整内容（注入 system prompt 的工作流指引）
    command_md_content: str
    # 插件内可用 Skill 列表
    # 格式：[{"name": str, "skill_md_path": "/mnt/.../skills/{name}/SKILL.md"}]
    plugin_skills: list[dict] = field(default_factory=list)


_plugin_ctx_var: ContextVar[PluginCommandContext | None] = ContextVar(
    "plugin_command_ctx", default=None
)


def get_plugin_context() -> PluginCommandContext | None:
    """获取当前请求的 Plugin 命令上下文（无 Plugin 命令时返回 None）"""
    return _plugin_ctx_var.get()


def set_plugin_context(ctx: PluginCommandContext) -> Token:
    """设置 Plugin 命令上下文，返回 Token（用于精确还原）"""
    return _plugin_ctx_var.set(ctx)


def reset_plugin_context(token: Token) -> None:
    """精确还原 ContextVar 到 set 前的状态"""
    _plugin_ctx_var.reset(token)
