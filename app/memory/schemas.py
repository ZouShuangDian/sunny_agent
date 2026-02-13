"""
工作记忆数据结构定义

所有存入 Redis Hash 的数据都必须有对应的 Pydantic 模型。
写入时 model_dump_json()，读取时 model_validate_json()。
"""

import json

from pydantic import BaseModel, Field
from typing import Literal


# ── 工具调用记录（Phase 2+ 使用） ──


class ToolCall(BaseModel):
    """
    一次工具调用的完整记录。
    Phase 2 的 L1 Fast Track 会生成 SQL 并执行，每次执行就是一个 ToolCall。
    """

    tool_call_id: str  # 工具调用唯一ID（关联 tool 消息用）
    tool_name: str  # 工具名称，如 "sql_query", "api_call"
    arguments: dict  # 调用参数，如 {"sql": "SELECT ..."}
    result: str | None = None  # 工具返回结果（成功时填充）
    error: str | None = None  # 错误信息（失败时填充）
    status: Literal["success", "error", "timeout"] = "success"
    duration_ms: int | None = None  # 执行耗时


# ── 子 Agent 调用记录（Phase 3+ 使用） ──


class SubAgentCall(BaseModel):
    """
    子 Agent 委托记录。
    Phase 3+ 的 Deep Engine 可能拆解复杂任务为多个子任务。
    """

    agent_call_id: str  # 子 Agent 调用唯一ID
    agent_name: str  # 子 Agent 名称，如 "sql_agent", "analysis_agent"
    task: str  # 委托的任务描述
    result: str | None = None  # 子 Agent 返回结果
    status: Literal["success", "error", "timeout"] = "success"
    duration_ms: int | None = None  # 执行耗时
    model: str | None = None  # 子 Agent 使用的模型


# ── 对话消息 ──


class Message(BaseModel):
    """
    单条对话消息——对话历史的最小单元。

    role 说明：
    - "user":      用户输入
    - "assistant":  Agent 回复（可能包含 tool_calls 或 sub_agent_calls）
    - "system":     系统指令（不参与滚动淘汰）
    - "tool":       工具执行结果（通过 tool_call_id 关联到对应的 assistant 消息）

    一轮完整交互可能包含多条 Message：
    user → assistant(tool_calls) → tool(result) → assistant(最终回复)
    """

    role: Literal["user", "assistant", "system", "tool"]
    content: str  # 消息内容
    timestamp: float  # Unix 时间戳
    message_id: str | None = None  # 消息唯一ID（UUID），追溯用

    # ── 通用标注 ──
    token_count: int | None = None  # 该消息的 token 数（上下文窗口管理）

    # ── assistant 消息专属 ──
    intent_primary: str | None = None  # 该轮识别的主意图
    route: str | None = None  # 该轮路由结果
    model: str | None = None  # 使用的 LLM 模型
    tool_calls: list[ToolCall] | None = None  # 触发的工具调用列表
    sub_agent_calls: list[SubAgentCall] | None = None  # 触发的子 Agent 调用

    # ── tool 消息专属 ──
    tool_call_id: str | None = None  # 关联到哪个 ToolCall
    tool_name: str | None = None  # 工具名称（冗余，方便日志/调试）


class ConversationHistory(BaseModel):
    """完整对话历史"""

    messages: list[Message] = Field(default_factory=list)
    max_turns: int = 20  # 最大保留轮次
    total_tokens: int = 0  # 当前历史总 token 数（近似值）

    def append(self, msg: Message) -> None:
        """追加消息，超出 max_turns 时滚动淘汰最早的一轮"""
        self.messages.append(msg)
        if msg.token_count:
            self.total_tokens += msg.token_count
        # 滚动淘汰：以 user 消息数作为"轮次"计数
        user_msgs = [m for m in self.messages if m.role == "user"]
        if len(user_msgs) > self.max_turns:
            oldest_user = user_msgs[0]
            oldest_idx = self.messages.index(oldest_user)
            # 找到下一轮 user 消息的位置（即本轮结束位置）
            next_user_idx = (
                self.messages.index(user_msgs[1])
                if len(user_msgs) > 1
                else len(self.messages)
            )
            # 整轮删除 [oldest_idx, next_user_idx)
            to_remove = self.messages[oldest_idx:next_user_idx]
            for m in to_remove:
                if m.token_count:
                    self.total_tokens -= m.token_count
            del self.messages[oldest_idx:next_user_idx]

    def to_llm_messages(self) -> list[dict]:
        """
        转为 LLM 调用所需的 messages 格式。
        - user/assistant/system → {"role": ..., "content": ...}
        - tool → {"role": "tool", "content": ..., "tool_call_id": ...}
        - assistant 带 tool_calls → 附加 tool_calls 字段
        """
        result = []
        for m in self.messages:
            entry: dict = {"role": m.role, "content": m.content}
            if m.role == "assistant" and m.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.tool_call_id,
                        "type": "function",
                        "function": {
                            "name": tc.tool_name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in m.tool_calls
                ]
            if m.role == "tool" and m.tool_call_id:
                entry["tool_call_id"] = m.tool_call_id
            result.append(entry)
        return result


# ── 上一轮意图快照 ──


class LastIntent(BaseModel):
    """上一轮意图结果快照，用于指代消解和连续追问判断"""

    primary: str  # 主意图
    sub_intent: str | None = None  # 子意图
    route: str  # 路由
    complexity: str  # 复杂度
    confidence: float  # 置信度
    needs_clarify: bool = False  # 是否在追问中
    clarify_question: str | None = None  # 追问话术


# ── 会话元数据 ──


class SessionMeta(BaseModel):
    """会话级元数据"""

    session_id: str  # 会话ID
    user_id: str  # 用户ID
    usernumb: str  # 人员工号
    turn_count: int = 0  # 当前对话轮次
    created_at: float  # 会话创建时间（Unix 时间戳）
    last_active_at: float  # 最后活跃时间
