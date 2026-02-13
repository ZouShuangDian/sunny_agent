"""
M03-3 Context Builder：按需组装 LLM 调用所需的上下文

所有用户输入统一走 IntentEngine，ContextBuilder 仅加载对话历史 + 用户信息。
码表实体解析已下沉至 Sub-Agent / Tool 层按需执行。
"""

from dataclasses import dataclass, field

from app.memory.schemas import ConversationHistory, LastIntent
from app.memory.working_memory import WorkingMemory
from app.security.auth import AuthenticatedUser


@dataclass
class AssembledContext:
    """组装完成的 LLM 上下文"""

    system_prompt: str  # 系统角色描述
    last_intent: LastIntent | None = None  # 上一轮意图
    history: ConversationHistory = field(default_factory=ConversationHistory)  # 对话历史
    user_profile: dict = field(default_factory=dict)  # 用户信息
    metadata: dict = field(default_factory=dict)  # 扩展信息


# ── 系统 Prompt 模板 ──

SYSTEM_PROMPT_TEMPLATE = """你是 Agent Sunny，一个制造业智能助手。你的任务是理解用户意图，提取关键信息，并给出结构化的分析结果。

当前用户: {usernumb}，姓名: {username}，角色: {role}，部门: {department}
数据权限范围: {data_scope}

{history_section}
请分析用户的意图，严格按照指定的 JSON 格式输出结果。"""


class ContextBuilder:
    """按需组装 LLM 上下文（无状态，仅依赖 memory + 用户信息）"""

    def __init__(self, memory: WorkingMemory):
        self.memory = memory

    async def build(
        self,
        user_input: str,
        user: AuthenticatedUser,
        session_id: str,
    ) -> AssembledContext:
        """组装 LLM 调用所需的完整上下文"""
        # 1. 加载工作记忆
        history = await self.memory.get_history(session_id)
        last_intent = await self.memory.get_last_intent(session_id)

        # 2. 构造用户信息
        user_profile = {
            "usernumb": user.usernumb,
            "username": user.username,
            "role": user.role,
            "department": user.department,
            "data_scope": user.data_scope,
        }

        # 3. 组装系统 Prompt
        history_section = self._format_history(history)

        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            usernumb=user.usernumb,
            username=user.username,
            role=user.role,
            department=user.department or "未指定",
            data_scope=user.data_scope or "全部",
            history_section=history_section,
        )

        return AssembledContext(
            system_prompt=system_prompt,
            last_intent=last_intent,
            history=history,
            user_profile=user_profile,
        )

    def _format_history(self, history: ConversationHistory) -> str:
        """格式化对话历史（只取最近几轮）"""
        recent = [
            m for m in history.messages
            if m.role in ("user", "assistant")
        ][-10:]  # 最多展示最近 5 轮（10 条消息）

        if not recent:
            return ""

        lines = ["对话历史:"]
        for m in recent:
            role_label = "用户" if m.role == "user" else "助手"
            lines.append(f"  {role_label}: {m.content}")

        return "\n".join(lines)
