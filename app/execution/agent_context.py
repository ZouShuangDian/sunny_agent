"""
Agent 深度上下文：通过 contextvars 在 async 调用链中透传当前 Agent 嵌套深度。

设计说明：
- 与 budget_context.py 同一范式（ContextVar）
- react_engine 初始深度为 0，SubAgentCallTool 写入 depth+1 后启动子 Agent
- 使用 Token.reset() 模式：即使子 Agent 异常，finally 块也能精确还原到调用前的值
- 防递归爆炸：SubAgentCallTool 检查 current_depth >= config.max_depth 时拒绝执行

使用方式：
    # 读取（SubAgentCallTool）
    current_depth = get_agent_depth()          # 主 Agent 为 0

    # 写入（SubAgentCallTool，启动子 Agent 前）
    token = set_agent_depth(current_depth + 1)
    try:
        sub_result = await sub_engine.execute_raw(messages)
    finally:
        reset_agent_depth(token)               # 精确还原，不受嵌套影响
"""

from contextvars import ContextVar, Token

_depth_var: ContextVar[int] = ContextVar("agent_depth", default=0)


def get_agent_depth() -> int:
    """返回当前 async 上下文中的 Agent 嵌套深度（主 Agent=0）"""
    return _depth_var.get()


def set_agent_depth(depth: int) -> Token:
    """
    设置当前 Agent 深度，返回 Token 用于精确还原。

    调用方必须在 finally 中调用 reset_agent_depth(token)。
    """
    return _depth_var.set(depth)


def reset_agent_depth(token: Token) -> None:
    """还原到 set_agent_depth() 调用之前的深度值"""
    _depth_var.reset(token)
