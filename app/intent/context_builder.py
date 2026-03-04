"""
Context Builder：加载对话历史并构造 history_messages

精简后仅保留历史加载 + history_messages 构造能力，
compaction 节点注入由 ConversationHistory.to_llm_messages() 统一处理。
"""

import structlog

from app.memory.working_memory import WorkingMemory

log = structlog.get_logger()


class ContextBuilder:
    """加载对话历史并构造 history_messages"""

    def __init__(self, memory: WorkingMemory):
        self.memory = memory

    async def load_history_messages(self, session_id: str) -> list[dict]:
        """
        加载对话历史并转为 LLM messages 格式。
        - user/assistant → 原样保留
        - is_compaction=True → 转为 role=user + 摘要框架
        """
        history = await self.memory.get_history(session_id)
        return history.to_llm_messages()
