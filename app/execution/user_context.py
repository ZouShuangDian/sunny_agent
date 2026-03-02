"""
User ID ContextVar

与 session_context.py / budget_context.py / agent_context.py 模式完全一致：
- ContextVar 保证 asyncio 并发隔离
- set_user_id 返回 Token，finally 块用 reset_user_id 精确还原
- 值为用户工号（usernumb），用于沙箱文件路径隔离（/mnt/users/{usernumb}/）
"""

from contextvars import ContextVar, Token

_user_var: ContextVar[str] = ContextVar("user_id", default="")


def get_user_id() -> str:
    """读取当前 async 上下文的用户工号"""
    return _user_var.get()


def set_user_id(uid: str) -> Token:
    """设置用户工号，返回还原用的 Token"""
    return _user_var.set(uid)


def reset_user_id(token: Token) -> None:
    """精确还原到设置前的值（与 Token 配套使用）"""
    _user_var.reset(token)
