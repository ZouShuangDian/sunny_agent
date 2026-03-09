"""
Skill Catalog ContextVar

每次请求时由 ExecutionRouter 设置当前用户可用的 Skill 列表，
SkillCallTool 在 schema()/execute() 中读取，无需持有 SkillRegistry 引用。

与 session_context.py / budget_context.py 完全一致的 ContextVar 模式：
- ContextVar 保证 asyncio 并发隔离
- set_skill_catalog 返回 Token，finally 块用 reset_skill_catalog 精确还原
- SubAgent（run(LoopContext.from_messages(...))）不调用 set_skill_catalog，默认值为空列表，
  SkillCallTool 会返回"无可用 Skill"提示，不影响主 Agent
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.skills.service import SkillInfo

_skill_catalog_var: ContextVar[list["SkillInfo"]] = ContextVar(
    "skill_catalog", default=[]
)


def get_skill_catalog() -> list["SkillInfo"]:
    """读取当前 async 上下文的 Skill 列表"""
    return _skill_catalog_var.get()


def set_skill_catalog(catalog: list["SkillInfo"]) -> Token:
    """设置 Skill 列表，返回还原用的 Token"""
    return _skill_catalog_var.set(catalog)


def reset_skill_catalog(token: Token) -> None:
    """精确还原到设置前的值（与 Token 配套使用）"""
    _skill_catalog_var.reset(token)
