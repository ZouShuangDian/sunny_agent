"""
TodoReadTool — 读取 Todo 列表（Layer 2 感知层工具）

opencode 对标：TodoReadTool in packages/opencode/src/tool/todo.ts
执行逻辑：从 Redis 读取当前会话 Todo → 返回完整快照
LLM 通过此工具主动刷新对任务进度的感知。
"""

import json

from pydantic import BaseModel

from app.execution.session_context import get_session_id
from app.todo.store import TodoStore
from app.tools.base import BaseTool, ToolResult


class _EmptyParams(BaseModel):
    """无参数"""


class TodoReadTool(BaseTool):
    """读取会话 Todo 列表，tier=L3（不暴露给 L1 FastTrack）"""

    @property
    def name(self) -> str:
        return "todo_read"

    @property
    def tier(self) -> list[str]:
        return ["L3"]

    @property
    def description(self) -> str:
        return (
            "读取当前会话的 Todo 任务列表。应主动频繁调用，尤其是：\n"
            "- 对话开始时，查看是否有未完成的待办\n"
            "- 开始新任务前，确认当前优先级\n"
            "- 不确定下一步时，通过列表决策\n"
            "- 每隔几条消息后，确认整体进度\n"
            "此工具无需任何参数，留空即可。"
        )

    @property
    def params_model(self) -> type[BaseModel]:
        return _EmptyParams

    async def execute(self, args: dict) -> ToolResult:
        session_id = get_session_id()
        todos = await TodoStore.get(session_id)
        in_progress = sum(1 for t in todos if t.get("status") == "in_progress")
        pending = sum(1 for t in todos if t.get("status") == "pending")
        completed = sum(1 for t in todos if t.get("status") == "completed")
        title = f"进行中 {in_progress}，待处理 {pending}，已完成 {completed}（共 {len(todos)} 项）"

        return ToolResult.success(
            title=title,
            todos=todos,
            snapshot=json.dumps(todos, ensure_ascii=False, indent=2),
        )
