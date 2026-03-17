"""
内置模式上下文 ContextVar

与 plugin_context.py 完全相同的 ContextVar 模式。

生命周期：
- 设置：chat.py _run_intent_pipeline() 检测到 /mode:xxx 时
- 读取：L3ReActEngine._build_initial_messages()，注入模式专用 prompt 到 system prompt
       L3ReActEngine._build_context()，按 allowed_tools 过滤工具集
- 重置：chat.py finally 块精确还原

模式注册表在 app/modes/__init__.py 中维护，本模块不关心具体模式定义。
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass
class ModeConfig:
    """内置模式静态配置"""

    system_prompt_block: str            # 模式专用 system prompt
    allowed_tools: list[str] | None     # 工具白名单（None = 不限制，使用全量工具）
    override_system_prompt: bool = False  # True = 替换 L3 基础 prompt；False = 追加到基础 prompt 后


@dataclass
class ModeContext:
    """当前请求的内置模式上下文"""

    mode_name: str
    # 用户在 /mode:xxx 之后输入的内容（去掉前缀后的部分）
    user_input: str
    # 模式专用 system prompt
    system_prompt_block: str
    # 工具白名单（从 ModeConfig 透传）
    allowed_tools: list[str] | None = None
    # True = 替换 L3 基础 prompt（从 ModeConfig 透传）
    override_system_prompt: bool = False


_mode_ctx_var: ContextVar[ModeContext | None] = ContextVar(
    "mode_ctx", default=None
)


def get_mode_context() -> ModeContext | None:
    """获取当前请求的模式上下文（非模式请求返回 None）"""
    return _mode_ctx_var.get()


def set_mode_context(ctx: ModeContext) -> Token:
    """设置模式上下文，返回 Token（用于精确还原）"""
    return _mode_ctx_var.set(ctx)


def reset_mode_context(token: Token) -> None:
    """精确还原 ContextVar 到 set 前的状态"""
    _mode_ctx_var.reset(token)
