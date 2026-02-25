"""
TodoWriteTool — 创建/更新 Todo 列表（Layer 2 感知层工具）

opencode 对标：TodoWriteTool in packages/opencode/src/tool/todo.ts
执行逻辑：接收完整 todos 列表 → 覆盖写入 Redis → 返回状态快照
LLM 读到快照后立即感知到最新状态（感知层核心）。
"""

import json

from pydantic import BaseModel, Field

from app.execution.session_context import get_session_id
from app.todo.schemas import TodoItem
from app.todo.store import TodoStore
from app.tools.base import BaseTool, ToolResult


class _Params(BaseModel):
    todos: list[TodoItem] = Field(description="更新后的完整 Todo 列表（全量替换，非增量）")


class TodoWriteTool(BaseTool):
    """创建/更新会话 Todo 列表，tier=L3（不暴露给 L1 FastTrack）"""

    @property
    def name(self) -> str:
        return "todo_write"

    @property
    def tier(self) -> list[str]:
        return ["L3"]

    @property
    def description(self) -> str:
        return (
            "创建或更新当前会话的 Todo 任务列表。接收完整列表并全量替换。\n"
            "必须在以下时机调用：\n"
            "1. 开始多步骤任务前：创建所有待办项（status: pending）\n"
            "2. 开始执行某一步时：立即标记为 in_progress（同时只能有一个）\n"
            "3. 完成某一步后：**立即**标记为 completed，禁止批量延迟更新\n"
            "参数：todos — 完整的任务列表（id, content, status, priority）"
        )

    @property
    def params_model(self) -> type[BaseModel]:
        return _Params

    async def execute(self, args: dict) -> ToolResult:
        session_id = get_session_id()
        todos_raw = args.get("todos", [])

        # 标准化：统一转为 dict（兼容 dict 和 TodoItem 两种入参形式）
        # id 强转字符串：LLM 有时传整数（1, 2, 3），TodoItem.id 要求 str
        todos: list[dict] = []
        for t in todos_raw:
            if isinstance(t, dict):
                if "id" in t:
                    t = {**t, "id": str(t["id"])}
                todos.append(TodoItem(**t).model_dump())
            else:
                todos.append(t.model_dump())

        await TodoStore.set(session_id, todos)

        active_count = sum(
            1 for t in todos
            if t.get("status") not in ("completed", "cancelled")
        )
        return ToolResult.success(
            title=f"{active_count} 个进行中",
            todos=todos,
            snapshot=json.dumps(todos, ensure_ascii=False, indent=2),
        )
