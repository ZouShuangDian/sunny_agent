"""
内置模式上下文 ContextVar

与 plugin_context.py 完全相同的 ContextVar 模式。

生命周期：
- 设置：chat.py _run_intent_pipeline() 检测到 /mode:xxx 时
- 读取：L3ReActEngine._build_initial_messages()，注入模式专用 prompt 到 system prompt
- 重置：chat.py finally 块精确还原

模式注册表：
- BUILTIN_MODES 定义所有内置模式（名称 → system prompt 注入块）
- Phase 1 仅 deep-research，后续按需扩展
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass
class ModeContext:
    """当前请求的内置模式上下文"""

    mode_name: str
    # 用户在 /mode:xxx 之后输入的内容（去掉前缀后的部分）
    user_input: str
    # 注入 system prompt 的模式专用指引
    system_prompt_block: str


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


# ── 内置模式注册表 ──

BUILTIN_MODES: dict[str, str] = {
    "deep-research": (
        "\n\n---\n## 深度研究模式\n\n"
        "用户触发了「深度研究」模式。你的工作流程：\n\n"
        "### 第一步：澄清需求\n"
        "在创建任务前，必须先与用户确认以下要点：\n"
        "1. 研究主题和具体方向\n"
        "2. 期望的研究深度和范围\n"
        "3. 输出形式偏好（分析报告、数据对比、趋势总结等）\n"
        "4. 是否有时间范围或特定关注点\n\n"
        "用简洁的问题引导用户，不要一次问太多。如果用户的描述已经足够清晰，"
        "可以直接确认理解并进入下一步。\n\n"
        "### 第二步：确认并创建任务\n"
        "与用户达成一致后，调用 `create_task` 工具创建后台深度研究任务。\n"
        "- task_type: deep_research\n"
        "- task_description: 你加工整理后的完整研究描述（非用户原始输入）\n\n"
        "### 第三步：告知用户\n"
        "任务创建成功后，告知用户：\n"
        "- 任务已提交到后台执行\n"
        "- 预计需要几分钟完成\n"
        "- 完成后会通过通知提醒\n\n"
        "**重要**：未经用户确认，不得直接创建任务。"
    ),
}
