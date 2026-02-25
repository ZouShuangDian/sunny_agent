"""
Session ID ContextVar

与 budget_context.py / agent_context.py 模式完全一致：
- ContextVar 保证 asyncio 并发隔离
- set_session_id 返回 Token，finally 块用 reset_session_id 精确还原
- SubAgent（execute_raw）不调用 set_session_id，默认值为空字符串，
  TodoStore 检测到空 session_id 时直接跳过，不污染主 Agent 的 todo 状态
"""

from contextvars import ContextVar, Token

_session_var: ContextVar[str] = ContextVar("session_id", default="")


def get_session_id() -> str:
    """读取当前 async 上下文的 session_id"""
    return _session_var.get()


def set_session_id(sid: str) -> Token:
    """设置 session_id，返回还原用的 Token"""
    return _session_var.set(sid)


def reset_session_id(token: Token) -> None:
    """精确还原到设置前的值（与 Token 配套使用）"""
    _session_var.reset(token)
