"""
工具抽象基类 + 标准化结果

BaseTool 强制约束：
1. name / description / params_model — 定义工具 Schema（Pydantic 生成，杜绝手写 dict 出错）
2. execute — 返回 ToolResult（标准化 status + data，L3 推理可依赖结构化信息）

ToolResult 标准化：
- status: "success" | "error"
- data: 工具特定的结果数据
- error: 错误描述（仅 status="error" 时有值）
"""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


@dataclass
class ToolResult:
    """工具执行标准化结果"""

    status: str  # "success" | "error"
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_json(self) -> str:
        """序列化为 JSON 字符串（给 LLM 作为 tool result）"""
        if self.status == "error":
            return json.dumps(
                {"status": "error", "error": self.error},
                ensure_ascii=False,
            )
        return json.dumps(
            {"status": "success", **self.data},
            ensure_ascii=False,
        )

    @classmethod
    def success(cls, **data: Any) -> "ToolResult":
        """快捷构造成功结果"""
        return cls(status="success", data=data)

    @classmethod
    def fail(cls, error: str) -> "ToolResult":
        """快捷构造失败结果"""
        return cls(status="error", error=error)


class BaseTool(ABC):
    """工具抽象基类，所有工具必须继承"""

    @property
    @abstractmethod
    def name(self) -> str:
        """工具唯一名称"""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述（给 LLM 看）"""
        ...

    @property
    @abstractmethod
    def params_model(self) -> type[BaseModel]:
        """参数 Pydantic Model，用于自动生成 JSON Schema"""
        ...

    @abstractmethod
    async def execute(self, args: dict) -> ToolResult:
        """执行工具，返回标准化结果"""
        ...

    def schema(self) -> dict:
        """生成 OpenAI function calling 格式的 tool schema"""
        json_schema = self.params_model.model_json_schema()

        # 提取 required 字段
        required = json_schema.get("required", [])

        # 提取 properties，移除 Pydantic 附加的 title 字段
        properties = {}
        for key, prop in json_schema.get("properties", {}).items():
            clean_prop = {k: v for k, v in prop.items() if k != "title"}
            properties[key] = clean_prop

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }
