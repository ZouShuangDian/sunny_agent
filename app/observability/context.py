"""
链路追踪上下文：通过 contextvars 在协程间自动传播 trace_id / user_id
"""

import contextvars
import uuid

# ── 全局上下文变量 ──
trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")
user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default="anonymous")


def new_trace_id() -> str:
    """生成新的 trace_id"""
    return str(uuid.uuid4())


def get_trace_id() -> str:
    return trace_id_var.get()


def get_user_id() -> str:
    return user_id_var.get()
