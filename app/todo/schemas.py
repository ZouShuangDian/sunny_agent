"""
Todo 数据模型

TodoItem 对应 opencode 的 Todo.Info shape，
status 枚举与 opencode todowrite.txt 规范保持一致。
"""

from typing import Literal

from pydantic import BaseModel, field_validator


class TodoItem(BaseModel):
    """单个 Todo 条目"""

    id: str
    content: str
    status: Literal["pending", "in_progress", "completed", "cancelled"]
    priority: Literal["high", "medium", "low"] = "medium"

    @field_validator("id", mode="before")
    @classmethod
    def coerce_id_to_str(cls, v: object) -> str:
        """LLM 有时传整数 id（1, 2, 3），统一转为字符串"""
        return str(v)
