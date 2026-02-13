"""
工具注册中心：统一管理所有工具的注册、Schema 获取和执行分发
"""

import json

import structlog

from app.tools.base import BaseTool, ToolResult

log = structlog.get_logger()


class ToolRegistry:
    """工具注册中心"""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """注册一个工具实例"""
        self._tools[tool.name] = tool
        log.debug("工具已注册", tool=tool.name)

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    def get_all_schemas(self) -> list[dict]:
        """获取所有已注册工具的 OpenAI function calling schema"""
        return [tool.schema() for tool in self._tools.values()]

    def get_schemas(self, allowed_tools: list[str]) -> list[dict]:
        """获取指定工具的 schema（按需过滤）"""
        return [
            self._tools[name].schema()
            for name in allowed_tools
            if name in self._tools
        ]

    async def execute(self, name: str, arguments: dict) -> str:
        """
        执行工具，返回 JSON 字符串结果。

        始终返回标准化的 ToolResult JSON：
        - 成功: {"status": "success", ...data}
        - 失败: {"status": "error", "error": "..."}
        """
        tool = self._tools.get(name)
        if not tool:
            return ToolResult.fail(f"未知工具: {name}").to_json()

        try:
            result = await tool.execute(arguments)
            return result.to_json()
        except Exception as e:
            log.error("工具执行异常", tool=name, error=str(e), exc_info=True)
            return ToolResult.fail(f"工具执行异常: {e}").to_json()

    @property
    def tool_names(self) -> list[str]:
        """获取所有已注册工具名称"""
        return list(self._tools.keys())
